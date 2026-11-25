# Production readiness audit — Vanpere

**Audit date:** 2026-11-20  
**Baseline commit:** `ad55c13`  
**Scope:** safe code/config fixes plus an evidence-based readiness assessment. No feature was intentionally removed or changed in behaviour except where a bug made the old behaviour unsafe or dishonest (non-delivering notification stubs, anonymous permission bypass, duplicate payment postings, over-refunds).  
**Severity key:** **blocker** = must be resolved before any production pilot; **high** = must have an owner and a dated plan before go-live; **medium** = should be resolved in the first hardening window; **low** = documentation/polish.

This report is the companion to [DEPLOY.md](DEPLOY.md), [MANUAL_TESTING.md](MANUAL_TESTING.md), [docs/ROLE_GUIDES.md](docs/ROLE_GUIDES.md), [docs/PILOT_ONBOARDING.md](docs/PILOT_ONBOARDING.md), [PILOT_PLAN.md](PILOT_PLAN.md), [PILOT_LIMITATIONS.md](PILOT_LIMITATIONS.md), [docs/LOAD_TEST_RESULTS.md](docs/LOAD_TEST_RESULTS.md) and [docs/ERROR_CODES.md](docs/ERROR_CODES.md).

---

## 1. Hosting and configuration

| Item | Assessment |
| --- | --- |
| **Existing functionality** | Environment-driven settings in `config/settings.py`; `render.yaml` sets `DEBUG=false`, generates `SECRET_KEY`, sets `ALLOWED_HOSTS`/CSRF origins through Render, uses gunicorn + WhiteNoise, and defines `/health/` as the health check. TLS redirect, HSTS, secure cookies and DB SSL are enabled when `DEBUG` is off. `config/env.py` loads `.env` with real environment variables winning. An optional Sentry integration is wired in `config/settings.py`. |
| **Gaps** | `DEBUG` defaults to `True` outside Render (`DEBUG = env_bool("DEBUG", ..., not RENDER)`), so a non-Render production host without `DEBUG=false` gets a throwaway secret, wildcard hosts and no secure cookies. `render.yaml` provisions only a web service — no worker/beat/cron. No Sentry DSN, alert route, log-retention policy or external uptime monitor is configured in the repository. Support contact defaults in `config/brand.py` are placeholder personal details. |
| **Severity** | **high** (fail-open DEBUG, missing job runner); **medium** (observability/contacts). |
| **Changes made** | Support phone/email are now configurable via `SUPPORT_PHONE` / `SUPPORT_EMAIL` and shown on error surfaces; the status page reports configuration honestly; slow-query logging and the Sentry SDK pin were added. |
| **Human/external decisions** | Choose hosting tier/domain; set `DEBUG=false` explicitly on every production host; confirm TLS proxy headers; provide real support contacts; choose Sentry project/DSN, alert channels, on-call rota and log retention; provision the worker/beat/cron service. |

## 2. Database backups, restore and retention

| Item | Assessment |
| --- | --- |
| **Existing functionality** | `backup_finance` wrote Fernet-encrypted JSON; `prune_finance` existed with a six-year window. |
| **Gaps** | Before this audit there was no restore command, no format version, no per-model counts, no per-institution scoping in prune, and no proof that a backup could be restored. There is no scheduled job, offsite copy, checksum manifest, key escrow or documented production restore drill in the repository. `--plain` can write unencrypted financial and personal data. |
| **Severity** | **high** — a **blocker** if no external scheduler/offsite copy is provisioned. |
| **Changes made** | `backup_finance.py` now uses `FORMAT_VERSION = 2`, covers every finance and fiscalisation model (asserted by a test), records per-model counts and warns loudly for `--plain`. New `restore_finance.py` detects encrypted/plain files, requires the correct key, remaps institution slugs/FKs, defaults to dry-run, requires `--confirm`, and skips existing primary keys so retries are safe. `prune_finance.py` is scoped per institution and prints counted results. Added `finance/tests/test_backup_prune.py` covering coverage, encrypted round-trip, wrong key, idempotent restore, dry-run writes nothing, and tenant-scoped pruning. |
| **Human/external decisions** | Define RPO/RTO, retention schedule, offsite target, key custody/rotation and escrow; schedule `backup_finance`/`prune_finance`; name a restore-drill owner and run one on staging before go-live. |

