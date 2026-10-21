from django import forms

from .models import Invoice, Payment, Waiver


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
        fields = ["description", "amount", "due_date"]
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}
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
