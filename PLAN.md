# EduSphere - build plan

Status legend: `[ ]` not started, `[~]` in progress, `[x]` done, `[!]` deliberately deferred.

Read `PROGRESS.md` for the dated log, decisions and known gaps.

---

## PHASE 0 - Stabilise and baseline
- [x] Read the whole project; run makemigrations/migrate/seed/runserver; fix errors
- [x] pytest config, factories for every model, Makefile (`make test/run/lint`), ruff + black, pre-commit
- [x] Baseline tests: cross-student access denied, overpayment rejected, balance = billed - paid - waived, receipt numbers unique per institution, student number generation
- [x] PLAN.md and PROGRESS.md created; git repo initialised, baseline committed

## PHASE 1 - Foundation (tenancy, roles, workflow, notifications, audit)
- [x] Multi-tenancy: `Institution`, `TenantModel`/`TenantManager`, middleware, per-institution uniqueness, isolation tests (models, views, search, exports)
- [x] Institution types + `Terminology` labels per type (type presets + per-institution override on the institution screen)
- [x] Feature flags (`InstitutionModule`) hiding disabled modules from menus, URLs and views
- [x] Roles and permissions as data: `Role`, `Permission`, `RolePermission`, `permission_required_code` decorator, `perms_code` template tag, permission-matrix admin screen, seeded default role matrix
- [x] Departments: `Department`, `StaffProfile` (screens for both, hierarchical departments)
- [x] Approval workflow engine: templates, requests, actions; submit/approve/reject/return services; notifications at each step
- [x] Notification system: `Notification`, `MessageTemplate`, `OutboundMessage`, provider interface (console/email/SMS stubs), Celery tasks with eager fallback, bell + notifications page
- [x] Audit log: `AuditEntry` (finance, results, users, roles) + `SensitiveViewLog`; read-only screen with filters and CSV export
- [x] Setup wizard: details, branding, year/term, modules, grading template, currency, first admin, optional demo data
- [x] Bulk import (CSV/Excel): upload, column mapping, validation preview, confirm, error report
- [x] Administrator user management (Part 2B): tenant-scoped Users area with search/filters, direct add staff and student, invitation or one-time temporary password, forced first change, account locking and unlock, parent linking, duplicate flagging/merging, safe deactivation/reactivation and role changes, plus access-mode-aware bulk import, one-time credential downloads and undo. Registrar add-student and HR add-staff are permission-gated.

Phase 1 status (2026-10-22): complete. Tenancy, roles, workflow, notifications, audit,
the setup wizard and the bulk import are all in place, with 440 tests passing.
Part 2B (2026-11-12) adds the administrator Users area and access-mode-aware bulk
import; the full suite is now 929 tests passing.

## PHASE 2 - Academics
- [~] AcademicYear/Term hardening (only one active per institution), GradeLevel, SchoolClass/Stream, Subject, ClassSubjectAllocation, class teacher, promotion tool
- [x] College mode: ProgrammeYear, CourseOffering, Cohort, prerequisites, credit limits, capacity - models, management screens, registration service/screen and demo offerings are in place
- [x] Timetable clash detection: lecturer, class/stream, course and venue overlap; views by class, teacher and student
- [~] Optional timetable hardening: printable PDF done (registrar with class/teacher filters, plus teacher and student copies); dedicated Room/PeriodDefinition models remain optional
- [x] Course registration with open/close dates, credit limits, prerequisite + capacity checks, waiting list, registrar override with a reason, audited drop/promotion
- [~] Assessment: AssessmentComponent weights and weighted final mark done; per-level GradingScale/GradeBand, AssessmentResult and the full calculation remain
- [x] Results engine: credit-weighted term average, cumulative GPA and class positions with ties on the report card, plus configurable award classification from cumulative GPA with built-in defaults
- [x] Marks workflow: enter -> moderate -> approve -> release; mark sheets lock after moderation, students/parents see released results only, fee balances withhold results and an audited bursar clearance releases one term with a written reason
- [x] Report cards + transcripts (PDF, branding, QR verification page)
- [x] Attendance: register with present/late/absent/excused, absence reasons, per-term summaries, parent notification once per absence (in-app plus queued email/SMS)
- [x] Academic calendar events (holidays, exams, meetings, deadlines) on the staff, student, parent and lecturer dashboards, optionally limited to one class
- [x] Data migration of existing Programme/Course/Enrollment/Attendance data; seed command still works

