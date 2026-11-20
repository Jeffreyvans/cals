"""Online payment gateways.

The base class keeps the rest of the application away from a provider's HTTP
details. The Paynow implementation talks to Zimbabwe's Paynow service with the
standard library, so no extra dependency is needed.
"""

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from core.tenancy import use_institution

from .models import GatewayTransaction, Invoice
from .services import receive_payment


class GatewayError(Exception):
    """Raised when a gateway cannot start or finish a payment."""


class PaymentGateway:
    """The interface every online gateway implements."""

    name = "base"

    def initiate(self, transaction, return_url="", result_url=""):
        raise NotImplementedError

    def poll(self, transaction):
        raise NotImplementedError

    def handle_webhook(self, payload, signature=""):
        raise NotImplementedError

    def verify_signature(self, payload, signature):
        raise NotImplementedError


def _post(url, data):
    encoded = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=encoded, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise GatewayError(f"The payment gateway could not be reached: {exc}") from exc


def _parse_response(text):
    return {key: values[-1] for key, values in urllib.parse.parse_qs(text).items()}


class PaynowGateway(PaymentGateway):
    """Paynow Zimbabwe: initiate, poll and webhook with signature checks."""

    name = "paynow"
    initiate_url = "https://www.paynow.co.zw/interface/initiatetransaction"
    poll_url = "https://www.paynow.co.zw/interface/checkpayment"

    def __init__(self, integration_id=None, integration_key=None, mode=None,
                 return_url=None, result_url=None):
        self.integration_id = integration_id or getattr(
            settings, "PAYNOW_INTEGRATION_ID", "")
        self.integration_key = integration_key or getattr(
            settings, "PAYNOW_INTEGRATION_KEY", "")
        self.mode = mode or getattr(settings, "PAYNOW_MODE", "test")
        self.return_url = return_url or getattr(settings, "PAYNOW_RETURN_URL", "")
        self.result_url = result_url or getattr(settings, "PAYNOW_RESULT_URL", "")

    def _hash(self, values):
        raw = "".join(str(value) for value in values) + self.integration_key
        return hashlib.sha512(raw.encode("utf-8")).hexdigest().upper()

    def initiate(self, transaction, return_url="", result_url=""):
        if not self.integration_id or not self.integration_key:
            raise GatewayError(
                "Paynow is not configured. Set PAYNOW_INTEGRATION_ID and "
                "PAYNOW_INTEGRATION_KEY.")
        fields = [
            ("id", self.integration_id),
            ("reference", transaction.reference),
            ("amount", f"{transaction.amount:.2f}"),
            ("additionalinfo", transaction.reference),
            ("returnurl", return_url or self.return_url),
            ("resulturl", result_url or self.result_url),
            ("authemail", ""),
            ("status", "Message"),
        ]
        fields.append(("hash", self._hash([value for _key, value in fields])))
        response = _parse_response(_post(self.initiate_url, fields))
        transaction.payload = response
        transaction.status = GatewayTransaction.Status.PENDING
        transaction.save(update_fields=["payload", "status", "updated"])
        if response.get("status", "").lower() == "error":
            raise GatewayError(response.get("error", "Paynow rejected the payment."))
        redirect = response.get("redirecturl") or response.get("browserurl")
        if not redirect:
            raise GatewayError("Paynow did not return a redirect URL.")
        return redirect

    def poll(self, transaction):
        fields = [("id", self.integration_id),
                  ("reference", transaction.reference)]
        fields.append(("hash", self._hash([value for _key, value in fields])))
        response = _parse_response(_post(self.poll_url, fields))
        transaction.payload = response
        transaction.save(update_fields=["payload", "updated"])
        status = (response.get("status") or "").lower()
        if status in ("paid", "awaiting delivery", "delivered"):
            self._complete(transaction, response)
        elif status in ("cancelled", "failed", "error"):
            transaction.status = (GatewayTransaction.Status.CANCELLED
                                  if status == "cancelled"
                                  else GatewayTransaction.Status.FAILED)
            transaction.save(update_fields=["status", "updated"])
        return transaction

    def verify_signature(self, payload, signature):
        if not self.integration_key:
            return False
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return False
        values = [str(value) for key, value in payload.items() if key != "hash"]
        expected = self._hash(values)
        # Constant-time comparison: a webhook signature is a shared secret.
        return hmac.compare_digest(expected, str(signature or payload.get("hash", "")).upper())

    def handle_webhook(self, payload, signature=""):
        if not self.verify_signature(payload, signature):
            raise GatewayError("The webhook signature is not valid.")
        reference = payload.get("reference") or payload.get("referenceid")
        # A webhook is unauthenticated as a Django user, so it is scoped to the
        # default tenant. References are global, and are unique, so look up the
        # transaction across tenants and lock it while deciding the outcome.
        with db_transaction.atomic():
            row = GatewayTransaction.all_objects.select_for_update().filter(
                reference=reference).first()
            if row is None:
                raise GatewayError("No transaction matches this reference.")
            if row.status == GatewayTransaction.Status.PAID:
                return row
            status = (payload.get("status") or "").lower()
            if status in ("paid", "awaiting delivery", "delivered"):
                return self._complete(row, payload)
            if status in ("cancelled", "failed", "error"):
                row.status = (GatewayTransaction.Status.CANCELLED
                              if status == "cancelled"
                              else GatewayTransaction.Status.FAILED)
                row.save(update_fields=["status", "updated"])
            return row

    def _complete(self, transaction, payload):
        """Post a payment exactly once, even under concurrent webhooks/polls."""
        with db_transaction.atomic():
            row = GatewayTransaction.all_objects.select_for_update().get(
                pk=transaction.pk)
            if row.status == GatewayTransaction.Status.PAID:
                return row
            if row.invoice_id:
                invoice = Invoice.all_objects.select_for_update().get(pk=row.invoice_id)
                if (invoice.currency and row.currency
                        and invoice.currency != row.currency):
                    raise GatewayError("The payment currency does not match the invoice.")
                if invoice.balance <= 0:
                    # Never create an unexplained credit from a duplicate or
                    # late callback for an already-settled invoice.
                    raise GatewayError("This invoice has already been settled.")
                with use_institution(invoice.institution):
                    payment, _credit = receive_payment(
                        invoice.student, invoice,
                        Decimal(row.amount), method="mobile",
                        reference=row.reference,
                        payer_name="Online payment",
                        received_by=None,
                        currency=row.currency,
                        exchange_rate=Decimal("1"))
                row.payment = payment
            row.status = GatewayTransaction.Status.PAID
            row.payload = payload
            row.save(update_fields=["status", "payload", "payment", "updated"])
            return row


def get_gateway(code="paynow"):
    gateways = {"paynow": PaynowGateway}
    try:
        return gateways[code]()
    except KeyError as exc:
        raise GatewayError(f"Unknown payment gateway: {code}") from exc


def start_online_payment(invoice, amount, idempotency_key, gateway_code="paynow",
                         return_url="", result_url=""):
    """Create the transaction record and ask the gateway for a redirect URL."""
    transaction, created = GatewayTransaction.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "provider": gateway_code,
            # Second-resolution timestamps collide when staff retry quickly;
            # the random suffix plus a unique DB constraint makes retries safe.
            "reference": f"ON{timezone.now():%y%m%d%H%M%S}{invoice.pk}{uuid.uuid4().hex[:8].upper()}",
            "amount": amount,
            "currency": invoice.student.institution.currency,
            "invoice": invoice,
        })
    if not created:
        existing = (transaction.payload or {}).get("browserurl") or \
            (transaction.payload or {}).get("redirecturl")
        if existing:
            return transaction, existing
        if transaction.status == GatewayTransaction.Status.PAID:
            raise GatewayError("This payment has already been completed.")
    redirect = get_gateway(gateway_code).initiate(transaction, return_url, result_url)
    return transaction, redirect
