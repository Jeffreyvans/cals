# Vanpere error codes

This is the reference for every user-facing failure in Vanpere. The single
source of truth is `core/error_catalogue.py`: pages, inline messages, JSON API
errors, logs and the platform **Errors** page all read the same `ErrorSpec`.
A message always names its category (for example, "Network problem: ...") so
people can tell the difference between their connection, their account, their
input and our systems.

## What every error surface shows

Every categorised error carries:

- an icon **and** a text category label, so colour is never the only signal;
- the category, code (for example `NET-OFFLINE`) and a plain-English headline;
- an explanation of what happened and a short action;
- numbered next steps;
- **Try again** and **Go back** actions where they make sense;
- a reference such as `ERR-A1B2C3` with a **Copy** button;
- the time, the two support contacts (`+263774573765` and
  `vanhuvaonejeffrey@gmail.com`);
- a **Technical details** panel. Only platform administrators and users holding
  `errors.view_details` see exception classes, view names and request IDs there.
  Nobody sees stack traces, SQL, request bodies, headers, passwords or tokens.

## How classification works

`core.errors.classify_exception(exc, request, status=None)` maps an exception
and/or HTTP status to one catalogue entry. `core.error_middleware.ErrorContextMiddleware`
gives each request an ID, classifies failures (including views that return a 4xx
or 5xx directly), records one `ErrorRecord`, and writes one structured line to
the `edusphere.errors` logger with the category, code, reference, request ID,
view, duration and safe user/institution IDs.

`render_error()` builds pages, `error_json()` builds API responses from the same
entry, and `api_exception_handler()` adds codes to DRF errors without removing
their original `detail`. The browser reads `/errors/catalogue.js`, a static,
CSP-safe external script generated from the same Python catalogue.

## The 30 codes

The "Message" column is the exact one-line catalogue message (`title (code). action`).
Some views deliberately use a shorter safe variant, for example login says
"Wrong email, username or password (AUTH-FAILED)", but the category and code are
always present.

