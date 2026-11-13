from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone

from academics.factories import ProgrammeFactory, StudentFactory
from admissions.models import (
    AlumniRecord,
    ApplicantResult,
    Application,
    Certificate,
    EntryRequirement,
    GraduationClearance,
    LifecycleTransition,
    Offer,
    StudentCard,
    TransferRecord,
)


class ApplicationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Application

    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.LazyAttribute(
        lambda o: f"{o.first_name}.{o.last_name}@example.com".lower())
    phone = "0770000000"
    programme = factory.SubFactory(ProgrammeFactory)
    previous_results = "Mathematics: B\nEnglish: C"
    status = Application.Status.APPLIED


class ApplicantResultFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ApplicantResult

    application = factory.SubFactory(ApplicationFactory)
    subject = factory.Sequence(lambda n: f"Subject {n}")
    grade = "B"


class EntryRequirementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EntryRequirement
        django_get_or_create = ("programme",)

    programme = factory.SubFactory(ProgrammeFactory)
    minimum_subjects = 2
    minimum_grade = "D"
    required_subjects = ""


class OfferFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Offer

    application = factory.SubFactory(ApplicationFactory)
    acceptance_deadline = factory.LazyFunction(
        lambda: timezone.localdate() + timedelta(days=14))
    deposit_amount = Decimal("100.00")
    status = Offer.Status.PENDING


class LifecycleTransitionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LifecycleTransition

    student = factory.SubFactory(StudentFactory)
    from_state = "enrolled"
    to_state = "deferred"
    reason = "Medical leave"


class TransferRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TransferRecord

    student = factory.SubFactory(StudentFactory)
    direction = TransferRecord.Direction.OUT
    other_institution = "Another College"
    reason = "Family moved"


class GraduationClearanceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = GraduationClearance

    student = factory.SubFactory(StudentFactory)
    fees_cleared = True
    library_books_returned = True
    hostel_keys_returned = True
    exams_passed = True


class CertificateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Certificate

    student = factory.SubFactory(StudentFactory, status="graduated")
    title = "Certificate of Award"
    award = "Diploma in Information Technology"


class StudentCardFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StudentCard

    student = factory.SubFactory(StudentFactory)
    valid_until = factory.LazyFunction(
        lambda: timezone.localdate() + timedelta(days=365))


class AlumniRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AlumniRecord

    student = factory.SubFactory(StudentFactory, status="alumni")
    graduation_year = factory.LazyFunction(lambda: timezone.localdate().year)