## PHASE 3 - Finance (institutional grade)
- [x] Chart of accounts + double-entry ledger (Account, JournalEntry, JournalLine, balancing rule, service layer)
- [x] Financial year + period close that locks posted periods
- [x] Fee structures with FeeItem lines; bulk invoice generation with preview
- [x] Adjustments: discounts, bursaries, scholarships, staff-child discounts, waiver approvals
- [x] Sponsors with sponsor statements
- [x] Instalment plans, overdue detection, late penalties (capped; `run_finance_jobs` for scheduled runs)
- [x] Payments: methods incl. EcoCash/mobile money, partial payments, credit balances, sequential receipts with QR
- [x] Reversals and refunds through approval workflow (reversing entries, never deletes)
- [x] Payment gateway: `PaymentGateway` base, Paynow implementation (initiate/poll/webhook signature/idempotency), online payment from portal
- [x] Multi-currency (USD/ZiG) with a dated `ExchangeRate` table; currency, amount and exchange rate stored on every invoice, payment and journal line, and reports in the base currency
- [x] Tax category on every fee item (exempt, standard-rated, zero-rated) and a dated `TaxRate` table replacing the hard-coded 15.5%; the rate in force is stored on each invoice line
- [x] Per-institution VAT registration: VAT status, VAT number, ZIMRA BP number and registered address
- [x] Gap-free sequential numbering per institution, document type and financial year for invoices, receipts, credit and debit notes (atomic allocation inside the transaction)
- [x] Issued documents are immutable: edits and deletes are refused, corrections go through approved credit/debit notes or reversing entries, and every issue/reversal is audited
- [x] Switchable `fiscalisation` module for ZIMRA FDMS (mock gateway first; endpoint and credentials in env vars), storing device, fiscal day, verification code, QR data and the response per document, with day open/close and Celery-optional retry
- [x] Six-year retention that blocks hard deletion, plus an encrypted (Fernet) finance backup command and a controlled past-window prune command
- [x] Accountant reports: trial balance, income statement, balance sheet, VAT output-vs-input summary, debtors ageing and cash-up, all exportable to Excel/CSV/PDF
- [x] Statements (PDF), debtors ageing, daily cash-up, collection vs target, income by fee item, refunds report, CSV/Excel/PDF export
- [x] Expenses, suppliers, department budgets and variance
- [x] Bank reconciliation import/match
- [x] `student_balance(student)` single service used everywhere, tested

Phase 3 status (2026-11-13): complete, including the Zimbabwe tax and
record-keeping additions (VAT registration, dated rates, gap-free numbering,
immutable documents, fiscalisation, six-year retention/backup and the VAT
summary). The ZIMRA FDMS gateway follows the documented receipt API but has not
been exercised live: the production endpoint, device ID/serial and API key are
still outstanding. Verification: 964 tests pass, `ruff check .` is clean and
`makemigrations --check` reports no changes.

## PHASE 4 - Admissions and student lifecycle
- [x] Public application form (no login) with honeypot, rate limit, tracking link
- [x] Review workflow states, entry-requirement eligibility check, interview scheduling
- [x] Offer letter PDF, acceptance + deposit -> atomic student creation (login, number, invoice, welcome message)
- [x] Lifecycle states with transition services (defer, suspend, transfer, withdraw, graduate) + audit
- [x] Graduation clearance checklist; certificates PDF with serial + public verification page
- [x] Student ID cards (front/back, photo, QR) with bulk print
- [x] Alumni records

Phase 4 status (2026-10-31): complete. Admissions, offers, lifecycle, graduation,
certificates, ID cards and alumni are in place. The suite is now 644 tests passing
after the Phase 3 report/export/job/reversal work.

## PHASE 5 - Portals and dashboards
- [x] Student portal: timetable, registration, results after release, balance, receipts, assignments, notices, ID card
- [x] Parent portal: multi-child switch (`GuardianLink`), finance flag, pay online, results, attendance, messages
- [x] Teacher/lecturer portal: timetable, rosters, mark entry, attendance, assignments
- [x] Staff department portals on shared shell
- [x] Role dashboards with Chart.js (principal, bursar, registrar, HOD, teacher)
- [x] At-risk nightly scoring + dashboard list + optional class-teacher notification
- [x] Global search (students, staff, courses, receipts, applications) permission-filtered

