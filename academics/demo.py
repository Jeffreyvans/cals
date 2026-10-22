"""Demo data for one institution.

The same data is used by the ``seed`` management command and by the setup
wizard, so a new institution can be filled with realistic content in one
click. Everything is created inside the institution, so two institutions never
share a row.
"""

from datetime import date, time, timedelta
from decimal import Decimal

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
from core.tenancy import use_institution
from finance.models import Invoice, Payment, Waiver

DEMO_PASSWORD = "demo1234"

DEMO_LOGINS = ("admin", "registrar", "bursar", "lecturer", "parent",
               "student1", "student2", "student3", "student4", "student5")


def seed_demo(institution, prefix="", mode=None, set_password=True):
    """Create demo users, academic structure, students, fees and timetables.

    `prefix` is put in front of every username, so a second institution can
    have its own ``admin`` without colliding with the first one. When a name
    is already taken by another institution the demo falls back to a name of
    its own, so no account of another institution is ever touched.

    `set_password` is False when the caller manages the accounts itself
    (the setup wizard), so an existing password is never reset.
    """
    with use_institution(institution):
        settings_row = SchoolSettings.get(institution)
        if mode:
            settings_row.mode = mode
            settings_row.save(update_fields=["mode"])

        usernames = {}

        def mk(username, first, last, role, **extra):
            """Find or make one demo account. Returns (user, username, created)."""
            wanted = prefix + username
            user = User.objects.filter(username=wanted).first()
            if user is not None and user.institution_id != institution.pk:
                wanted = f"{wanted}-{institution.slug}"
                user = User.objects.filter(username=wanted).first()
            created = False
            if user is None:
                user = User.objects.create_user(
                    wanted, password=DEMO_PASSWORD, first_name=first,
                    last_name=last, role=role, institution=institution, **extra)
                created = True
            elif set_password and user.institution_id == institution.pk:
                user.set_password(DEMO_PASSWORD)
                user.save()
            usernames[username] = wanted
            return user, wanted, created

        mk("admin", "System", "Admin", "admin",
                         is_staff=True, is_superuser=True)
        reg, _, _ = mk("registrar", "Rudo", "Moyo", "registrar")
        bur, _, _ = mk("bursar", "Tendai", "Chuma", "bursar")
        lec, _, _ = mk("lecturer", "Dr. Farai", "Ncube", "lecturer")

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
            term, _ = Term.objects.get_or_create(academic_year=year, name=name, defaults=dict(
                start_date=date(year_num, *start), end_date=date(year_num, *end),
                is_current=(i == 0 and not Term.objects.filter(is_current=True).exists())))
            terms[name] = term
        term1 = terms["Term 1"]

        classes = {}
        for name, level in [("Grade 1", "primary"), ("Grade 3", "primary"),
                            ("Grade 7", "primary"), ("Form 1", "secondary"),
                            ("Form 2", "secondary"), ("Form 4", "secondary")]:
            school_class, _ = SchoolClass.objects.get_or_create(
                name=name, defaults=dict(level=level))
            classes[name] = school_class
            for stream_name in ("A", "B"):
                Stream.objects.get_or_create(school_class=school_class, name=stream_name)

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
        for subject_row in Subject.objects.filter(lecturer__isnull=True):
            subject_row.lecturer = lec
            subject_row.save(update_fields=["lecturer"])

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
                 subject=mth2, school_class=classes["Form 2"], lecturer=lec, venue="Room 7"),
            dict(term=term1, day=3, start_time=time(9, 0), end_time=time(10, 30),
                 course=c1, lecturer=lec, venue="Lab 1"),
        ]
        for lesson in lessons:
            lookup = {k: v for k, v in lesson.items()
                      if k in ("term", "day", "start_time", "school_class", "course")}
            TimetableEntry.objects.get_or_create(
                **lookup, defaults={**lesson, "academic_year": year})

        # ----- students -----
        college_students = [("student1", "Tariro", "Dube", dip, 900),
                            ("student2", "Blessing", "Sibanda", dip, 400),
                            ("student3", "Chipo", "Mutasa", dip, 0)]
        for i, (name, first, last, prog, paid) in enumerate(college_students, 1):
            user, _username, created = mk(name, first, last, "student")
            if not created:
                continue
            student = Student.objects.create(
                user=user, programme=prog,
                guardian_name="Parent " + last, guardian_phone=f"+263 77 000 000{i}")
            invoice = Invoice.objects.create(
                student=student, description=f"Tuition – {prog.name}",
                amount=prog.tuition_fee,
                due_date=date.today() + timedelta(days=30),
                created_by=bur)
            if paid:
                Payment.objects.create(invoice=invoice, amount=paid, received_by=bur)
            for course in Course.objects.filter(programme=prog):
                Enrollment.objects.create(student=student, course=course)
            Activity.objects.create(student=student, kind="club", title="Robotics Club",
                                    date=date.today())

        school_students = [("student4", "Rutendo", "Chikowore", pri, "Grade 3", "A", mth1, eng1),
                           ("student5", "Tafadzwa", "Mhlanga", sec, "Form 2", "B", mth2, eng2)]
        for i, (name, first, last, prog, class_name, stream_name, *subjects) in \
                enumerate(school_students, 4):
            user, _username, created = mk(name, first, last, "student")
            if not created:
                continue
            school_class = classes[class_name]
            stream = Stream.objects.get(school_class=school_class, name=stream_name)
            student = Student.objects.create(
                user=user, programme=prog, school_class=school_class, stream=stream,
                guardian_name="Parent " + last, guardian_phone=f"+263 77 000 000{i}")
            invoice = Invoice.objects.create(
                student=student, description=f"Tuition – {prog.name}",
                amount=prog.tuition_fee,
                due_date=date.today() + timedelta(days=30),
                created_by=bur)
            if name == "student4":
                Payment.objects.create(invoice=invoice, amount=50, received_by=bur)
            for subject_row in subjects:
                Enrollment.objects.create(student=student, subject=subject_row, term=term1.name)

        inv5 = Invoice.objects.filter(
            student__user__username=usernames.get("student5")).first()
        if inv5 and not inv5.waivers.exists():
            Waiver.objects.create(invoice=inv5, amount=Decimal("100.00"),
                                  reason="Bursary contribution still pending",
                                  recorded_by=bur)

        Announcement.objects.get_or_create(
            title="Term opens Monday",
            defaults=dict(body="Fees must be cleared before exams.", audience="all",
                          posted_by=reg))
        Announcement.objects.get_or_create(
            title="Grade 3 sports day Friday",
            defaults=dict(body="Send a hat and a water bottle.", audience="student",
                          school_class=classes["Grade 3"], posted_by=reg))
        Announcement.objects.get_or_create(
            title="Parents: fee slips are out",
            defaults=dict(body="Please settle term fees before the 30th.",
                          audience="parent", posted_by=reg))

        parent, _, _ = mk("parent", "Perpetua", "Chikowore", "parent")
        for student in Student.objects.filter(
                user__username__in=(usernames.get("student4"), usernames.get("student5"))):
            if student.user_id and student.user.username:
                Guardian.objects.get_or_create(parent=parent, student=student)

        demo_marks = [("student1", "IT101", "72"), ("student1", "IT102", "58"),
                      ("student2", "IT101", "45"), ("student4", "MTH1", "81"),
                      ("student4", "ENG1", "66"), ("student5", "MTH2", "54")]
        for name, code, mark in demo_marks:
            enrollment = Enrollment.objects.filter(
                student__user__username=usernames.get(name)).filter(
                Q(course__code=code) | Q(subject__code=code)).first()
            if enrollment and enrollment.mark is None:
                enrollment.mark = Decimal(mark)
                enrollment.save(update_fields=["mark"])

        demo_attendance = [("student1", "IT101", [True, True, False]),
                           ("student4", "MTH1", [True, True, True])]
        for name, code, pattern in demo_attendance:
            enrollment = Enrollment.objects.filter(
                student__user__username=usernames.get(name)).filter(
                Q(course__code=code) | Q(subject__code=code)).first()
            if enrollment:
                for i, present in enumerate(pattern):
                    Attendance.objects.get_or_create(
                        enrollment=enrollment, date=date.today() - timedelta(days=8 - i),
                        defaults={"present": present})

        ExamSitting.enter(term1, Student.objects.filter(status="active"))
        ExamSitting.objects.filter(term=term1).update(venue="Hall A")
        blocked = ExamSitting.objects.filter(
            term=term1, student__user__username=usernames.get("student2")).first()
        if blocked:
            blocked.cleared = False
            blocked.note = "Fee balance outstanding"
            blocked.save(update_fields=["cleared", "note"])

    return {
        "institution": institution,
        "users": [usernames.get(name, prefix + name) for name in DEMO_LOGINS],
    }
