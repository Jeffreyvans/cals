import pytest
from django.contrib.auth import get_user_model

from academics.factories import AcademicYearFactory, ProgrammeFactory, SchoolClassFactory, StudentFactory, TermFactory
from accounts.factories import UserFactory


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def student_user(db):
    return UserFactory(role="student")


@pytest.fixture
def registrar_user(db):
    return UserFactory(role="registrar")


@pytest.fixture
def bursar_user(db):
    return UserFactory(role="bursar")


@pytest.fixture
def lecturer_user(db):
    return UserFactory(role="lecturer")


@pytest.fixture
def admin_user(db):
    return UserFactory(role="admin", is_staff=True, is_superuser=True)


@pytest.fixture
def programme(db):
    return ProgrammeFactory()


@pytest.fixture
def academic_year(db):
    return AcademicYearFactory(is_current=True)


@pytest.fixture
def term(db, academic_year):
    return TermFactory(academic_year=academic_year, is_current=True)


@pytest.fixture
def school_class(db):
    return SchoolClassFactory()


@pytest.fixture
def student(db, programme, student_user):
    return StudentFactory(user=student_user, programme=programme)


@pytest.fixture
def invoice_factory():
    from finance.factories import InvoiceFactory

    return InvoiceFactory


@pytest.fixture
def payment_factory():
    from finance.factories import PaymentFactory

    return PaymentFactory


@pytest.fixture
def waiver_factory():
    from finance.factories import WaiverFactory

    return WaiverFactory
