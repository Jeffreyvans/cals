from datetime import date
from decimal import Decimal

import factory

from finance.models import Invoice, MoneyLog, Payment, Waiver


class InvoiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Invoice

    student = factory.SubFactory("academics.factories.StudentFactory")
    description = factory.Sequence(lambda n: f"Invoice {n}")
    amount = Decimal("500.00")
    due_date = date.today()


class PaymentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Payment

    invoice = factory.SubFactory(InvoiceFactory)
    amount = Decimal("100.00")
    method = "cash"
    reference = ""


class WaiverFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Waiver

    invoice = factory.SubFactory(InvoiceFactory)
    amount = Decimal("50.00")
    reason = "Bursary"


class MoneyLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MoneyLog

    action = "invoice"
    amount = Decimal("500.00")
    student = factory.SubFactory("academics.factories.StudentFactory")
    invoice = factory.SubFactory(InvoiceFactory)
    description = "Invoice"