## 3. Object storage

| Item | Assessment |
| --- | --- |
| **Existing functionality** | `STORAGE_BACKEND` supports S3-compatible providers (`s3`, `r2`, `b2`, `supabase`, `object`) via `django-storages`; signed URLs, immutable object cache headers, a custom domain/prefix and startup validation are implemented in `config/settings.py`. Production boot fails loudly if the selected backend has no bucket/keys/endpoint. |
| **Gaps** | No provider, bucket or credentials are provisioned in this repository; the fallback is local disk, which is not durable on an ephemeral host. Bucket versioning, server-side encryption, lifecycle rules, CORS and CDN behaviour cannot be verified from code. No upload/download restore test exists. |
| **Severity** | **high** — a **blocker** for any pilot that accepts logos, photos or documents. |
| **Changes made** | The new `/status/` page reports local disk as **Not configured** rather than implying uploads are safe. Existing startup validation was left intact. |
| **Human/external decisions** | Choose the provider and bucket; supply credentials and endpoint; enable versioning and encryption at rest; set CORS/lifecycle rules; decide whether a custom domain/CDN is required; run an upload/download drill. |

## 4. Email deliverability and every email type

| Item | Assessment |
| --- | --- |
| **Existing functionality** | All mail flows through `core/notifications.py` → Django backend/django-anymail. `core/email_types.py` defines 36 canonical codes; `DEPLOY.md` mirrors them. `core/checks.py` emits `E001`–`E006` in production for SMTP/console backends or missing provider keys and `W001` in development. `OutboundMessage` logs every attempt with retries, suppression, status and sanitised provider detail. Resend and Mailjet webhooks feed `EmailEvent` and `EmailSuppression`. The Email health page, `check_email` command and source-scan tests prevent ad-hoc `send_mail` calls. |
| **Gaps** | Reachability audit found **seven registered codes with no real caller**: `signup_link`, `billing_reminder`, `suspension_warning`, `results_notification`, `library_overdue`, `security_alert`, `other`; `admin_error` has a helper but no callers. Many messages use inline plain text plus a generic HTML shell. Bulk newsletters have no unsubscribe link. ESP webhook secrets are optional and empty by default, so bounces are logged but not applied. No Brevo webhook exists. SPF/DKIM/DMARC are documented in `DEPLOY.md` but not verified automatically. No live-inbox test or per-type delivery SLA is recorded. Message bodies are retained without a documented policy. |
| **Severity** | **high** (missing real senders for billing/suspension/security/results, webhook secrets, live verification); **medium** (template quality, unsubscribe, retention). |
| **Changes made** | SMS and WhatsApp stubs now return `ok=False` and mark messages `FAILED` instead of pretending success (`core/notifications.py`, `core/tests/test_notifications_audit.py`). Support details are configurable and appear on error pages. The status page says email is *Not configured* when a console/locmem backend is active. |
| **Human/external decisions** | Choose ESP and sending domain; add SPF/DKIM/DMARC and confirm ESP verification; set webhook secrets/triggers and suppression thresholds; approve wording and unsubscribe policy for every template; decide which of the seven unused codes must become real notifications before pilot; perform a live send/receive check for each reachable type. |

## 5. Security: tenancy, permissions, rate limits, password, 2FA, secrets and dependencies

