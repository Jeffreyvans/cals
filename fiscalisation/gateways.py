"""ZIMRA FDMS gateways.

The mock gateway is the default so the whole flow can be exercised without
credentials. The real gateway talks to ZIMRA's Fiscal Device Management System
and refuses to run until the endpoint and API key are configured, so a school
never accidentally reports to nowhere.
"""

import hashlib
import json
import urllib.error
import urllib.request

from django.conf import settings


class FiscalGatewayError(Exception):
    """Raised when a fiscal document cannot be submitted."""


class FiscalGateway:
    """The interface every FDMS gateway implements."""

    name = "base"

    def submit(self, device, fiscal_day, payload):
        raise NotImplementedError


class MockGateway(FiscalGateway):
    """Deterministic stand-in used in development and tests."""

    name = "mock"

    def submit(self, device, fiscal_day, payload):
        counter = int(payload.get("receipt_counter") or 0)
        raw = "|".join([
            str(getattr(device, "device_id", "")),
            str(getattr(fiscal_day, "fiscal_day_no", 0)),
            str(counter),
            str(payload.get("document_number", "")),
            str(payload.get("document_type", "")),
        ])
        code = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()
        return {
            "status": "verified",
            "receipt_counter": counter,
            "verification_code": code,
            "qr_data": f"https://fdms.zimra.co.zw/verify/{code}",
            "response": {"gateway": "mock", "verification_code": code},
        }


class ZimraFdmsGateway(FiscalGateway):
    """The live ZIMRA FDMS endpoint.

    The request and response shapes below follow the FDMS receipt API. They
    have not been exercised against ZIMRA yet because the test endpoint and
    credentials are still outstanding; wire the real field names in here once
    the documentation and credentials are supplied.
    """

    name = "zimra"

    def submit(self, device, fiscal_day, payload):
        url = settings.ZIMRA_FDMS_URL
        api_key = settings.ZIMRA_API_KEY
        if not url or not api_key:
            raise FiscalGatewayError(
                "ZIMRA FDMS is not configured. Set EDUSPHERE_ZIMRA_FDMS_URL "
                "and EDUSPHERE_ZIMRA_API_KEY (and the device id/serial).")
        body = json.dumps({
            "deviceID": getattr(device, "device_id", ""),
            "deviceSerialNo": getattr(device, "device_serial", ""),
            "fiscalDayNo": getattr(fiscal_day, "fiscal_day_no", 0),
            "receipt": payload,
        }).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            })
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                reply = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise FiscalGatewayError(f"ZIMRA FDMS request failed: {exc}") from exc
        return {
            "status": "verified",
            "receipt_counter": reply.get("receiptCounter",
                                         payload.get("receipt_counter", 0)),
            "verification_code": reply.get("verificationCode", ""),
            "qr_data": reply.get("qrCodeUrl", ""),
            "response": reply,
        }


def get_gateway(name=None):
    name = (name or settings.FISCALISATION_GATEWAY or "mock").lower()
    if name in ("zimra", "fdms", "live"):
        return ZimraFdmsGateway()
    return MockGateway()
