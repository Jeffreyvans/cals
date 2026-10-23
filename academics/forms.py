from django import forms

from accounts.models import User

from .models import (
    AcademicYear,
    Activity,
    Announcement,
    ClassSubjectAllocation,
    ClassTeacher,
    Cohort,
    Course,
    CourseOffering,
    CoursePrerequisite,
    Enrollment,
    GradeBand,
    GradeLevel,
    Guardian,
    Programme,
    ProgrammeYear,
    SchoolClass,
    SchoolSettings,
    Stream,
    Student,
    Subject,
    Term,
    TimetableEntry,
)


class StudentRegistrationForm(forms.ModelForm):
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    username = forms.CharField(max_length=60, help_text="Student logs in with this")
    password = forms.CharField(widget=forms.PasswordInput, min_length=6)
    email = forms.EmailField(required=False)
    date_of_birth = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Student
        fields = ["programme", "programme_year", "cohort", "year_or_grade",
                  "school_class", "stream", "date_of_birth", "guardian_name",
                  "guardian_phone"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["stream"].required = False
        if SchoolSettings.get().is_school:
            self.fields.pop("programme_year")
            self.fields.pop("cohort")
            self.fields.pop("year_or_grade")
            self.fields["programme"].label = "Level"
            self.fields["school_class"].required = True
            self.fields["school_class"].empty_label = "Choose a class"
            self.fields["stream"].queryset = Stream.objects.select_related("school_class")
        else:
            self.fields.pop("school_class")
            self.fields.pop("stream")
            self.fields["programme_year"].queryset = ProgrammeYear.objects.select_related(
                "programme", "academic_year")
            self.fields["programme_year"].empty_label = "Choose a programme year"
            self.fields["programme_year"].required = False
            self.fields["cohort"].queryset = Cohort.objects.select_related(
                "programme_year__programme")
            self.fields["cohort"].empty_label = "No cohort yet"
            self.fields["cohort"].required = False

    def clean_username(self):
        u = self.cleaned_data["username"]
        if User.objects.filter(username=u).exists():
            raise forms.ValidationError("That username is taken. Choose another.")
        return u

    def clean(self):
        c = super().clean()
        if SchoolSettings.get().is_school:
            if not c.get("school_class"):
                raise forms.ValidationError("Choose the class for this student.")
        if c.get("stream") and c.get("school_class"):
            if c["stream"].school_class_id != c["school_class"].pk:
                raise forms.ValidationError("That stream belongs to another class.")
        programme, programme_year = c.get("programme"), c.get("programme_year")
        if programme and programme_year and programme_year.programme_id != programme.pk:
            raise forms.ValidationError("That programme year belongs to another programme.")
        cohort = c.get("cohort")
        if cohort and cohort.programme_year_id != getattr(programme_year, "pk", None):
            raise forms.ValidationError("That cohort belongs to another programme year.")
        return c


class EnrollForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ["course", "subject", "term"]

    def __init__(self, student=None, *a, **kw):
        super().__init__(*a, **kw)
        is_school = SchoolSettings.get().is_school
        if is_school:
            self.fields.pop("course")
            subjects = Subject.objects.exclude(level="college")
            if student and student.school_class_id:
                subjects = subjects.filter(level=student.school_class.level)
            self.fields["subject"].queryset = subjects
            self.fields["subject"].empty_label = "Choose a subject"
        else:
            self.fields.pop("subject")
            if student and student.programme_id:
                self.fields["course"].queryset = Course.objects.filter(programme=student.programme)
            self.fields["course"].empty_label = "Choose a course"
        terms = Term.objects.select_related("academic_year")
        self.fields["term"].queryset = terms
        self.fields["term"].empty_label = "Choose a term"
        self.fields["term"].required = True
        if not self.is_bound:
            current = terms.filter(is_current=True).first()
            if current:
                self.initial["term"] = current


class ActivityForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = ["kind", "title", "date", "detail"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class YearForm(forms.ModelForm):
    class Meta:
        model = AcademicYear
        fields = ["name", "start_date", "end_date"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}),
                   "end_date": forms.DateInput(attrs={"type": "date"})}


class TermForm(forms.ModelForm):
    class Meta:
        model = Term
        fields = ["academic_year", "name", "start_date", "end_date"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}),
                   "end_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["academic_year"].queryset = AcademicYear.objects.all()
        self.fields["academic_year"].empty_label = "Choose a year"
        if not self.is_bound:
            self.initial["academic_year"] = AcademicYear.objects.filter(is_current=True).first()


class SchoolClassForm(forms.ModelForm):
    class Meta:
        model = SchoolClass
        fields = ["name", "level"]


class StreamForm(forms.ModelForm):
    class Meta:
        model = Stream
        fields = ["name"]


