from django import forms

from academics.models import Course

from .models import (
    Assignment,
    Choice,
    ContentItem,
    CourseSite,
    Forum,
    ForumPost,
    ForumThread,
    GradeCategory,
    GradeItem,
    LiveSession,
    MatchingPair,
    Question,
    Quiz,
    SiteSection,
    Submission,
)

DATE_INPUT = forms.DateInput(attrs={"type": "date"})
DATETIME_INPUT = forms.DateTimeInput(
    attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
DATETIME_INPUT.input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"]
LOCAL_DATETIME_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                          "%Y-%m-%d %H:%M"]


def _accept_local_datetimes(form):
    for name in ("due_at", "opens_at", "closes_at", "starts_at", "ends_at"):
        if name in form.fields:
            form.fields[name].input_formats = LOCAL_DATETIME_FORMATS


class CourseSiteForm(forms.ModelForm):
    class Meta:
        model = CourseSite
        fields = ["course", "title", "description", "welcome", "is_published"]

    def __init__(self, user=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        courses = Course.objects.none()
        if user is not None:
            if user.is_superuser or user.role == "admin":
                courses = Course.objects.all()
            else:
                courses = getattr(user, "courses", Course.objects.none()).all()
        self.fields["course"].queryset = courses
        self.fields["course"].empty_label = "Choose a course"


class SiteSectionForm(forms.ModelForm):
    class Meta:
        model = SiteSection
        fields = ["title", "description", "order", "visible_from",
                  "visible_until", "is_published"]
        widgets = {"visible_from": DATE_INPUT, "visible_until": DATE_INPUT}


class ContentItemForm(forms.ModelForm):
    class Meta:
        model = ContentItem
        fields = ["section", "kind", "title", "body", "file", "url", "order",
                  "visible_from", "visible_until", "is_published"]
        widgets = {"visible_from": DATE_INPUT, "visible_until": DATE_INPUT,
                   "body": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, site=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if site is not None:
            self.fields["section"].queryset = site.sections.order_by("order", "pk")
        self.fields["section"].empty_label = "Choose a section"


class AssignmentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _accept_local_datetimes(self)

    class Meta:
        model = Assignment
        fields = ["title", "instructions", "due_at", "late_policy",
                  "late_penalty_percent", "max_score", "attachment",
                  "allow_resubmission", "is_published"]
        widgets = {"due_at": DATETIME_INPUT,
                   "instructions": forms.Textarea(attrs={"rows": 6})}


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ["text", "file"]
        widgets = {"text": forms.Textarea(attrs={"rows": 8})}


class GradeSubmissionForm(forms.Form):
    mark = forms.DecimalField(max_digits=7, decimal_places=2, required=False)
    feedback = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4}))


class QuizForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _accept_local_datetimes(self)

    class Meta:
        model = Quiz
        fields = ["title", "instructions", "time_limit_minutes", "max_attempts",
                  "randomize_questions", "randomize_choices", "opens_at",
                  "closes_at", "is_published"]
        widgets = {"opens_at": DATETIME_INPUT, "closes_at": DATETIME_INPUT,
                   "instructions": forms.Textarea(attrs={"rows": 5})}


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ["kind", "text", "points", "order", "accepted_answers",
                  "explanation"]
        widgets = {"text": forms.Textarea(attrs={"rows": 4}),
                   "accepted_answers": forms.Textarea(attrs={"rows": 3}),
                   "explanation": forms.Textarea(attrs={"rows": 3})}


class ChoiceForm(forms.ModelForm):
    class Meta:
        model = Choice
        fields = ["text", "is_correct", "order"]


class MatchingPairForm(forms.ModelForm):
    class Meta:
        model = MatchingPair
        fields = ["left_text", "right_text", "order"]


class ForumForm(forms.ModelForm):
    class Meta:
        model = Forum
        fields = ["title", "description", "is_published"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class ThreadForm(forms.ModelForm):
    class Meta:
        model = ForumThread
        fields = ["title", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 6})}


class PostForm(forms.ModelForm):
    class Meta:
        model = ForumPost
        fields = ["body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 5})}


class LiveSessionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _accept_local_datetimes(self)

    class Meta:
        model = LiveSession
        fields = ["title", "provider", "join_url", "starts_at", "ends_at",
                  "is_published"]
        widgets = {"starts_at": DATETIME_INPUT, "ends_at": DATETIME_INPUT}


class GradeCategoryForm(forms.ModelForm):
    class Meta:
        model = GradeCategory
        fields = ["name", "weight", "order"]


class GradeItemForm(forms.ModelForm):
    class Meta:
        model = GradeItem
        fields = ["category", "title", "max_score", "due_date", "is_published",
                  "order"]
        widgets = {"due_date": DATE_INPUT}

    def __init__(self, site=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if site is not None:
            self.fields["category"].queryset = site.grade_categories.order_by(
                "order", "pk")
        self.fields["category"].empty_label = "Choose a category"


class GradeEntryForm(forms.Form):
    score = forms.DecimalField(max_digits=7, decimal_places=2, required=False)
    feedback = forms.CharField(required=False)