| Sub-area | Existing | Gaps / severity | Changes / decisions |
| --- | --- | --- | --- |
| **Tenant matrix** | `TenantManager`, `TenantModel`, contextvar-scoped `CurrentInstitutionId`, `InstitutionMiddleware`, API-key tenant scoping, consent middleware and existing tenancy tests. | **high** before this audit: ID-bearing routes were only spot-checked. **medium** remaining: `is_superuser` still short-circuits code checks; permission cache can be stale for 300s; token routes and webhook/API-key models require manual scoping. | Added `core/tests/test_tenant_matrix.py`: walks every registered ID-bearing web route (>100 checks) plus 13 API detail routes, substitutes foreign-institution IDs and asserts no marker leaks. **Decision:** confirm the intended superuser model and revocation latency. |
| **Permission enforcement** | `core/permissions.py` catalogue, data-driven roles, deny-by-default decorator and audit logging. | **blocker** before this audit: `permission_required_code` did not require login, so an anonymous request could reach a view whose code check was empty/legacy. | `core/access.py` now applies `@login_required` to every code-protected view; `accounts/tests/test_permissions.py` covers the anonymous case. |
| **Rate limiting** | Email verification/reset/IP/bulk limits, registration/signup-link/application/demo-form limits, DRF `ScopedRateThrottle` (`api: 120/min`, `api-write: 30/min`). | **high**: only 5 API views declare `throttle_scope`; TOTP verification and reauth are unthrottled; IP keys trust the first `X-Forwarded-For` value unless the proxy strips it. | **Decisions:** apply scopes to all API views; add TOTP/reauth throttles; verify proxy header handling with the host. |
| **Password and 2FA** | Validators (min 10, similarity, common, numeric), forced password change, 8-strike/15-minute login lockout, TOTP for platform staff with 30s step and 15-minute reauth. | **high**: 2FA is platform-staff only; disabling 2FA requires no code/password; no TOTP brute-force throttle. **medium**: no password age/history/reuse rule; no IP-wide throttle/CAPTCHA. | **Decisions:** extend 2FA to institution admins/finance/sensitive-data roles, require confirmation to disable, throttle attempts, and confirm password-age policy. |
| **Secrets history** | `.env` is gitignored; real env vars win; `SECRET_KEY` is generated by Render; API keys are SHA-256 hashed, shown once and revocable; webhook secret has a rotate action; deployment docs describe manual rotation. No committed `.env` or literal `PLATFORM_ADMIN_PASSWORD` was found in git history. | **high**: DEBUG fail-open outside Render (see §1). **medium**: no secret manager, versioning or global rotation; TOTP and webhook secrets are plaintext at rest; no API-key rotate action. | **Decisions:** confirm secret manager/custody and whether plaintext TOTP/webhook secrets are acceptable; add rotation runbook ownership. |
| **Dependency vulnerabilities** | All runtime packages are pinned in `requirements.txt`. | **blocker**: published advisories affect `Django==5.1.4` (multiple SQL-injection/DoS fixes; 5.1 is past upstream support), `djangorestframework==3.15.2` (DoS/token-revocation/disclosure fixes) and `Pillow==11.0.0` (many image-decoder fixes). **medium**: no automated dependency scanning in CI. | **Deliberate decision:** upgrades were **not** applied in this pass because they need a regression window and an owner. Upgrade to a supported Django LTS, current DRF and current Pillow, then run the full suite and staging checks. Add `pip-audit`/Dependabot or equivalent. |

## 6. Data protection

| Item | Assessment |
| --- | --- |
| **Existing functionality** | Password and API-key hashing; Fernet-encrypted finance backups; sanitised provider responses; slow-query logging that records SQL text but never parameters; error records that store only trusted metadata; sensitive-view logs; `DataAccessGrant`, `AccessRequest`, `DataConsentLog`; append-only `PlatformAuditLog` with hash-chain verification; per-institution six-year `MoneyLog` pruning. |
| **Gaps** | No DSAR implementation: `DATA_EXPORT` and `DELETION` request kinds exist but have no export/anonymise/erase workflow. TOTP and webhook secrets are plaintext at rest. `--plain` backups are unencrypted. Message bodies in `OutboundMessage` have no retention rule. Bucket encryption is unverified. |
| **Severity** | **high** (DSAR, secret storage, unencrypted backup option); **medium** (message retention, bucket encryption). |
| **Changes made** | Added parameter-free slow-SQL logging (`core/db_monitor.py`); status page exposes configuration state but no tenant data or message content; backup `--plain` now emits an explicit warning. |
| **Human/external decisions** | Determine applicable data-protection law and DSAR/erasure obligations; define retention for email bodies and audit logs; approve where secrets live; verify provider-side encryption. |

## 7. Money accuracy and hand-calculated tests