Phase 5 status (2026-11-03): complete. Portals for students, parents, lecturers
and departments are in place, role dashboards use Chart.js, global search is
permission-filtered, and `score_at_risk` stores nightly scores and notifies
class teachers. The suite is now 664 tests passing.

## PHASE 6 - LMS
- [x] Course sites: sections, notes, files, links, visibility by date
- [x] Assignments: due dates, late policy, text/file submissions, resubmission, rubrics, feedback, marks -> gradebook
- [x] Quizzes: question bank, random order, time limit, attempts, auto-marking, per-question analytics
- [x] Discussion forums with moderation
- [x] Live class links with "join now"
- [x] Gradebook with weighted categories visible to students
- [x] Plagiarism flagging between submissions of one assignment

Phase 6 status (2026-11-03): complete. Course sites, dated sections and
content, assignments with late policies/rubrics and gradebook sync, quizzes
with auto-marking and analytics, moderated discussions, live class join
windows, weighted gradebooks and internal similarity flagging are in place.
The suite is now 680 tests passing.

## PHASE 7 - Department modules (each behind a feature flag)
### A. Administration
- [x] HR: staff file, contracts, documents, recruitment, leave workflow, appraisals, training, discipline (restricted)
- [x] Payroll: structures, allowances, deductions, flat-rate tax/pension rules, monthly run, payslip PDF, bank file, ledger posting
- [x] Procurement and stores: requisition -> quotation -> PO -> GRN -> supplier invoice -> stock/assets
- [x] Estates: work orders, cleaning schedules, utility readings, preventive maintenance
- [x] Security: visitor log, gate passes, student sign-out, incidents
- [x] Compliance: policy library + acknowledgements, audits, risk register, data-protection requests
- [x] Marketing: enquiries, lead pipeline, open days, newsletters via notification system
- [x] Governance: meetings, agendas, minutes, resolutions, action items, levies

Phase 7A status (2026-11-05): the administration modules are live behind their
feature flags and permission codes, with registry-driven registers, payroll PDF
and bank export, policy acknowledgement, newsletter sending, quotation awarding,
supplier-invoice matching, a module hub and 745 tests passing.

### B. Student services
- [x] Library: catalogue, loans, reservations, renewals, fines posted to the student account, overdue notices, ISBN/title lookup and QR lookup/PNG codes
- [x] Health: profiles, clinic visits, medication and immunisations done; every view logged; teacher-safe alert screen in place
- [x] Counselling: sessions and referrals done, every view logged, and notes are author-only unless a role holds `counselling.view_all_notes`
- [x] Student affairs: discipline cases, clubs, memberships, events and registrations
- [x] Sports: teams, members, fixtures, results, house points and equipment loans with availability tracking
- [x] Boarding: hostels, rooms, boarders, roll call, exeat workflow and automatic room/bed allocation
- [x] Transport: routes, stops, vehicles, assignments, trip logs, printable pick-up lists (with CSV) and a fuel-log report
- [x] Catering and tuck shop: meals, menus, bookings, wallet transactions, stock items/purchases and ledger posting of purchases
- [x] Careers and attachment: employers, placements, logs, events, supervisor reports and final attachment grading

### C. Academic support
- [x] Examinations: exams, papers, candidates, invigilation duties, clash checks, seat allocation, per-paper moderation, result release, candidate CSV and ZIMSEC/HEXCO board exports
- [x] Research and postgraduate: projects, supervision meetings, ethics, publications, milestones and thesis tracking
- [x] Labs and workshops: labs, equipment, bookings, maintenance logs, safety induction, consumables and practical assessments
- [x] ICT helpdesk: devices, tickets, comments, software licences, SLA policies/overdue detection and the account-reset workflow (temporary password shown once)

Phase 7B/7C status (2026-11-09): all 13 apps have models, forms, registry
screens, migrations, feature flags, tenant isolation, audit entries, role codes
and a "School services" directory. The remaining workflows listed in the
previous status are now built: library QR lookup, sports equipment loans,
automatic boarding allocation, transport pick-up lists and fuel reports,
catering stock/ledger posting, careers supervisor reports and grading, research
milestones/theses, lab safety/consumables/practicals, and ICT SLA plus account
resets. The suite is now 814 tests passing.

