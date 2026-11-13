from django import forms

from .models import (
    Account,
    Budget,
    CollectionTarget,
    DebitNote,
    Discount,
    ExchangeRate,
    Expense,
    FeeItem,
    FeeStructure,
    FinancialYear,
    Instalment,
    InstalmentPlan,
    Invoice,
    LatePenaltyRule,
    Payment,
    Refund,
    Sponsor,
    Sponsorship,
    Supplier,
    TaxRate,
    Waiver,
)


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["invoice", "amount", "method", "reference"]
    def __init__(self, student, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["invoice"].queryset = student.invoices.all()
        self.fields["invoice"].empty_label = "Choose an invoice"

class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["description", "amount", "due_date", "tax_category"]
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["tax_category"].required = False
        self.fields["tax_category"].help_text = (
            "Only a VAT-registered institution charges standard-rated VAT.")

    def clean_amount(self):
        a = self.cleaned_data["amount"]
        if a <= 0:
            raise forms.ValidationError("Amount must be above zero.")
        return a

class WaiverForm(forms.ModelForm):
    class Meta:
        model = Waiver
        fields = ["amount", "reason"]
    def __init__(self, invoice, *a, **kw):
        super().__init__(*a, **kw)
        self.instance.invoice = invoice
        self.fields["amount"].help_text = "Part of the invoice to forgive."
        self.fields["reason"].help_text = "Recorded on the credit note."


class DebitNoteForm(forms.ModelForm):
    class Meta:
        model = DebitNote
        fields = ["amount", "reason"]

    def __init__(self, invoice, *a, **kw):
        super().__init__(*a, **kw)
        self.instance.invoice = invoice
        self.fields["amount"].help_text = "Extra amount to add to the invoice."
        self.fields["reason"].help_text = "Recorded on the debit note."

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Amount must be above zero.")
        return amount


class TaxRateForm(forms.ModelForm):
    class Meta:
        model = TaxRate
        fields = ["name", "category", "rate", "effective_from", "effective_to"]
        widgets = {
            "effective_from": forms.DateInput(attrs={"type": "date"}),
            "effective_to": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        data = super().clean()
        start = data.get("effective_from")
        end = data.get("effective_to")
        if start and end and end < start:
            self.add_error("effective_to",
                           "The end date is before the start date.")
        return data


class ExchangeRateForm(forms.ModelForm):
    class Meta:
        model = ExchangeRate
        fields = ["currency", "rate", "effective_from"]
        widgets = {"effective_from": forms.DateInput(attrs={"type": "date"})}


class FeeStructureForm(forms.ModelForm):
    class Meta:
        model = FeeStructure
        fields = ["code", "name", "academic_year", "programme", "school_class",
                  "term", "is_active"]


class FeeItemForm(forms.ModelForm):
    class Meta:
        model = FeeItem
        fields = ["name", "kind", "amount", "tax_category", "is_mandatory"]


class GenerateInvoicesForm(forms.Form):
    structure = forms.ModelChoiceField(queryset=FeeStructure.objects.none())
    term = forms.ModelChoiceField(queryset=None, required=False)

    def __init__(self, *a, **kw):
        from academics.models import Term

        super().__init__(*a, **kw)
        self.fields["structure"].queryset = FeeStructure.objects.filter(is_active=True)
        self.fields["term"].queryset = Term.objects.all()


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["code", "name", "type", "is_active"]


class FinancialYearForm(forms.ModelForm):
    class Meta:
        model = FinancialYear
        fields = ["name", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and start > end:
            raise forms.ValidationError("The start date is after the end date.")
        return cleaned


class DiscountForm(forms.ModelForm):
    class Meta:
        model = Discount
        fields = ["student", "invoice", "fee_item", "sponsor", "name", "category",
                  "kind", "value", "valid_from", "valid_to", "reason"]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_to": forms.DateInput(attrs={"type": "date"}),
        }


class SponsorForm(forms.ModelForm):
    class Meta:
        model = Sponsor
        fields = ["name", "type", "contact_name", "email", "phone", "address",
                  "is_active"]


class SponsorshipForm(forms.ModelForm):
    class Meta:
        model = Sponsorship
        fields = ["sponsor", "student", "percentage", "fixed_amount",
                  "valid_from", "valid_to", "reference", "is_active"]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_to": forms.DateInput(attrs={"type": "date"}),
        }


class InstalmentPlanForm(forms.ModelForm):
    class Meta:
        model = InstalmentPlan
        fields = ["student", "term", "name", "total_amount", "is_active"]


class InstalmentForm(forms.ModelForm):
    class Meta:
        model = Instalment
        fields = ["due_date", "amount", "paid_amount"]
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}


class LatePenaltyRuleForm(forms.ModelForm):
    class Meta:
        model = LatePenaltyRule
        fields = ["name", "kind", "value", "grace_days", "cap_amount", "is_active"]


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "code", "contact_name", "email", "phone", "address",
                  "is_active"]


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["supplier", "department", "financial_year", "description",
                  "category", "amount", "date", "reference"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class BudgetForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ["name", "department", "financial_year", "amount"]


class CollectionTargetForm(forms.ModelForm):
    class Meta:
        model = CollectionTarget
        fields = ["name", "department", "financial_year", "start_date",
                  "end_date", "amount"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        data = super().clean()
        start = data.get("start_date")
        end = data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The target must end on or after it starts.")
        return data


class RefundForm(forms.ModelForm):
    class Meta:
        model = Refund
        fields = ["payment", "amount", "reason"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from .models import Payment

        self.fields["payment"].queryset = Payment.objects.order_by("-paid_on")
