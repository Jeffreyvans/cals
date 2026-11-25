# Vanpere pilot: what we can and cannot promise

This is the plain-English note to show the school before sign-off. It is not a
sales page. It says what the one-school pilot covers, what we still do not know,
and what the school must not rely on. The technical detail and owners are in
[PRODUCTION_READINESS.md](PRODUCTION_READINESS.md); the exact pilot journey is
in [PILOT_PLAN.md](PILOT_PLAN.md).

---

## What is ready for this pilot

We have tested one complete journey from an empty school to documents:

1. A platform administrator creates the school and its first administrator.
2. The administrator signs in, changes the temporary password and finishes setup.
3. The school sets a year, term, class, programme and subject.
4. A registrar imports students from a deliberately messy CSV and reviews the rejected row.
5. Students are enrolled in a subject.
6. A bursar creates a fee, generates invoices, records a cash payment and issues a receipt.
7. A teacher records attendance and marks and submits the result sheet.
8. A registrar moderates, approves and releases the results.
9. A report card and receipt open as PDFs and can be checked on a public verification page.
10. Any signed-in user can press **Pilot feedback** to send a short report to the platform team.

That journey is covered by an automated test that fails on any broken screen,
wrong value or missing PDF. The feature set is frozen; we are not adding new
features during the pilot.

---

## What the school should not rely on

### 1. This is one school, not a multi-school service yet

The pilot is for one institution. The code separates schools, and we spot-check
that another school's records cannot be reached, but the platform is still
being operated by a small team. Do not give platform administrator or
superuser access to people who only need school access. A full superuser can
still bypass some code-level permission checks, so that account must be named,
logged and controlled.

### 2. Platform staff are not expected to browse school records

The platform screens do not show student lists, health records, counselling
notes, results or school finance. Access to protected data needs an explicit,
time-limited grant. The remaining superuser bypass above is the reason we do
not treat “platform staff cannot see data” as an absolute guarantee yet.

### 3. SMS and WhatsApp do not send

The channels exist in the database, but no SMS or WhatsApp provider is
configured. A queued SMS or WhatsApp message will show as failed. Use email and
in-app notifications for the pilot, or agree a separate provider first. Do not
promise parents an SMS reminder.

### 4. Email delivery depends on configuration

Email is not guaranteed in a local or default setup. The status page reports
email as *Not configured* when a console or local backend is used. Before
go-live, the school must send a real test email to a real mailbox and confirm
delivery, SPF/DKIM/DMARC and bounce handling. The welcome email and password
reset flows need this to work.

### 5. Online payments and ZIMRA fiscalisation are not live-tested here

The manual cash/bank payment, receipt and ledger flow is in the pilot. The
Paynow gateway code exists but has not been proven against a live provider, and
the callback amount and exchange-rate rules still need a human decision. The
ZIMRA FDMS gateway follows the documented API but has no live device, endpoint
or API key in this repository. For the pilot, record manually issued receipts
and do not assume online payment or fiscal submission is automatic.

### 6. Backups and restore are commands, not a service

An encrypted finance backup command and a restore dry-run command exist. There
is no automatic schedule, offsite copy, key escrow or proven restore in the
repository. A named person must run the backup, copy it somewhere safe and
complete a restore dry-run on staging before sign-off. Until then, treat
backups as a manual responsibility.

### 7. Uploads need durable storage

If object storage is not configured, uploaded logos, photos and documents go
to local disk. On an ephemeral hosting service those files can be lost on
restart. The status page will say storage is *Not configured*. Do not upload
irreplaceable documents until durable storage is provisioned and an
upload/download test has been done.

### 8. Imports are checked by people, not trusted blindly

The four-step import shows a mapping, a preview and a row error report before
anything is written. It handles duplicate and blank headers without shifting
values into the wrong fields. It does **not** decide which source column is
correct for your school, and it does not clean names, dates or codes for you.
Check the mapping and the rejects every time. Undo is a manual step, and a
batch that has already created invoices, marks or attendance may not be fully
undoable. Files are limited to 5 MB and 5,000 rows.

### 9. Money figures still need a hand check