## PHASE 8 - API and integrations
- [x] REST API v1: token auth, pagination, filtering, ordering, throttling, tenant isolation, role permissions, OpenAPI docs
- [x] Webhooks: configurable URLs, HMAC signatures, retries, delivery log
- [~] Integrations: Paynow exists (poll + signed webhook) and EcoCash is a payment method; SMTP already exists; attendance-device bulk ingestion and the accounting journal CSV export are built. Still stubs or missing: a real SMS gateway, a real WhatsApp Business adapter, and Google/Microsoft OIDC SSO.

## Public landing page and self-registration
- [x] Platform-level sign-in and marketing page (`templates/registration/login.html`): sticky header, hero with sign-in card and responsive classroom photo, trust strip, feature cards, audience cards, how-it-works steps, role blocks, security band, contact section with form, privacy link and current-year footer
- [x] Public design system (`static/js/landing.js`): Figtree, mobile-first layout, dark mode, reduced-motion support, skip link, keyboard focus and vanilla-JS menu/search/enhancement. The stylesheet is now the shared brown `static/css/public.css` (see "Part 3B" below); the earlier deep green/marigold `static/css/landing.css` is superseded and no longer referenced.
- [x] Responsive landing images (`static/img/landing/`, `scripts/build_landing_images.py`): WebP + JPEG at phone and desktop widths, width/height attributes, lazy loading below the fold, fallbacks and a generated gradient placeholder when a photo is missing
- [x] Searchable public registration at `/register/` with student and staff paths, institution picker restricted to institutions that switched public registration on
- [x] Student path: match admission record by registration number + surname + date of birth, create/attach one login, activate immediately, email verification, generic mismatch message, institution contact page, per-IP/per-number throttling and audit logging
- [x] Staff path: inactive "Pending approval" request limited to the institution's allowed positions, administrator review with filters, approve (including a different position), reject with a reason, email and in-app notification, audit trail
- [x] Institution settings for `public_registration` and allowed `staff_positions`; migrations for both fields and the registration models
- [x] Tests for the landing page, sign-in, contact form and honeypot, student matching/mismatch/duplicates/throttling, generic message, staff creation/approval/rejection, cross-institution isolation and forbidden roles