| Item | Assessment |
| --- | --- |
| **Existing functionality** | `Decimal` money fields throughout `finance`; all movements post balanced journal entries through `finance/services.py`; gap-free document numbering with locks; dated `TaxRate`; reports and trial balance. Hardcoded hand-calculated expectations exist in `finance/tests/test_tax_and_numbering.py`, `test_waivers.py`, `test_reports.py`, `test_payments.py`. New tests use explicit amounts such as 100.00 paid, 60.00 + 50.00 attempted refunds, leaving 40.00. |
| **Gaps** | Rounding/scale and chart-of-accounts mapping are not documented as an accounting policy; no property/fuzz tests for rounding boundaries; `Refund.clean()` still only counts approved refunds (the service layer now compensates, but direct DB writes remain a risk). |
| **Severity** | **low** for core arithmetic; **medium** for undocumented accounting policy. |
| **Changes made** | `request_refund()` counts **pending + approved** refunds under a payment lock; `approve_refund()` re-locks the payment, rechecks the remaining balance and uses a conditional status update; platform invoice payments reject zero and overpayments and the form now renders those errors. |
| **Human/external decisions** | Confirm currency decimals, rounding mode, VAT basis and chart-of-accounts mapping with the finance owner; define refund approval matrix/SLA. |

## 8. Platform billing and suspension E2E

| Item | Assessment |
| --- | --- |
| **Existing functionality** | `Plan`, `Subscription`, `PlatformInvoice`, `PlatformPayment`; manual invoice creation and payment recording; overpayment guard; lifecycle suspend/reinstate/archive/restore/delete in `platform_admin/lifecycle.py`; suspended institutions are redirected to `/billing/contact/`; tests in `platform_admin/tests/test_platform_administration.py`. |
| **Gaps** | No automatic invoice generation from plans/subscriptions, no `PAST_DUE` state, no dunning, no automatic suspension, no invoice PDF/email, no credit notes or platform refunds, and no single E2E test covering subscribe → invoice → payment → suspension → reinstatement with real data. |
| **Severity** | **high** — viable manually, but not an automated production billing cycle. |
| **Changes made** | Zero/negative and overpayments are rejected with a field error; `templates/platform_admin/invoice_detail.html` displays form errors; regression tests added. |
| **Human/external decisions** | Pricing cycles, invoice timing, dunning schedule, suspension/appeal policy, VAT treatment of platform fees, and whether billing is manual for the pilot. |

## 9. Online payment and webhook idempotency

| Item | Assessment |
| --- | --- |
| **Existing functionality** | Paynow gateway in `finance/gateways.py`: SHA512 hash, `hmac.compare_digest`, CSRF-exempt webhook, globally unique references, cross-tenant lookup with `all_objects`, `select_for_update` row locks, atomic completion, currency-mismatch rejection and settled-invoice rejection. `finance/tests/test_production_hardening.py` covers signed callbacks, duplicate webhooks (exactly one `Payment`), cross-tenant callbacks, unique references and currency mismatch. |
| **Gaps** | The webhook does **not** validate the payload `amount` against the stored transaction/invoice; `exchange_rate` is hardcoded to `1`; transaction currency comes from `institution.currency` rather than `invoice.currency`; there is no HTTP-level test for invalid signature, tampered amount, true concurrent callbacks or live Paynow behaviour; `PAYNOW_MODE` defaults to `test`. |
| **Severity** | **high**; live validation is a **blocker**. |
| **Changes made** | All hardened behaviours listed above were added in this audit, plus a unique constraint migration (`finance/migrations/0017_production_hardening.py`) and a data migration to dedupe old references. The gateway factory is now looked up through the `finance.gateways` module at call time, so tests and runtime cannot bind a stale or mock gateway when Django lazily imports the views module. |
| **Human/external decisions** | Provide live credentials and confirm callback format/guarantees; decide FX/base-currency policy; approve whether payload amount validation should use major/minor units before it is added. |

## 10. Notification channels