class ClassTeacherForm(forms.ModelForm):
    class Meta:
        model = ClassTeacher
        fields = ["teacher"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["teacher"].queryset = User.tenant_objects.filter(
            role__in=("lecturer", "admin"), is_active=True).order_by("first_name", "username")
        self.fields["teacher"].empty_label = "Choose a teacher"


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ["code", "name", "level", "lecturer"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["lecturer"].queryset = User.tenant_objects.filter(
            role__in=("lecturer", "admin"), is_active=True).order_by("first_name", "username")
        self.fields["lecturer"].empty_label = "Choose the teacher"
        self.fields["lecturer"].required = False


class GradeLevelForm(forms.ModelForm):
    class Meta:
        model = GradeLevel
        fields = ["name", "kind", "order"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["order"].help_text = "The step on the way up. Higher numbers come later."
        self.fields["name"].help_text = "For example Grade 3 or Form 2."


class ClassSubjectAllocationForm(forms.ModelForm):
    class Meta:
        model = ClassSubjectAllocation
        fields = ["subject", "lecturer", "lessons_per_week"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["subject"].queryset = Subject.objects.exclude(level="college") \
            .order_by("code")
        self.fields["subject"].empty_label = "Choose a subject"
        self.fields["lecturer"].queryset = User.tenant_objects.filter(
            role__in=("lecturer", "admin"), is_active=True).order_by("first_name", "username")
        self.fields["lecturer"].empty_label = "Choose the teacher"
        self.fields["lecturer"].required = False
        self.fields["lessons_per_week"].required = False
        self.fields["lessons_per_week"].help_text = "How many lessons a week."


class ProgrammeForm(forms.ModelForm):
    class Meta:
        model = Programme
        fields = ["name", "code", "level", "duration_months", "tuition_fee"]


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["code", "title", "programme", "credits", "lecturer"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["programme"].empty_label = "Choose a programme"
        self.fields["lecturer"].queryset = User.tenant_objects.filter(
            role="lecturer", is_active=True).order_by("first_name", "username")
        self.fields["lecturer"].required = False


class ProgrammeYearForm(forms.ModelForm):
    class Meta:
        model = ProgrammeYear
        fields = ["programme", "academic_year", "name", "number", "capacity",
                  "credit_limit", "registration_opens", "registration_closes"]
        widgets = {
            "registration_opens": forms.DateInput(attrs={"type": "date"}),
            "registration_closes": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["programme"].empty_label = "Choose a programme"
        self.fields["academic_year"].empty_label = "Choose an academic year"
        self.fields["academic_year"].queryset = AcademicYear.objects.order_by("-name")
        self.fields["name"].help_text = "For example Year 1 or Part 2. Leave empty to use the number."
        self.fields["capacity"].help_text = "How many students this year can take. 0 means no limit."
        self.fields["credit_limit"].help_text = "The most credits one student may carry. 0 means no limit."

    def clean(self):
        c = super().clean()
        opens, closes = c.get("registration_opens"), c.get("registration_closes")
        if opens and closes and closes < opens:
            self.add_error("registration_closes",
                           "Registration must close after it opens.")
        return c


class CohortForm(forms.ModelForm):
    class Meta:
        model = Cohort
        fields = ["programme_year", "name", "lecturer"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["programme_year"].queryset = ProgrammeYear.objects.select_related(
            "programme", "academic_year")
        self.fields["programme_year"].empty_label = "Choose a programme year"
        self.fields["lecturer"].queryset = User.tenant_objects.filter(
            role__in=("lecturer", "admin"), is_active=True).order_by("first_name", "username")
        self.fields["lecturer"].empty_label = "Choose a lecturer"
        self.fields["lecturer"].required = False


class CourseOfferingForm(forms.ModelForm):
    class Meta:
        model = CourseOffering
        fields = ["course", "term", "lecturer", "capacity", "credits",
                  "enrolment_deadline"]
        widgets = {"enrolment_deadline": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["course"].queryset = Course.objects.select_related("programme")
        self.fields["course"].empty_label = "Choose a course"
        self.fields["term"].queryset = Term.objects.select_related("academic_year")
        self.fields["term"].empty_label = "Choose a term"
        self.fields["lecturer"].queryset = User.tenant_objects.filter(
            role__in=("lecturer", "admin"), is_active=True).order_by("first_name", "username")
        self.fields["lecturer"].empty_label = "Use the course lecturer"
        self.fields["lecturer"].required = False
        self.fields["credits"].help_text = "Leave empty to use the course credits."
        self.fields["capacity"].help_text = "0 means no limit."


class CoursePrerequisiteForm(forms.ModelForm):
    class Meta:
        model = CoursePrerequisite
        fields = ["course", "required", "min_mark"]
        widgets = {"min_mark": forms.NumberInput(attrs={"min": 0, "max": 100, "step": 0.01})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        courses = Course.objects.select_related("programme").order_by("code")
        self.fields["course"].queryset = courses
        self.fields["required"].queryset = courses
        self.fields["course"].empty_label = "Choose the later course"
        self.fields["required"].empty_label = "Choose the course it needs"
        self.fields["min_mark"].help_text = "Leave empty to accept any pass."

    def clean(self):
        c = super().clean()
        if c.get("course") and c.get("course") == c.get("required"):
            raise forms.ValidationError("A course cannot be its own prerequisite.")
        return c


class TimetableEntryForm(forms.ModelForm):
    class Meta:
        model = TimetableEntry
        fields = ["academic_year", "term", "day", "start_time", "end_time", "subject",
                  "course", "school_class", "stream", "lecturer", "venue"]
        widgets = {"start_time": forms.TimeInput(attrs={"type": "time"}),
                   "end_time": forms.TimeInput(attrs={"type": "time"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        is_school = SchoolSettings.get().is_school
        if is_school:
            self.fields.pop("course")
            self.fields["subject"].queryset = Subject.objects.exclude(level="college")
            self.fields["subject"].empty_label = "Choose a subject"
        else:
            self.fields.pop("subject")
            self.fields.pop("school_class")
            self.fields.pop("stream")
            self.fields["course"].queryset = Course.objects.select_related("programme")
            self.fields["course"].empty_label = "Choose a course"
        if "stream" in self.fields:
            self.fields["stream"].queryset = Stream.objects.select_related("school_class")
            self.fields["stream"].empty_label = "Whole class (no split)"
        self.fields["lecturer"].queryset = User.tenant_objects.filter(
            role__in=("lecturer", "admin"), is_active=True).order_by("first_name", "username")
        self.fields["lecturer"].empty_label = "Choose a teacher"
        self.fields["academic_year"].queryset = AcademicYear.objects.all()
        self.fields["academic_year"].empty_label = "Choose a year"
        self.fields["term"].queryset = Term.objects.all()
        self.fields["term"].empty_label = "Choose a term"
        if not self.is_bound:
            year = AcademicYear.objects.filter(is_current=True).first()
            term = Term.objects.filter(is_current=True).first()
            if year:
                self.initial["academic_year"] = year
            if term:
                self.initial["term"] = term
        if self.is_bound and self.data.get("school_class") and "stream" in self.fields:
            self.fields["stream"].queryset = Stream.objects.filter(
                school_class_id=self.data["school_class"])

    def clean(self):
        c = super().clean()
        if c.get("term") and c.get("academic_year"):
            if c["term"].academic_year_id != c["academic_year"].pk:
                raise forms.ValidationError("That term belongs to a different academic year.")
        if c.get("stream") and c.get("school_class"):
            if c["stream"].school_class_id != c["school_class"].pk:
                raise forms.ValidationError("That stream belongs to another class.")
        return c


class SchoolSettingsForm(forms.ModelForm):
    class Meta:
        model = SchoolSettings
        fields = ["mode"]


class GradeBandForm(forms.ModelForm):
    class Meta:
        model = GradeBand
        fields = ["letter", "min_mark", "points"]
        widgets = {"min_mark": forms.NumberInput(attrs={"min": 0, "max": 100, "step": 0.01}),
                   "points": forms.NumberInput(attrs={"step": 0.5})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["letter"].help_text = "The grade students see, for example A."
        self.fields["min_mark"].help_text = "Lowest mark that still earns this grade."
        self.fields["points"].help_text = "Points for reports, for example 4 for an A."


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "body", "audience", "school_class"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["title"].help_text = "Short headline."
        self.fields["body"].help_text = "The full notice."
        self.fields["audience"].help_text = ("Everyone, one role, or a single class. "
                                             "Parents also see notices aimed at students.")
        if SchoolSettings.get().is_school:
            self.fields["school_class"].empty_label = "Every class (no limit)"
            self.fields["school_class"].help_text = "Restrict the notice to one class."
        else:
            self.fields.pop("school_class")


class GuardianForm(forms.Form):
    """Link a student to a parent account, creating the account if needed."""
    username = forms.CharField(max_length=60, help_text="The parent logs in with this")
    first_name = forms.CharField(max_length=60, required=False)
    last_name = forms.CharField(max_length=60, required=False)
    password = forms.CharField(widget=forms.PasswordInput, min_length=6, required=False,
        help_text="Only needed when the parent does not have an account yet.")

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        existing = User.objects.filter(username=u).first()
        if existing and existing.role != "parent":
            raise forms.ValidationError(
                "That username belongs to someone who is not a parent account.")
        return u

    def clean(self):
        c = super().clean()
        u = c.get("username")
        if u and not User.objects.filter(username=u).exists():
            if not c.get("password"):
                self.add_error("password", "Set a password for the new parent account.")
            if not c.get("first_name"):
                self.add_error("first_name", "Give the new parent a first name.")
        return c

    def save(self, student):
        d = self.cleaned_data
        user, created = User.objects.get_or_create(
            username=d["username"],
            defaults={"first_name": d["first_name"], "last_name": d["last_name"],
                      "role": "parent"})
        if created:
            user.set_password(d["password"])
            user.save()
        link, _ = Guardian.objects.get_or_create(parent=user, student=student)
        return link