## Part 3B - Brown public theme
- [x] One shared stylesheet (`static/css/public.css`) for every public/access page, built on a single brown palette (`--brown-950` .. `--brown-50`); no colour is hard-coded outside those variables, and every gradient runs between two browns.
- [x] The signed-in application keeps its own theme (`static/css/app.css`); only the signed-out surface changed. The dashboard moved to `/dashboard/` and `/` now serves the platform landing page (a signed-in visitor is redirected to the dashboard).
- [x] All-brown dark variant (espresso `#1e120c`/`#2b1a12`, cream text, caramel accents) driven by `prefers-color-scheme` plus a toggle remembered in `localStorage` (`edusphere.public.theme`) and applied with a no-flash inline script.
- [x] Accessibility: main text/button pairs reach WCAG AA (>=4.5:1), a thick dark-brown focus outline sits on a cream offset, and error/success messages use a dark-brown panel with an icon and wording rather than colour alone.
- [x] Institution branding (logo, name, tagline, About) shows on the public institution page (`/i/<slug>/`) and the learn index (`/learn/`), but the page stays brown; the logo sits on a soft cream backing with initials as a fallback for missing or very light/dark logos. `Institution.tagline`/`about` were added (migration `0008`).
- [x] Photos get a CSS-only warm brown duotone overlay; faces stay clear and alt text is kept.
- [x] Emails from verification, invitation and password reset use the same brown palette with inline CSS and keep a plain-text alternative (`templates/emails/`, `OutboundMessage.html_body`, migration `0009`, provider `html_body` plumbing in `core/notifications.py`).
- [x] Tests (`core/tests/test_public_theme.py`, 46 tests): a palette scan of `public.css` and the public templates, a contrast check for the main text/button pairs in both themes, and a render test that loads every public page with the stylesheet and no 500s.
- [x] Verification: 1010 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Part 1 - Institution logo and branding
- [x] The setup wizard and the institution settings screen both let an administrator upload a logo, choose the primary and accent colours, and set the "About us" text (up to 600 characters), tagline, address, phone, email and website. The existing WCAG contrast check still rejects an unreadable colour pair.
- [x] Logo upload rules: PNG, JPG or WebP only (SVG is refused), at most 2 MB, at least 200x200 px. The real file type is checked with Pillow, the picture is re-encoded so no metadata or hidden payload survives, and each institution's files are stored as WebP at 64, 160 and 512 px with a 512 px PNG fallback under a random name in `institutions/<slug>/logos/`. The form shows a live preview before saving and offers "Remove logo"; without a logo the UI falls back to the institution's initials in a coloured circle.
- [x] One helper (`core/logo.py`) builds every logo URL (`institution_logo_url`, `institution_logo_png_url`, `absolute_logo_url`) and a `logo_flowable` for PDFs, plus the `{% institution_logo %}` and friends template tags (`core/templatetags/branding.py`). These are used in the sidebar, the institution login and registration pages, emails, PDFs, receipts, invoices, statements, report cards, transcripts, ID cards and certificates.
- [x] Tenant isolation and audit: uploads live under the institution's own folder, a user may only change their own institution's branding, only holders of `admin.manage_branding` may do it, and every change is written to the audit log. `Institution.website` was added and `Institution.about` is now capped at 600 characters (migration `0010`); `core/migrations/0011` grants `admin.manage_branding` to existing institution-admin roles.
- [x] Tests (`core/tests/test_logo.py`, 50 tests): validation and EXIF/metadata stripping, the stored sizes and PNG fallback, random names, the canonical field, delete/replace, the URL helpers, `logo_flowable`, the template tags, the model fields and initials, the settings screen (save/remove/permission gate/tenant isolation/audit) and the wizard logo flow.
- [x] Verification: 1060 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Part 2 - Shareable public sign-up links
- [x] An administrator can create a shareable link (`/join/<uuid>/`) for their own institution and hand it out by email, poster or chat. A `SignupLink` can be limited to students, to staff, or to both; a staff link can be preset to one position; and it can carry a name, an expiry date and a maximum number of uses. Each registration that arrives through a link is recorded as a `SignupLinkUse`, which makes the use limit exact and gives the administrator a list of who came in through the link.
- [x] The link is its own invitation, so it works even when the institution has `public_registration` switched off; the public registration page still only lists institutions that switched it on. A staff link offers the link's preset position, otherwise the institution's public position list, otherwise every position a public form may offer (administrator, principal and the other leadership roles are always refused).
- [x] Registration reuses the existing services: `register_student` matches the admission record and activates the portal, and `register_staff` creates the inactive login and the pending request for review. `register_staff` gained an `allowed_positions` keyword (defaulting to the institution's public list, so the ordinary flow is unchanged), and `StudentRegistrationForm`/`StaffRegistrationForm` gained `institution=`/`positions=` keywords that lock the form to one institution and restrict the positions offered.
- [x] The public page (`templates/registration/signup_link.html`) uses the public brown theme and the institution logo/tagline/contact details, shows the student/staff chooser when the link is for both, and shows a closed page (expired, revoked, used up or inactive institution) or a 429 when the address is over the attempt limit. An unknown token is a 404. The administrator screen (`templates/accounts/signup_links.html`, `admin.manage_users`) lists the links with a copy button and lets an administrator revoke, reactivate or delete one; it is linked from the institution settings and the Users list.
- [x] Tenancy, permission and audit: the list and actions are scoped to the signed-in user's institution, the actions require `admin.manage_users`, and create/revoke/reactivate/delete/use are written to the audit log. Migration `accounts/0008` creates the two models and the index.
- [x] Tests (`accounts/tests/test_signup_links.py`, 37 tests): the model open/closed logic, the service validation and audit rows, the position fallbacks, the public page (chooser, preset position, closed/expired/used-up links, student activation with public registration off, the generic mismatch error, staff requests, rate limiting, 404) and the administrator screen (permission gate, create, bad date, share URL, revoke/delete, tenant isolation, settings link).
- [x] Verification: 1097 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Part 3 - Institution-specific login page
- [x] Each institution has its own branded sign-in page at `/i/<slug>/login/` (`accounts.views.InstitutionLoginView`, `templates/registration/institution_login.html`). It shows the institution's logo (or its initials), name, tagline and contact details on the public brown theme, and it stays on the brown palette rather than applying the institution's own primary/accent colours.
- [x] The page only accepts accounts that belong to that institution. `InstitutionAuthenticationForm` (`accounts/forms.py`) checks the institution after the password is confirmed and refuses a valid login for a different school with the same generic message as a wrong password, so the page never reveals whether an account exists elsewhere. A platform superuser may still sign in on any institution's page. Login works whether or not `public_registration` is switched on, and an unknown or inactive institution is a 404.
- [x] The lockout rules are shared: the failed-password counting and the "temporarily locked" message were extracted into `LoginLockoutMixin` and both the platform sign-in and the institution sign-in use it. A signed-in visitor who opens the page is sent to the dashboard, and the `next` parameter is honoured.
- [x] The link is shareable: the public institution page links to it, and the institution settings screen shows the absolute link with a copy button (`institution_login_url` in `core/views.settings_view`).
- [x] Tests (`accounts/tests/test_institution_login.py`, 16 tests): the branded page and its initials fallback, the colours staying off the public page, unknown/inactive institutions (404), the public page link, sign-in for the right institution, refusal for another institution, superuser access, a wrong password, login with public registration off, the `next` parameter, the signed-in redirect, a counted failed attempt, an inactive account and the shareable link on the settings screen. The new page was added to the public-page render sweep in `core/tests/test_public_theme.py`.
- [x] Verification: 1113 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Part 4 - Printable/PDF receipt and emailed receipt
- [x] Every payment receipt can be downloaded as a PDF (`finance/receipts.py`, `build_receipt_pdf`, view `receipt_pdf` at `receipt/<pk>/pdf/`, name `receipt_pdf`). The PDF is a real document, not a screenshot: the institution's logo (via `logo_flowable`), its name, tagline, address and contact details, the primary colour for the headings, the receipt number, student, what the payment was for, the amount, the method and reference, the date, who received it and the balance left on the invoice.
- [x] The PDF carries a QR code that opens the public verification page (`verify_receipt`), so a printed receipt can be checked against the system. The verification page shows no more than the receipt itself. The download obeys the same rule as the on-screen receipt: a student may only open their own receipts, and the other roles with finance access may open any.
- [x] A receipt can be emailed to the student with one POST (`email_receipt` at `receipt/<pk>/email/`, name `email_receipt`). It reuses the existing notification pipeline (`queue_message` + `send_message`) and a new brown HTML template (`templates/emails/receipt.html`) that carries the institution's logo and the public verify link; a student without an email address on file is told so instead of failing silently. A GET never sends anything.
- [x] The on-screen receipt (`templates/finance/receipt.html`) now offers "Download PDF" and "Email receipt" next to "Print receipt". No new model or migration was needed: the receipt number, verification token and branding already existed.
- [x] Tests (`finance/tests/test_receipt_documents.py`, 15 tests): the PDF download and its headers, the request and fiscal record passed to the builder, the student's ownership rule, the sign-in requirement, the on-screen buttons, the email send (channel, recipient, subject, status and HTML), the verify link in the email, a GET not sending, a missing email address, a student emailing only their own receipt, the verify URL helper, and the PDF building with and without a stored logo plus the QR flowable.
- [x] One shared masthead (`core/document_header.py`) now puts the logo, institution name, address, phone, email, website and, when set, the VAT and BP numbers at the top of every document: screen receipts, credit and debit notes, statements, invoices, PDF receipts, invoices, statements, report cards, transcripts, timetables, offer letters, certificates, identity cards and the receipt/fee-reminder emails. PDFs use the 512 px logo and screens/email the 160 px one; without a usable logo the same masthead draws an initials circle, and a missing or corrupt file falls back instead of breaking the document. The written text stays ink-on-page so every document remains readable in black and white.
- [x] HTML pages use `templates/core/_document_header.html` through `{% document_masthead %}` (in `core/templatetags/branding.py`); PDFs use `build_header`/`header_mark`. The same module exposes `institution_details`, `contact_lines` and `header_context`, so no builder can print a name but forget the address or tax number.
- [x] A fiscalised document keeps the layout it was issued with: `FiscalSubmission.layout` stores the details and a copied PNG of the logo under `fiscalisation/headers/...` when the submission is created. The receipt, invoice, credit-note and debit-note screens and PDFs pass that snapshot back to the masthead, so changing the live logo or address never rewrites an already-issued document. Non-fiscalised documents always use the current details.
- [x] Added branded invoice screens (`templates/finance/invoice.html`, `invoice_detail`/`invoice_pdf` views) and `finance/invoices.py`, linked from the student record, showing the VAT, balance and payment history; the credit/debit note and statement templates use the same masthead.
- [x] Tests (`core/tests/test_document_header.py`): the details and contact lines, the tax line only when set, the initials fallback, a missing logo file, a real logo plus its fiscal snapshot, the three-column header, a stored layout winning over the live institution, the receipt screen using the fiscal snapshot, and the receipt email carrying the details.
- [x] Verification: 1188 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Part 5 - Configurable student and staff numbers
- [x] An institution chooses the shape of its own numbers in the settings screen (`core/numbering.py`, action `numbering` in `core/views.settings_view`): a prefix, whether the year is part of the number, how many digits the sequence is padded to, and the next number to issue. The screen shows the number the next record will receive.
- [x] The counter lives in a new `core.models.NumberSequence` row (one per institution and kind) and is advanced with an atomic update inside a row lock, mirroring the finance document sequence, so two people admitting at the same moment never share a number. A migration (`core/0012_numbersequence`) adds the table.
- [x] Student numbers are generated by the format instead of the old hard-coded `year + pk`: `academics.models.Student.save` calls `next_number(institution, "student")`. Staff numbers are generated the same way: `core.models.StaffProfile.save` fills `employee_no` from `next_number(institution, "staff")`. In both cases a number that was supplied (typed in, or imported from a spreadsheet) is kept exactly as it is, and numbers are only generated on creation, never on an update.
- [x] Tests (`core/tests/test_numbering.py`, 24 tests): the defaults and the formatting, saving a format and seeding the counter, the validation of the prefix, padding and next number, sequential and year-less allocation, the separate student and staff sequences, the preview, student and staff generation, an explicit number being kept, the format change taking effect, per-institution isolation, and the settings screen (showing the section, saving a format, the audit entry, a bad value and the permission gate).
- [x] Full token set for student, staff and applicant numbers: a fixed letter prefix, a two- or four-digit year, an optional programme (students) or department (staff) code, a zero-padded sequence of 1-8 digits, no separator, a hyphen or a slash, and an optional final letter (fixed, or a documented weighted-mod-26 calculated check letter). Example `LIT2026000123C`.
- [x] The format and the last issued number are stored per institution, kind and year in `core.NumberFormat` and `core.NumberSequence` (migration `core/0013`); `core.NumberFormatChange` keeps an append-only history of every change with the before/after format, actor, reason and time. The sequence resets each year or continues forever, and an administrator can choose the start number.
- [x] Allocation is atomic: `next_number` takes a `select_for_update` row, advances the counter with `F()` and retries a rare SQLite double insert or "database is locked" error. Student, staff and application creation call it inside the same transaction as the person record, so two simultaneous admissions never share a number, and a number is never put back after a deletion or withdrawal. The staff employee number is unique per institution.
- [x] A dedicated numbering page at `/settings/numbering/` (`numbering_settings`) shows every option, a live three-number preview built in the browser with the same check-letter arithmetic, validation messages, and a confirm-before-save warning that existing numbers are not renamed. It carries a manual-override form and the change history. The short "Number formats" section on the institution page links to it and still works.
- [x] Explicit and imported numbers are uppercased, validated for uniqueness (ignoring case), and logged; the bulk importer accepts existing numbers and generates the rest. Numbers are matched and searched tolerantly (case, spaces and separators) in global search, the student search API, staff screens, admissions lists, the user list, registration matching and the attendance-device API, and the same numbers appear on receipts, statements, invoices, ID cards and exports.
- [x] Tests (`core/tests/test_numbering.py`, `core/tests/test_import.py`, `accounts/tests/test_registration.py`, `academics/tests/test_dashboards_search.py`): the token combinations, check letters, yearly and continuous sequences, start numbers, previews, change history, deletion/no-reuse, manual override and audit, applicant numbers, separator-insensitive search and registration, import duplicates and generated numbers.
- [x] Verification: 1188 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.