| Item | Assessment |
| --- | --- |
| **Existing functionality** | In-app notifications, email via Anymail, `OutboundMessage` tracking for all channels, retry/backoff, suppression, a resend UI and SMS/WhatsApp channel enums/provider hooks. |
| **Gaps** | SMS and WhatsApp are stubs and cannot deliver. `absence_notification` and `fee_reminder` queue SMS that will now be visibly `FAILED`. No push or digest channel. No worker is provisioned to drain queued retries automatically. No per-channel delivery SLA. |
| **Severity** | **high** if guardians rely on SMS/WhatsApp; **medium** otherwise. |
| **Changes made** | Stubs return `ok=False` with an explicit "not configured" detail and messages are marked `FAILED`; audit tests updated so dashboards no longer claim delivery. |
| **Human/external decisions** | Choose/purchase SMS and WhatsApp Business providers, sender numbers, approved templates, opt-in/consent handling and carrier rules; decide whether those channels are in the pilot scope. |

## 11. Bulk import with messy sample

| Item | Assessment |
| --- | --- |
| **Existing functionality** | Four-step import (upload → map → validate → write) with aliases, preview, downloadable error report, tenant scoping, row/byte limits and templates for students, bulk students, staff, courses, subjects and fee structures. |
| **Gaps** | No automated run against a full 5,000-row production-shaped file; field mapping still needs a human check per school; undo is a manual pilot step. |
| **Severity** | **medium** — the corruption bug below was **high** while present. |
| **Changes made** | Duplicate and blank header cells no longer shift values into the wrong columns; student numbers, employee numbers, usernames and staff emails are cached during validation instead of re-queried per row; `User.all_objects` misuse was corrected to `User.objects`; `max_bytes()` is now used by `core/import_views.py`; added `core/samples/messy_students.csv` (duplicate header, blank cell, stray column, quoted notes) and `core/tests/test_import.py` coverage. |
| **Human/external decisions** | Confirm the school's column mapping and data-cleanup owner; run one small import and undo before the full pilot import. |

## 12. Performance and load testing

| Item | Assessment |
| --- | --- |
| **Existing functionality** | Caching (Redis or locmem), gzip, ETags, WhiteNoise, slow-request/slow-SQL logs, single aggregate status query, and a dependency-light load script. |
| **Gaps** | No production-shaped test (multi-worker gunicorn + PostgreSQL), no authenticated tenant-page test with realistic data volumes, no SLOs and no external uptime monitor. |
| **Severity** | **medium**. |
| **Changes made** | Added `scripts/load_test.py`, `docs/LOAD_TEST_RESULTS.md` and raw JSON results. Final clean runs: 200 requests at 10 users → 6.091s, 32.8 RPS, 0 errors, p95 355.1ms; 1000 requests at 25 users → 22.697s, 44.1 RPS, 0 errors, p95 1204.8ms. A noisy run taken while two servers competed for port 8000 was discarded. |
| **Human/external decisions** | Provide staging capacity, define SLOs (e.g. authenticated p95 < 500ms) and choose an external monitoring provider. |

## 13. Error handling and System status page

| Item | Assessment |
| --- | --- |
| **Existing functionality** | `core/errors.py` + `error_catalogue.py` classify every failure with a request ID, category, stable code and reference; HTML/JSON error pages; platform Errors page; `ORG-SUSPENDED` billing flow; `/health/` liveness. |
| **Gaps** | The status page does not yet check Celery worker/beat, webhook backlog, backup freshness or Paynow/FDMS connectivity. There is no uptime history or release marker. |
| **Severity** | **medium**. |
| **Changes made** | Added `core/status.py`, `/status/`, `templates/status.html`, `templates/status_detail.html`, status/help links in the sidebar and error pages, and 4 tests in `core/tests/test_status.py`. The page reports **Operational / Degraded / Outage / Not configured** honestly and never exposes tenant data. |
| **Human/external decisions** | Decide which additional components should be public and who owns status-page updates. |

## 14. Monitoring and alerts