| Code | Category | Message | Who fixes it | Manual trigger |
| --- | --- | --- | --- | --- |
| `NET-OFFLINE` | NETWORK | Network problem: your device looks offline (NET-OFFLINE). Reconnect to the internet and try again. Your unsaved answers are kept on this device. | user | Open `/offline/`, or tick **Offline** in browser DevTools → Network and submit a form. |
| `NET-SLOW` | NETWORK | Network problem: your connection is very slow (NET-SLOW). Wait a little longer, or try again on a stronger connection. | user | Set DevTools throttling to **Slow 3G** and submit a form; the 5-second warning appears. |
| `NET-TIMEOUT` | NETWORK | Network problem: the request timed out (NET-TIMEOUT). Try again with the same request. A retry key prevents duplicate payments or registrations. | user | Throttle to **Offline** after clicking Submit, or block the request for more than 15 seconds. |
| `NET-UNREACHABLE` | NETWORK | Network problem: the server could not be reached (NET-UNREACHABLE). Check your connection and try again. If it persists, ask your administrator to check the school network. | user | Change the API host to an unresolvable domain in staging, or raise `requests.ConnectionError("dns")` in a temporary view. |
| `SRV-500` | SERVER | Server problem: our system had a fault, it is not your internet (SRV-500). Try again. If it keeps happening, send us the reference number below. | developer | In a temporary staging view, `raise RuntimeError("classified-fault")` and open it. |
| `SRV-503` | SERVER | Server problem: the service is temporarily unavailable (SRV-503). Wait a minute and try again. | platform admin | Scale the web service to zero, restart it during a request, or return `HttpResponse(status=503)` from a temporary view. |
| `SRV-504` | SERVER | Server problem: the gateway timed out (SRV-504). Try again in a moment; this is not caused by your device. | platform admin | Hold a response longer than the reverse-proxy timeout, or return `HttpResponse(status=504)` from a temporary view. |
| `SRV-429` | SERVER | Server problem: the server is too busy (SRV-429). Wait before trying again. | platform admin | Submit a throttled login/API endpoint repeatedly, or return `HttpResponse(status=429)` from a temporary view. |
| `SRV-MAINT` | SERVER | Server problem: scheduled maintenance is running (SRV-MAINT). Wait for maintenance to finish, then try again. | platform admin | In staging, call `render_error(request, "SRV-MAINT")` from a temporary view; there is no production maintenance switch yet. |
| `DB-DOWN` | DATABASE | Database problem: the data store is unavailable (DB-DOWN). Try again shortly. If it continues, the platform team must restore the database. | platform admin | Point `DATABASE_URL` at a closed port in staging, or raise `django.db.OperationalError("db gone")` in a temporary view. |
| `DB-CONFLICT` | DATABASE | Database problem: this record conflicts with another one (DB-CONFLICT). Reload the page, check the latest values, then save again. | user | Open the same record in two tabs, change it in both, save the older tab; or create a duplicate unique value. |
| `DB-PROTECTED` | DATABASE | Database problem: this record is in use and cannot be deleted (DB-PROTECTED). Remove the linked records first, or archive this one instead. | school administrator | Try to delete a department, year or other record that still has linked staff/students, or raise `ProtectedError("in use", [])`. |
| `AUTH-REQUIRED` | ACCESS | Access problem: you are not signed in (AUTH-REQUIRED). Sign in with your school or platform account, then try again. | user | Sign out and open `/dashboard/`. |
| `AUTH-EXPIRED` | ACCESS | Access problem: your session expired (AUTH-EXPIRED). Sign in again. Your place on this page will be kept where possible. | user | Delete the `sessionid` cookie in DevTools and reload a private page, or sign in on another device with the same account. |
| `AUTH-FAILED` | ACCESS | Access problem: wrong username or password (AUTH-FAILED). Check your details and try again, or ask an administrator to reset your password. | user | Enter a wrong password at `/login/` or an institution login page. The same message is used for an unknown account or an account from another school. |
| `AUTH-LOCKED` | ACCESS | Access problem: this account is temporarily locked (AUTH-LOCKED). Wait until the unlock time shown, then sign in again, or ask an administrator to unlock it. | school administrator | Fail login 8 times for one account (`LOGIN_FAILURE_LIMIT`), then try again with the correct password. |
| `AUTH-UNVERIFIED` | ACCESS | Access problem: your email is not verified (AUTH-UNVERIFIED). Use the verification link we sent, or ask for a new one. | user | Register a student account and open a protected page before following the verification email. |
| `AUTH-2FA` | ACCESS | Access problem: a two-factor code is needed (AUTH-2FA). Enter the current six-digit code and continue. | user | Perform a platform action that requires a fresh second factor, or raise an exception whose view hint contains `2fa` in a temporary view. |
| `PERM-DENIED` | ACCESS | Access problem: you do not have permission (PERM-DENIED). Ask your school administrator for the role named below, then try again. | school administrator | Sign in as a teacher and open a registrar-only page, or raise `PermissionDenied("You cannot do that")`. |
| `ORG-SUSPENDED` | ACCESS | Access problem: your institution is suspended (ORG-SUSPENDED). Ask your school's billing contact to visit the billing contact page and settle the account. | school administrator | Set `institution.suspended_at` (or `is_active=False`) on your test user's school and open any app page. `/billing/contact/` stays reachable. |
| `NOT-FOUND` | ACCESS | Access problem: that page or record was not found (NOT-FOUND). Go back and choose the item again, or check the address. | user | Visit `/this-page-does-not-exist/`, or open a deleted record's old URL. |
| `CSRF` | ACCESS | Access problem: the security token expired - please reload (CSRF). Reload the page and submit the form again. No data was sent. | user | Leave a form open until its session/CSRF token expires, then submit; send a POST without a CSRF token; or post an invalid signature to `/webhooks/resend/`. |
| `INPUT-INVALID` | INPUT | Input problem: some fields need attention (INPUT-INVALID). Correct the highlighted fields and submit again. | user | Submit a form with a missing or malformed required field, or raise `ValidationError("Enter a number")`. |
| `FILE-REJECTED` | INPUT | Input problem: the file was rejected (FILE-REJECTED). Choose a smaller file of an accepted type and try again. | user | Upload a logo larger than 2 MB, an SVG, or a file whose real type does not match its extension. The message includes the actual and allowed sizes. |
| `IMPORT-ROWS` | INPUT | Input problem: some spreadsheet rows have errors (IMPORT-ROWS). Download the row report, fix the listed cells and upload again. | user | Upload a CSV/XLSX with bad or duplicate rows on a bulk upload screen, then open the row error report. |
| `EXT-EMAIL` | EXTERNAL | External service problem: the email service did not respond (EXT-EMAIL). Use Show one-time link instead, or try sending later. | platform admin | Break `RESEND_API_KEY` in staging and trigger a verification or welcome email. The action still completes; the one-time link is shown. |
| `EXT-PAY` | EXTERNAL | External service problem: the payment gateway failed (EXT-PAY). Try again with the same request. A retry key prevents a second charge. | platform admin | Use invalid Paynow credentials in staging and start an online payment, or raise a gateway error in a temporary view. |
| `EXT-STORAGE` | EXTERNAL | External service problem: file storage is unavailable (EXT-STORAGE). Wait a moment and upload again. | platform admin | Point the S3-compatible storage settings at an unreachable endpoint in staging and upload a file. |
| `EXT-FISCAL` | EXTERNAL | External service problem: the fiscalisation service is unavailable (EXT-FISCAL). The submission will retry automatically. You can also retry it from the document page. | platform admin | Use invalid FDMS credentials in staging and issue a fiscalised document, or raise an exception from a view whose hint contains `fiscal`. |
| `EXT-HOOK` | EXTERNAL | External service problem: a webhook delivery failed (EXT-HOOK). The delivery will be retried automatically; no action is needed from you. | developer | Configure an outgoing webhook URL that refuses connections, trigger the event, and inspect the Jobs page for the retry. |