Amounts use decimal arithmetic and each movement posts to a ledger, but the
rounding, tax basis, currency decimals and chart-of-accounts mapping must be
confirmed with the school's finance owner. Do a paper calculation for the fee
structure, invoice, payment and balance before trusting a report. Direct edits
to the database bypass the safety checks; all corrections must go through the
approved payment, waiver, refund or journal screens.

### 10. Results become visible only after release

Marks are private until the registrar releases the result sheet. A fee balance
can withhold results. If something is wrong after release, use the school's
correction process; do not quietly edit the released record. The automated
pilot test proves the submit → moderate → approve → release order works, but it
does not prove every school grading policy.

### 11. Student and guardian portals are not part of sign-off

The portals exist, but the pilot acceptance path does not depend on them. If
they are enabled, access is limited to linked children and released
information. Treat them as a demonstration, not a promised channel, until the
school has tested its own accounts and permissions.

### 12. Security controls have known gaps

Passwords have length and common-password checks, login lockout exists, and
platform staff use 2FA. Institution users do not have 2FA yet, there is no
password-age or reuse rule, and some API and login routes have weaker rate
limits than we would like. Use unique strong passwords, do not share accounts,
and keep the number of privileged users small. The remaining items are listed
in `PRODUCTION_READINESS.md`.

### 13. Feedback is not an emergency channel

The **Pilot feedback** button asks for the page, a short message and, when
there was an error, an `ERR-XXXXXX` reference. It sends the report to the
platform Inbox and notifies platform staff. Please leave out names, student
numbers, health details and other personal information. At most five reports
per user per hour are accepted. For urgent help, use the phone and email shown
on the error page or contact the school office directly.

### 14. Monitoring is basic

There is a status page and `Operational / Degraded / Outage / Not configured`
reporting, but it does not yet watch background workers, backup freshness,
payment gateways or uptime history. Sentry, alert routing, an external uptime
check and a log-retention policy are decisions still to be made. A green status
page means “these checks passed”, not “everything about the service is
guaranteed”.

### 15. Some work depends on a scheduler that is not included

Queued email retries, fee reminders, webhook delivery retries, fiscalisation
retries, finance jobs, backups and pruning need a worker or scheduled task.
Nothing in this repository runs them automatically on a host. Decide who will
run them and how before the school relies on them.

### 16. Performance limits are only locally measured

A small local load test ran without errors, but it was not a production-shaped
test with many workers, PostgreSQL and realistic school data volumes. There is
no agreed response-time target yet. Keep the first pilot small and watch slow
pages rather than assuming the system will scale to the whole school on day
one.

### 17. Dependency upgrades are outstanding

Some pinned libraries have published security advisories and one is past
upstream support. The pilot should run in an isolated deployment with an owner
for upgrades. Upgrading and re-running the full suite is the first hardening
task after the pilot.

### 18. Data requests and retention are not automated

There is no self-service export, anonymisation or erasure workflow for a data
access request. Email bodies and audit logs do not yet have an agreed retention
period. Do not load more personal data than the pilot needs, and route formal
data requests to a person, not the system.

### 19. The guides are in English

The role guides, error messages and this note are English only. If the school
needs another language, plan for a human translation and training record; the
system does not translate them automatically.

---

## What we will do during the pilot to reduce these risks

- Enable only **Core academics** and **Finance and fees** for the school.
- Run the automated end-to-end scenario and the full test suite on the deployed commit.
- Use a named platform administrator, separate temporary-password delivery and
  a recorded support contact.
- Check money by hand and keep the import row reports and PDF downloads.
- Review the platform **Errors** page, feedback Inbox and status page every day
  in the first week.
- Give every repeated error or money/results support request an owner and a
  written decision by day 14.

## When we would stop or pause

Pause the pilot if any of these happens:

- a payment, receipt or balance cannot be reconciled by hand;
- one school's record appears in another school's workspace;
- student, health or counselling data is exposed to someone without a grant;
- a result is visible before release or changes without an audit record;
- email or storage is reported as configured but a real send/upload fails;
- the deployed commit fails the full test suite or the end-to-end scenario.

A pause is not a failure of the school. It means we fix the problem before
more real data is entered.