| Item | Assessment |
| --- | --- |
| **Existing functionality** | Structured logs (`edusphere.errors`, `edusphere.slow`, `edusphere.uploads`), per-request timing and error references, platform Errors page, `/health/`, optional Sentry with `send_default_pii=False`. |
| **Gaps** | No DSN or alert rules in `render.yaml`, no dashboards, no log retention policy, no external uptime check, no on-call rota, no worker/beat monitoring. |
| **Severity** | **medium** — **high** if the pilot handles live money. |
| **Changes made** | Added parameter-free slow-SQL logging in `core/db_monitor.py`, pinned `sentry-sdk[django]==2.75.0`, and made configuration state visible on `/status/`. |
| **Human/external decisions** | Create the Sentry project, set `SENTRY_DSN`/environment/traces rate, define alerts and escalation, choose log retention and an external uptime monitor. |

## 15. Documentation and in-app help for every role

| Item | Assessment |
| --- | --- |
| **Existing functionality** | `docs/ERROR_CODES.md`, `DEPLOY.md`, `MANUAL_TESTING.md`, `README.md`. |
| **Gaps** | Before this audit there was no single role-by-role guide or in-app help entry point. No translations or training-attendance record exist. |
| **Severity** | **low**. |
| **Changes made** | Added `core/help.py`, `/help/`, `templates/help.html`, `templates/help_detail.html`, sidebar links for every authenticated role, `docs/ROLE_GUIDES.md` and 7 tests in `core/tests/test_help.py`. Guides cover platform admin, institution admin, registrar, bursar/accounts, teacher, student, parent/guardian and other staff. |
| **Human/external decisions** | Approve the wording, add localised versions if required, and assign training ownership. |

## 16. Pilot onboarding checklist

| Item | Assessment |
| --- | --- |
| **Existing functionality** | Platform institution/application flows and onboarding tooling already existed. |
| **Gaps** | No standard, owner-assigned go-live checklist existed. |
| **Severity** | **medium** until it has been executed and signed off. |
| **Changes made** | Added `docs/PILOT_ONBOARDING.md`: 9 stages (approval, first admin/2FA, institution basics, roles, import, finance, tenant spot-check, notifications/help, go-live gate) plus a first-7-days review. Each stage has an owner and a definition of done, including a backup restore dry-run and a full test-suite gate. |
| **Human/external decisions** | Assign named owners, execute the checklist for the real pilot, record sign-off in the pilot ticket and complete the 7-day review. |

## 17. Pilot freeze, E2E scenario and school-facing limitations

| Item | Assessment |
| --- | --- |
| **Existing functionality** | Institution onboarding, imports, finance, academics, results, PDF verification, error references and platform Inbox already existed. |
| **Gaps** | Before this pass there was no single frozen pilot scope, no in-app feedback path for institution users, no automated journey covering the whole school workflow, and no plain-English limitations note that could be shown to the school without exposing internal caveats. |
| **Severity** | **medium** — the pilot cannot be honestly signed off without these artefacts. |
| **Changes made** | Added `PILOT_PLAN.md` (exact in/out scope, 14-day plan, messy sample files, per-role checklists, success measures), `PILOT_LIMITATIONS.md`, `core/tests/test_pilot_scenario.py`, `platform_admin/tests/test_pilot_feedback.py`, `core/samples/pilot_students_messy.csv`, `core/samples/pilot_fee_structures_messy.csv`, and the `feedback` request kind with migration `platform_admin/0008_alter_platformrequest_kind.py`. The feedback button uses `reverse("platform_admin:pilot_feedback")`, `@login_required`, a validated local page path and an `ERR-[0-9A-F]{6}` reference; it writes to the platform Inbox and notifies platform staff. Error pages propagate references through `support_context` and the feedback link. Documentation and checklists were updated in `README.md`, `docs/PILOT_ONBOARDING.md`, `docs/ROLE_GUIDES.md` and `MANUAL_TESTING.md`. |
| **Decision** | The pilot is **Core academics + Finance and fees only**. Live Paynow, ZIMRA FDMS, SMS/WhatsApp, worker/cron jobs, DSAR and optional modules are explicitly out of scope. The frozen acceptance path is `python -m pytest core/tests/test_pilot_scenario.py -q`; any failure blocks the pilot. |

---

## Verification evidence

