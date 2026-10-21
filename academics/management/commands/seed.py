from datetime import date, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from academics.models import (
    AcademicYear,
    Activity,
    Announcement,
    Attendance,
    ClassTeacher,
    Course,
    Enrollment,
    ExamSitting,
    GradeBand,
    Guardian,
    Programme,
    SchoolClass,
    SchoolSettings,
    Stream,
    Student,
    Subject,
    Term,
    TimetableEntry,
)
from accounts.models import User
from finance.models import Invoice, Payment, Waiver


class Command(BaseCommand):
    help = "Create demo users, academic structure, students, fees and timetables"

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["school", "college"], default=None,
            help="Switch EduSphere to school mode or college mode after seeding.")

    def handle(self, *a, **o):
        if o.get("mode"):
            s = SchoolSettings.get()
            s.mode = o["mode"]
            s.save()

        def mk(username, first, last, role, **extra):
            return User.objects.get_or_create(
                username=username,
                defaults=dict(first_name=first, last_name=last, role=role, **extra),
            )[0]

        admin = mk("admin", "System", "Admin", "admin", is_staff=True, is_superuser=True)
        reg, bur, lec = mk("registrar", "Rudo", "Moyo", "registrar"), \
            mk("bursar", "Tendai", "Chuma", "bursar"), \
            mk("lecturer", "Dr. Farai", "Ncube", "lecturer")
        for u in (admin, reg, bur, lec):
            u.set_password("demo1234")
            u.save()

        def programme(code, name, level, fee, months):
            return Programme.objects.get_or_create(
                code=code,
                defaults=dict(name=name, level=level, tuition_fee=fee, duration_months=months),
            )[0]

        dip = programme("DIT", "Diploma in Information Technology", "diploma", 900, 24)
        programme("SC-WEB", "Web Development (Short Course)", "short", 250, 3)
        pri = programme("PRI", "Primary School", "primary", 150, 12)
        sec = programme("SEC", "Secondary School (O-Level)", "secondary", 300, 12)
        programme("BSC", "BSc Computer Science", "degree", 2400, 48)
        programme("MSC", "MSc Data Science", "masters", 3800, 24)
        c1 = Course.objects.get_or_create(code="IT101", defaults=dict(
            title="Programming Fundamentals", programme=dip, lecturer=lec))[0]
        Course.objects.get_or_create(code="IT102", defaults=dict(
            title="Networking Basics", programme=dip, lecturer=lec))

        for letter, mark, pts in [("A", 75, 4), ("B", 65, 3), ("C", 50, 2),
                                  ("D", 40, 1), ("F", 0, 0)]:
            GradeBand.objects.get_or_create(letter=letter, defaults=dict(
                min_mark=mark, points=pts))

        # ----- academic structure -----
        year_num = date.today().year
        year, _ = AcademicYear.objects.get_or_create(name=str(year_num), defaults=dict(
            start_date=date(year_num, 1, 1), end_date=date(year_num, 12, 31),
            is_current=not AcademicYear.objects.filter(is_current=True).exists()))
        if not AcademicYear.objects.filter(is_current=True).exists():
            year.is_current = True
            year.save()
        term_spans = [("Term 1", (1, 12), (4, 10)), ("Term 2", (5, 4), (8, 28)),
                      ("Term 3", (9, 14), (12, 4))]
        terms = {}
        for i, (name, start, end) in enumerate(term_spans):
            t, _ = Term.objects.get_or_create(academic_year=year, name=name, defaults=dict(
                start_date=date(year_num, *start), end_date=date(year_num, *end),
                is_current=(i == 0 and not Term.objects.filter(is_current=True).exists())))
            terms[name] = t
        term1 = terms["Term 1"]

        classes = {}
        for name, level in [("Grade 1", "primary"), ("Grade 3", "primary"),
                            ("Grade 7", "primary"), ("Form 1", "secondary"),
                            ("Form 2", "secondary"), ("Form 4", "secondary")]:
            cls, _ = SchoolClass.objects.get_or_create(name=name, defaults=dict(level=level))
            classes[name] = cls
            for s in ("A", "B"):
                Stream.objects.get_or_create(school_class=cls, name=s)

        def subject(code, name, level):
            return Subject.objects.get_or_create(
                code=code, defaults=dict(name=name, level=level, lecturer=lec))[0]

        mth1 = subject("MTH1", "Mathematics", "primary")
        eng1 = subject("ENG1", "English", "primary")
        subject("SCI1", "Science", "primary")
        subject("SHO1", "Shona", "primary")
        mth2 = subject("MTH2", "Mathematics", "secondary")
        eng2 = subject("ENG2", "English", "secondary")
        subject("BIO2", "Biology", "secondary")
        subject("GEO2", "Geography", "secondary")
        for s in Subject.objects.filter(lecturer__isnull=True):
            s.lecturer = lec
            s.save(update_fields=["lecturer"])

        for cls_name in ("Grade 3", "Form 2"):
            ClassTeacher.objects.get_or_create(
                school_class=classes[cls_name], academic_year=year,
                defaults={"teacher": lec})

        lessons = [
            dict(term=term1, day=1, start_time=time(8, 0), end_time=time(8, 45),
                 subject=mth1, school_class=classes["Grade 3"], lecturer=lec, venue="Room 4"),
            dict(term=term1, day=1, start_time=time(8, 50), end_time=time(9, 35),
                 subject=eng1, school_class=classes["Grade 3"], lecturer=lec, venue="Room 4"),
            dict(term=term1, day=2, start_time=time(8, 0), end_time=time(8, 45),
                 subject=mth1, school_class=classes["Form 2"], lecturer=lec, venue="Room 7"),
            dict(term=term1, day=3, start_time=time(9, 0), end_time=time(10, 30),
                 course=c1, lecturer=lec, venue="Lab 1"),
        ]
        for e in lessons:
            lookup = {k: v for k, v in e.items()
                      if k in ("term", "day", "start_time", "school_class", "course")}
            TimetableEntry.objects.get_or_create(
                **lookup, defaults={**e, "academic_year": year})

        # ----- students -----
        college_students = [("student1", "Tariro", "Dube", dip, 900),
                            ("student2", "Blessing", "Sibanda", dip, 400),
                            ("student3", "Chipo", "Mutasa", dip, 0)]
        for i, (username, first, last, programme, paid) in enumerate(college_students, 1):
            if User.objects.filter(username=username).exists():
                continue
            u = User.objects.create_user(username, password="demo1234", first_name=first,
                                         last_name=last, role="student")
            s = Student.objects.create(
                user=u, programme=programme,
                guardian_name="Parent " + last, guardian_phone=f"+263 77 000 000{i}")
            inv = Invoice.objects.create(student=s, description=f"Tuition – {programme.name}",
                                         amount=programme.tuition_fee,
                                         due_date=date.today() + timedelta(days=30),
                                         created_by=bur)
            if paid:
                Payment.objects.create(invoice=inv, amount=paid, received_by=bur)
            for c in Course.objects.filter(programme=programme):
                Enrollment.objects.create(student=s, course=c)
            Activity.objects.create(student=s, kind="club", title="Robotics Club",
                                    date=date.today())

        school_students = [("student4", "Rutendo", "Chikowore", pri, "Grade 3", "A", mth1, eng1),
                           ("student5", "Tafadzwa", "Mhlanga", sec, "Form 2", "B", mth2, eng2)]
        for i, (username, first, last, programme, class_name, stream_name, *subjects) in \
                enumerate(school_students, 4):
            if User.objects.filter(username=username).exists():
                continue
            cls = classes[class_name]
            stream = Stream.objects.get(school_class=cls, name=stream_name)
            u = User.objects.create_user(username, password="demo1234", first_name=first,
                                         last_name=last, role="student")
            s = Student.objects.create(
                user=u, programme=programme, school_class=cls, stream=stream,
                guardian_name="Parent " + last, guardian_phone=f"+263 77 000 000{i}")
            inv = Invoice.objects.create(student=s, description=f"Tuition – {programme.name}",
                                         amount=programme.tuition_fee,
                                         due_date=date.today() + timedelta(days=30),
                                         created_by=bur)
            if username == "student4":
                Payment.objects.create(invoice=inv, amount=50, received_by=bur)
            for subject in subjects:
                Enrollment.objects.create(student=s, subject=subject, term=term1.name)

        inv5 = Invoice.objects.filter(student__user__username="student5").first()
        if inv5 and not inv5.waivers.exists():
            Waiver.objects.create(invoice=inv5, amount=Decimal("100.00"),
                                  reason="Bursary contribution still pending",
                                  recorded_by=bur)

        reg_user = User.objects.get(username="registrar")
        Announcement.objects.get_or_create(
            title="Term opens Monday",
            defaults=dict(body="Fees must be cleared before exams.", audience="all",
                          posted_by=reg_user))
        Announcement.objects.get_or_create(
            title="Grade 3 sports day Friday",
            defaults=dict(body="Send a hat and a water bottle.", audience="student",
                          school_class=classes["Grade 3"], posted_by=reg_user))
        Announcement.objects.get_or_create(
            title="Parents: fee slips are out",
            defaults=dict(body="Please settle term fees before the 30th.",
                          audience="parent", posted_by=reg_user))

        parent = mk("parent", "Perpetua", "Chikowore", "parent")
        parent.set_password("demo1234")
        parent.save()
        for s in Student.objects.filter(user__username__in=("student4", "student5")):
            Guardian.objects.get_or_create(parent=parent, student=s)

        demo_marks = [("student1", "IT101", "72"), ("student1", "IT102", "58"),
                      ("student2", "IT101", "45"), ("student4", "MTH1", "81"),
                      ("student4", "ENG1", "66"), ("student5", "MTH2", "54")]
        for username, code, mark in demo_marks:
            e = Enrollment.objects.filter(student__user__username=username).filter(
                Q(course__code=code) | Q(subject__code=code)).first()
            if e and e.mark is None:
                e.mark = Decimal(mark)
                e.save(update_fields=["mark"])

        demo_attendance = [("student1", "IT101", [True, True, False]),
                           ("student4", "MTH1", [True, True, True])]
        for username, code, pattern in demo_attendance:
            e = Enrollment.objects.filter(student__user__username=username).filter(
                Q(course__code=code) | Q(subject__code=code)).first()
            if e:
                for i, present in enumerate(pattern):
                    Attendance.objects.get_or_create(
                        enrollment=e, date=date.today() - timedelta(days=8 - i),
                        defaults={"present": present})

        ExamSitting.enter(term1, Student.objects.filter(status="active"))
        ExamSitting.objects.filter(term=term1).update(venue="Hall A")
        blocked = ExamSitting.objects.filter(
            term=term1, student__user__username="student2").first()
        if blocked:
            blocked.cleared = False
            blocked.note = "Fee balance outstanding"
            blocked.save(update_fields=["cleared", "note"])

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {SchoolSettings.get().get_mode_display()} demo data. "
            "Logins (password demo1234): admin, registrar, bursar, lecturer, "
            "parent, student1-5"))