## Safe manual triggers in staging

To check the page, inline message or JSON for a specific code without breaking a
real service, add a temporary development-only view:

```python
from core.errors import add_error_message, error_json, render_error

def trigger_page(request, code):
    return render_error(request, code)

def trigger_inline(request, code):
    add_error_message(request, code)
    # then redirect to any normal page
```

`render_error()` accepts the code, status, exception and extra detail; never
pass real secrets as `exception` or `detail`. For API parity, call
`error_json(request, code)` with a POST request. Remove temporary views before
deploying. The automated equivalents are in `core/tests/test_error_handling.py`.

## Browser and offline behaviour

- `static/js/error-handling.js` listens for `online`/`offline`, warns after 5
  seconds, shows a timeout after 15 seconds, and keeps non-password form values
  in `sessionStorage` until a retry succeeds.
- Retries of payments and registrations include an `idempotency_key` stored by
  `core.idempotency`; the server reuses the first result for the same key, so a
  retry cannot charge twice or create two students.
- `/errors/catalogue.js` supplies the same titles, steps and codes to the
  browser. It is an external script (no inline JavaScript, no `innerHTML`), so
  it works under the project's CSP.
- `static/js/sw.js` only caches GET responses explicitly marked
  `X-EduSphere-Offline-Public: 1`. `ErrorContextMiddleware` adds that header only
  to anonymous, signed-out public pages; signed-in HTML is never cached. An
  uncached navigation while offline falls back to `/offline/`.

## Support and administration

Platform staff with `platform.view_health` can open **Platform → Errors**. The
page filters by category and code, shows trends, looks up a reference, and links
to Email health, Jobs and Audit. Technical record fields such as exception class
and view name are shown only when `can_view_error_details(user)` is true.
Support can quote the `ERR-XXXXXX` reference; the matching structured log line
and `ErrorRecord` contain the request ID, category, code, view, duration and
safe IDs without any secret or stack trace.