| Check | Result |
| --- | --- |
| Baseline `python -m ruff check .` | Clean. |
| Baseline `python manage.py makemigrations --check --dry-run` | `No changes detected` (with the expected `core.email.W001` development-backend warning). |
| Baseline `python -m pytest -q` | **1669 passed, 221 warnings in 519.30s**. |
| Final `python -m ruff check .` | `All checks passed!` |
| Final `python manage.py makemigrations --check --dry-run` | `No changes detected` (same `core.email.W001` warning). |
| Final `python -m pytest -q > pytest-final.log 2>&1` | **1701 passed, 221 warnings in 515.56s**. |
| Targeted status/help/health tests | `13 passed`. |
| Targeted backup/platform fixes | `6 passed`. |
| Pilot baseline before changes | **1807 passed, 221 warnings in 624.15s**. |
| Pilot targeted tests (scenario, feedback, fee sample) | **13 passed** (1 scenario + 8 feedback + 4 fee-sample import tests). |
| Final pilot `python -m ruff check .` | `All checks passed!` |
| Final pilot `python manage.py makemigrations --check --dry-run` | `No changes detected` (same `core.email.W001` warning). |
| Final pilot `python manage.py check` | One expected `core.email.W001` console-backend warning. |
| Final pilot `python -m pytest -q` | **1817 passed, 221 warnings in 675.92s**. |
| Load test | Two clean local runs, 0 errors; see [docs/LOAD_TEST_RESULTS.md](docs/LOAD_TEST_RESULTS.md) and `pytest-load-results*.json`. |

The `core.email.W001` warning is expected for local development and tests: the console backend does not deliver mail. Production is blocked by `core.email.E001`–`E006` if SMTP/console is selected or required provider keys are missing.

---

## Ranked top-10 pre-go-live actions

1. **blocker** — Upgrade `Django`, `djangorestframework` and `Pillow` to supported, patched releases; add automated dependency scanning and rerun the full suite/staging checks.
2. **high / blocker if automated jobs are in scope** — Provision the Celery worker/beat or cron service and verify that queued mail retries, fee reminders, webhook delivery, fiscalisation retries, backups and pruning actually run.
3. **high / blocker if uploads are in scope** — Provision object storage, enable encryption/versioning, and run an upload/download plus encrypted-backup restore drill on staging with a named owner.
4. **high** — Complete the email deliverability matrix: ESP/domain/SPF/DKIM/DMARC, API keys, webhook secrets, live send/receive for every reachable type, and a decision on the seven registered-but-unused codes.
5. **high** — Close the security gaps: explicit `DEBUG=false` on every host, extend 2FA beyond platform staff, require confirmation to disable it, throttle TOTP/reauth, apply `throttle_scope` to all API views, review superuser bypass and proxy `X-Forwarded-For` handling.
6. **high / blocker for live money** — Validate Paynow end to end in live mode, confirm callback amount units and FX/base-currency policy, and add the missing tampered-amount/invalid-signature/concurrency tests.
7. **high** — Run a full platform billing/suspension E2E and decide pricing cycles, dunning, automatic vs manual suspension, VAT treatment and invoice/credit-note delivery.
8. **blocker if Zimbabwe fiscalisation is in scope; high otherwise** — Confirm ZIMRA FDMS access and real gateway request/response shapes, and settle tax-rate, VAT-basis and FX policy with legal/finance.
9. **high** — Resolve data-protection decisions: DSAR/export/erasure obligations, message-body and audit retention, plaintext TOTP/webhook-secret acceptance, and removal/protection of `--plain` backups in production.
10. **medium → high at scale** — Run a production-shaped load test with authenticated tenant pages, set SLOs, and turn on Sentry, alert routing, external uptime monitoring and log retention with a named on-call owner.

The final gate is the checklist in [docs/PILOT_ONBOARDING.md](docs/PILOT_ONBOARDING.md) and the measures in [PILOT_PLAN.md](PILOT_PLAN.md#5-success-measures): no *Outage* on `/status/`, green full suite and end-to-end pilot scenario on the deployed commit, a completed restore dry-run, a named rollback owner, school acknowledgement of [PILOT_LIMITATIONS.md](PILOT_LIMITATIONS.md) and a signed pilot ticket.
