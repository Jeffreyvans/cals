"""The events a webhook endpoint can subscribe to."""

EVENTS = [
    ("student.created", "A student record was created"),
    ("invoice.created", "An invoice was raised"),
    ("payment.received", "A payment was received and receipted"),
    ("application.submitted", "A public application was submitted"),
    ("webhook.ping", "A test event from the settings screen"),
]

EVENT_NAMES = [code for code, _label in EVENTS]
