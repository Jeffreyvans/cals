"""Signals raised by the finance app.

The fiscalisation module listens for :data:`financial_document_issued` so the
two apps stay independent: finance never imports fiscalisation, and a school
that has not switched the module on is unaffected.
"""

from django.dispatch import Signal

# Sent when an invoice, receipt, credit note or debit note has been issued.
# Receivers get ``instance`` and ``document_type``.
financial_document_issued = Signal()
