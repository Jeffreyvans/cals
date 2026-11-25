# Vanpere - build plan

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
> Superseded by Part 3C below: the public surface is now deep green and marigold, not brown.
- [x] One shared stylesheet (`static/css/public.css`) for every public/access page, built on a single brown palette (`--brown-950` .. `--brown-50`); no colour is hard-coded outside those variables, and every gradient runs between two browns.
- [x] The signed-in application keeps its own theme (`static/css/app.css`); only the signed-out surface changed. The dashboard moved to `/dashboard/` and `/` now serves the platform landing page (a signed-in visitor is redirected to the dashboard).
- [x] All-brown dark variant (espresso `#1e120c`/`#2b1a12`, cream text, caramel accents) driven by `prefers-color-scheme` plus a toggle remembered in `localStorage` (`edusphere.public.theme`) and applied with a no-flash inline script.
- [x] Accessibility: main text/button pairs reach WCAG AA (>=4.5:1), a thick dark-brown focus outline sits on a cream offset, and error/success messages use a dark-brown panel with an icon and wording rather than colour alone.
- [x] Institution branding (logo, name, tagline, About) shows on the public institution page (`/i/<slug>/`) and the learn index (`/learn/`), but the page stays brown; the logo sits on a soft cream backing with initials as a fallback for missing or very light/dark logos. `Institution.tagline`/`about` were added (migration `0008`).
- [x] Photos get a CSS-only warm brown duotone overlay; faces stay clear and alt text is kept.
- [x] Emails from verification, invitation and password reset use the same brown palette with inline CSS and keep a plain-text alternative (`templates/emails/`, `OutboundMessage.html_body`, migration `0009`, provider `html_body` plumbing in `core/notifications.py`).
- [x] Tests (`core/tests/test_public_theme.py`, 46 tests): a palette scan of `public.css` and the public templates, a contrast check for the main text/button pairs in both themes, and a render test that loads every public page with the stylesheet and no 500s.
- [x] Verification: 1010 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Part 3C - Green public platform, learn pages and institution profiles
- [x] One deep-green and marigold public identity (`#12403c` / `#e8a317`) on Figtree replaces the earlier brown palette for every signed-out page. `static/css/public.css` holds the tokens, the responsive mobile-first layout, the dark variant (system setting plus a remembered toggle) and the learn/institution styles.
- [x] `/` is a platform login-and-marketing page: hero, sign-in card, an African classroom photo (WebP with a JPEG fallback and lazy loading below the fold), features, audience cards, how-it-works, role blocks, security band, contact section (phone `+263 774 573 765` and `vanhuvaonejeffrey@gmail.com`) and a footer. It carries no institution-specific content.
- [x] Seven plain-English learn pages linked from the header and footer: `/features/`, `/for-schools/`, `/for-colleges/`, `/for-parents-and-students/`, `/security-and-privacy/`, `/pricing-and-contact/` and `/faq/`, plus a `/learn/` hub. Content lives in `core/learn.py`; there are no invented statistics, client names, awards, testimonials or prices. The header uses a `<details class="nav-more">` menu so every page is reachable without JavaScript.
- [x] `/pricing-and-contact/` carries a "Request a demo" form (`DemoRequestForm`) with a honeypot and the shared contact rate limit; it emails the platform contact address and never publishes a price.
- [x] Branded institution page at `/i/<slug>/`: logo, validated primary/accent colours with computed readable text colours, name, tagline, About, contact details, sign-in card and a "Powered by Vanpere" footer linking to `/`. An institution can switch the page off and choose whether to show a Register button; the button only appears when public registration is on or an open sign-up link exists.
- [x] Institution-scoped sign-in from the institution page refuses another institution's account with the standard "wrong username or password" message. Institution colours apply only on `/i/<slug>/`, never on `/i/<slug>/login/`.
- [x] SEO: page titles, meta descriptions, Open Graph/Twitter tags, canonical URLs and clean routes; a public-only sitemap (`/sitemap.xml`); and `/robots.txt` that disallows the private app while allowing the public check pages. Private and institution login pages are `noindex`, and no personal data appears on a public page.
- [x] Tests: `core/tests/test_public_pages.py` (learn pages, demo form, public page/Register toggles, sitemap, robots, noindex) and a rewritten `core/tests/test_public_theme.py` (green palette scan, AA contrast in both themes, render sweep, branding and green emails).
- [x] Verification: 1214 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

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




## PHASE 10 - Deployment, platform administrator provisioning and Render
Status (2026-11-16): complete.

- [x] Platform administrator auto-provisioning: new `ensure_platform_admin` management command reads `PLATFORM_ADMIN_USERNAME`, `PLATFORM_ADMIN_EMAIL`, `PLATFORM_ADMIN_PASSWORD`, `PLATFORM_ADMIN_RESET_PASSWORD` and `PLATFORM_ADMIN_FORCE_CHANGE` from the environment. It creates one account with `is_platform_admin`, `is_staff`, `is_superuser`, `is_active`, role `admin`, no institution, a verified email and a `PlatformStaff` `main_admin` row; reruns are idempotent and never duplicate the account.
- [x] Password safety: the password is validated with Django's validators plus the project's `validate_strong_password`, stored only through `set_password`, and never printed or logged. It is not changed on a later run unless `PLATFORM_ADMIN_RESET_PASSWORD=true`; the first creation writes an audit entry with no secret material. Missing variables produce a clear warning and create nothing.
- [x] Automatic run: `build.sh` runs the command after `migrate`; `platform_admin.startup.ensure_once()` is a guarded second chance from `AppConfig.ready()` that runs at most once per process, only when the variables are present, and never under tests, migrations, `collectstatic`, `check` or other management commands.
- [x] First-login safety: `User.is_platform_admin` and `User.password_set_from_env` were added (migration `accounts/0009`); `User.institution` is now nullable so the platform administrator truly belongs to no school. `PLATFORM_ADMIN_FORCE_CHANGE` defaults to true, the existing forced-password-change middleware sends the administrator to a strong-password form, and the platform dashboard shows a warning banner until the password is changed. The platform sign-in redirects to the platform dashboard, not a school dashboard.
- [x] Render settings: `config/settings.py` is environment-driven for `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, `EMAIL_BACKEND`, `RESEND_API_KEY`, `DEFAULT_FROM_EMAIL` and storage. `RENDER_EXTERNAL_HOSTNAME` is added to hosts and CSRF origins automatically; `DEBUG=false` enables secure cookies, `SECURE_PROXY_SSL_HEADER`, SSL redirect, HSTS, `SECURE_REFERRER_POLICY`, `SECURE_CONTENT_TYPE_NOSNIFF` and `X_FRAME_OPTIONS=DENY`. Local development still runs with no variables (SQLite, debug on, console email).
- [x] Static and media: WhiteNoise with `CompressedManifestStaticFilesStorage`; `collectstatic` copies and post-processes 509 files. Uploads use local storage in development and S3-compatible storage (Supabase Storage, Cloudflare R2 or Backblaze B2) through `django-storages` + `boto3` when `STORAGE_BACKEND` says so.
- [x] Required packages pinned in `requirements.txt` (Django, DRF, drf-spectacular, openpyxl, qrcode, pillow, reportlab, cryptography, celery, gunicorn, whitenoise, psycopg[binary], dj-database-url, django-storages, boto3, tzdata). A clean virtual environment install succeeded and `manage.py check` passed.
- [x] `build.sh` (executable) installs dependencies, collects static files, migrates and ensures the administrator. `render.yaml` declares the Python web service, `./build.sh`, gunicorn start, `/health/`, pinned Python, generated `SECRET_KEY`, `DEBUG=false` and every secret as `sync: false`.
- [x] `/health/` (`config/health.py`) runs one database query and returns `200 {"status":"ok","database":"ok"}` or `503` with `Cache-Control: no-store`.
- [x] `.gitignore` covers local databases, `.env`, media, staticfiles, caches and virtual environments; `.env.example` lists every variable with obvious placeholders.
- [x] `python manage.py check --deploy` with production settings reports zero issues. `ruff check .` is clean and `makemigrations --check` reports no changes.
- [x] `DEPLOY.md` documents click-by-click Render setup, every environment variable, Supabase session-pooler PostgreSQL, S3-compatible uploads, Resend/SMTP email, a custom domain, password rotation, a go-live checklist, troubleshooting and useful commands.
- [x] Tests: `platform_admin/tests/test_ensure_platform_admin.py` (creation, double run, unchanged password, reset flag, missing variables, weak password, no password in logs/output, force-change switch) and `config/tests/test_health.py` (200 and 503).
- [x] Verification: 1385 tests pass, `ruff check .` is clean, `makemigrations --check` reports no changes, `check --deploy` with production settings reports zero issues, and `collectstatic` succeeds. Local development and tests use plain static storage; production uses the WhiteNoise compressed manifest. The health service module hub lives at `/health/hub/`, leaving `/health/` for the Render health check.

## PHASE 11 - Reliable email on the Render free plan (Resend over HTTPS)
Status (2026-11-16): complete.

Every message now goes through one path: `queue_message()` -> `OutboundMessage` ->
`send_message()`. There is no direct `EmailMessage.send()` left in a view and no
SMTP setting anywhere, so Render's blocked outbound ports are never used.

- [x] One backend: `config/settings.py` selects `anymail.backends.resend.EmailBackend`
  when `RESEND_API_KEY` is set and the console backend otherwise. Django's test
  runner still swaps in the in-memory backend, so `mail.outbox` keeps working.
  `django-anymail[resend]==15.2` is pinned in `requirements.txt`. `EMAIL_HOST`,
  `EMAIL_PORT`, `EMAIL_USE_TLS` and `EMAIL_USE_SSL` are gone from the project.
- [x] Every email is themed: `core/notifications.py` renders a brown HTML part
  (a supplied template, or `templates/emails/generic.html` via
  `core/notifications_html.py` when none was given) alongside the plain text, so
  no message is text-only. The sender display name is the institution's name
  while the address stays on the verified domain; `Reply-To` is the institution's
  contact address when one is set.
- [x] Delivery is resilient: a send runs in the background only when a Celery
  broker is configured and `EMAIL_ASYNC=true` (otherwise inline), with a 10 second
  timeout, up to `EMAIL_MAX_RETRIES=5` attempts and exponential backoff. Every
  attempt is recorded on `OutboundMessage` (`provider`, `provider_message_id`,
  sanitized `provider_response`, `retries`, `next_attempt_at`). A failed send
  never raises into a view; the user sees a calm message.
- [x] Abuse limits: verification and password reset are limited per address and
  per IP, and bulk sends (newsletter, fee reminders) are capped per institution.
  Every limit is configurable (`EMAIL_VERIFY_LIMIT`, `EMAIL_RESET_LIMIT`,
  `EMAIL_IP_LIMIT`, `EMAIL_BULK_LIMIT`, `EMAIL_RATE_WINDOW_SECONDS`).
- [x] Secrets and links: provider error text is sanitized before it is stored
  (keys, tokens and token-bearing URLs are redacted), the API key is never logged
  or printed, and every link is built from `SITE_URL` (forced to https outside
  debug) instead of the request host. Verification and reset links stay single
  use and expiring (`PASSWORD_RESET_TIMEOUT=3600`).
- [x] Operations: `python manage.py check_email --to you@example.com` sends a test
  and prints the sanitized provider response; the platform **Email health** page
  lists the last 50 messages, failure/bounce/complaint/suppression counts and a
  Resend button for a failed message; `/webhooks/resend/` verifies the Svix
  signature (`svix` from `django-anymail[resend]`, with an HMAC fallback) and
  records delivered/bounced/complained events, suppressing hard-bounce and
  complaint addresses (`EmailSuppression`).
- [x] `render.yaml`, `.env.example` and `DEPLOY.md` document the Resend key, the
  verified domain, `SITE_URL`, `SERVER_EMAIL` and the webhook secret, plus
  troubleshooting.
- [x] Tests: `core/tests/test_email_pipeline.py` (backend selection, no SMTP
  settings, each email type with HTML and text, failures/retries/non-raising,
  `SITE_URL` links, rate limits, webhook signature and suppression, tenant
  isolation of email logs, the `check_email` command). `accounts/tests/test_signup_links.py`
  now expects the `SITE_URL` link and `admissions/tests/test_admissions.py`
  expects a failed email not to roll back an admission.
- [x] Verification: 1409 tests pass, `ruff check .` is clean and
  `makemigrations --check` reports no changes.

## PHASE 12 - Institution onboarding from the platform admin area
Status (2026-11-18): all three parts complete.

The platform admin can now take a newly created institution from empty to a
working first administrator without leaving the platform area. The work reuses
the existing invitation, branding, audit, notification and central email code.

### Part 1 - First administrator
- [x] Added `InstitutionOnboarding` (`platform_admin/migrations/0004`) to remember the one-time milestones: first administrator created, first administrator signed in, welcome email sent/resend count, setup started/completed and platform-created sign-up links. Passwords, tokens and links are never stored in this row.
- [x] Added `platform_admin/onboarding.py`: derives the seven-step checklist from live rows, provisions administrators in invitation or temporary-password mode, sends the branded welcome email through `queue_message`/`send_message`, notifies the institution, writes platform audit rows without secrets, and manages resend/reset/unlock/deactivate/reactivate/remove with a last-active-administrator guard.
- [x] Added `InstitutionAdministratorForm` and the platform views/URLs for `admins/new/` and `admins/<user_pk>/credentials/`. The temporary password or one-time set-password link is stored in the session for exactly one on-screen display with Copy and Print; it cannot be shown again. When delivery fails, the screen offers a one-time link instead.
- [x] Updated the institution detail and administrator list templates with a progress checklist, "what is missing", management actions and a welcome-email resend form. The approval flow now uses the same welcome email template and service as direct onboarding.
- [x] Added `platform_admin/tests/test_institution_onboarding.py` (10 tests) and updated the existing deactivation test for the new last-admin rule. Verification: 1,498 tests pass, `ruff check .` is clean and `makemigrations --check` reports no changes.

### Part 2 - Web address, sign-in link and first-sign-in handoff
- [x] Added `core/slugs.py` as the one place for institution slug rules: plain lower-case slugs, a reserved-word list for top-level routes, uniqueness against institutions and live redirects, and a `unique_slug()` helper for safe suggestions. Domain parsing/verification-token helpers live there too.
- [x] Added `InstitutionSlugRedirect` (migration `core/0020`) and `resolve_institution_slug`/`record_slug_change`. Changing an institution slug keeps the old `/i/<slug>/` and `/i/<slug>/login/` addresses alive for 90 days, including the query string; expired redirects stop working and stop reserving the word. Inactive institutions never resolve.
- [x] The platform institution detail screen now shows a branded sign-in-link panel with the copyable URL, a QR PNG endpoint (reusing installed `qrcode[pil]`), a WhatsApp share link, an enabled/disabled status and an audited "email sign-in link" action. The email is sent through `queue_message` with the support contacts `+263774573765` / `vanhuvaonejeffrey@gmail.com`; a non-`sent` `OutboundMessage.status` surfaces an error.
- [x] Platform admins can create a sign-up link for the institution from the detail screen (`SignupLink.created_by_platform_admin`, migration `accounts/0011`). The marker is informational: the link obeys the same kind, expiry, max-use and tenant rules as institution-created links. The result is shown once with a copyable `SITE_URL` link.
- [x] Custom domains are off by default. Saving a domain generates a verification token and prints the DNS TXT record plus the required `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` entries; an explicit confirmation marks it verified and enables host-based institution mapping through `core.middleware` (cached). Disabling keeps the record but stops the mapping. No live DNS lookup.
- [x] The first administrator's real form login queues a one-shot session flag from `LoginLockoutMixin.form_valid` (not the auth signal, so `force_login` cannot trigger it), then `FirstSignInSetupMiddleware` opens a five-step, institution-scoped guided setup in `core/institution_setup.py` reusing `templates/core/setup_wizard.html`. It never grants the platform-only `admin.create_institutions` permission; the last step creates a sign-up link, shows it once and requires a final "Finish setup" post.
- [x] Added `core/tests/test_slugs.py` (27 tests) and `platform_admin/tests/test_onboarding_part2.py` (24 tests) covering slug/domain rules, old-link redirects, host mapping, link panel, QR, email queue, sign-up links, custom-domain lifecycle, real-login-only handoff, tenant isolation, permission 404/403 and secret-free audits. Verification: `ruff check .` clean, `makemigrations --check` reports no changes; the full suite collected 1,549 tests with 1,548 passing and one unrelated flaky finance concurrency test that passes in isolation.

### Part 3 - Handover
- [x] Cross-checked every requirement against the code and tests. Closed the remaining gaps: legacy administrators are no longer sent to the guided setup, failed invitation/password-reset emails are surfaced on the one-time credential screen, a failed welcome email is not counted as delivered, and the application-approval follow-up now carries the support contacts. Added `platform_admin/tests/test_onboarding_part3.py` (6 tests) plus a legacy-admin regression test.
- [x] Wrote `ONBOARDING_REPORT.md` (requirement matrix, verification, decisions and caveats) and `MANUAL_TESTING.md` (nine click-by-click scenarios including email-failure simulation and custom-domain verification).
- [x] Final verification: 1,556 tests pass, `ruff check .` is clean and `makemigrations --check --dry-run` reports no changes.

## PHASE 13 - Dedicated bulk upload of students (session 35, 2026-11-19)
Status: complete.

Institution registrars can now create and update students, programmes/classes,
years/grades, streams/cohorts, courses and enrolments from one UTF-8 CSV or
`.xlsx` file. The work reuses the shared import reader, admissions creation,
numbering, audit and central email paths; the older `students` import is
unchanged.

- [x] Added a separate `student_upload` import kind, gated by the new
  `students.bulk_import` permission, with dedicated upload, preview, run,
  result, CSV report and undo screens/templates and navigation links.
- [x] The reader enforces a 5 MB/5,000-row configurable limit, real file types,
  blank-row skipping, trimming, header aliases and an optional column mapping.
  The preview supports filters, counts, old-to-new changes, new objects, dry
  runs and a downloadable report, and asks for confirmation before running.
- [x] Strong matching uses registration number, then email or username; a
  per-institution `BulkUploadMatch` fingerprint recognises a previous upload
  with no identity fields. Surname + date of birth + programme is only a
  review flag and never auto-merges. Duplicate identity values in one file
  flag every row in the duplicate group.
- [x] Updates write only supplied fields. Blank cells never erase stored
  values, a registration number never changes, and logins, fees, results,
  attendance and history are untouched. Pure `no_change` rows are not written;
  enrolment-only changes promote the row to an update.
- [x] New students go through `create_student_record` with manual or generated
  numbers, an institution `no_login`/invitation/temporary-password access mode
  and an optional first invoice. Credentials are shown once; invitations are
  queued through `send_email(send=False)` and delivered in rate-limited chunks.
- [x] Missing programmes, courses, classes, years, programme years and cohorts
  are previewed and created only when ticked, marked "Created by import,
  review needed", and never rename or delete existing objects. Enrolments are
  added only when missing; existing marks/attendance are preserved, rules are
  skipped with a warning when unticked, and removals require an explicit
  confirmation and set status to `dropped`.
- [x] Each row writes in its own transaction into one `ImportBatch`; results,
  warnings, audit entries and undo data avoid secrets. A background job uses
  Celery when available and a daemon-thread fallback otherwise. Safe undo
  deactivates created users, cancels queued invitations and keeps students
  with invoices, payments, marks or attendance.
- [x] Hardened the shared concurrent document-number allocator with a
  process-wide SQLite lock so ten simultaneous allocations stay gap-free.
- [x] Added `core/tests/test_bulk_upload.py` (29 tests) covering the full
  matrix, including undo of updates, the Excel template and bounded queries for
  5,000 rows; `MANUAL_TESTING.md` has a ready-to-paste sample file and
  click-by-click scenarios.
- [x] Verification: 1,630 tests pass, `ruff check .` is clean and
  `python manage.py makemigrations --check --dry-run` reports no changes.

## PHASE 14 - Categorised errors, offline handling and safe retries (session 36, 2026-11-20)
Status: complete.

Every failure now names its category and code on every surface, so users and
support can tell a connection problem from an account, input, database, server
or provider problem.

- [x] Added `core/error_catalogue.py`: one shared list of 30 codes in six categories (NETWORK, SERVER, DATABASE, ACCESS, INPUT, EXTERNAL), each with icon, title, explanation, action, numbered steps, who can fix it, default status and optional service.
- [x] Added `core/errors.py` with `classify_exception()`, `record_error()`, `log_error()`, `render_error()`, `error_json()`, `api_exception_handler()` and `add_error_message()`; every page, inline alert, API error and log line uses the same catalogue entry.
- [x] Added `ErrorRecord` (migration `core/0023_errorrecord.py`) storing metadata only: category, code, reference, request ID, view, method, path, duration, exception class, service, safe user/institution IDs and the catalogue wording. No body, header, token, password, SQL or stack trace is stored.
- [x] Added `ErrorContextMiddleware` and global 400/403/404/500 handlers. Every request gets a request ID; exceptions and direct error statuses are classified, recorded and logged once with a structured `edusphere.errors` line, and responses carry `X-Request-ID` and `X-EduSphere-Error-Code`.
- [x] Error pages, inline messages and `/errors/catalogue.js` show icon + text label, category/code, explanation, numbered steps, Try again, Go back, an `ERR-XXXXXX` reference with Copy, time and support contacts. Technical details are visible only to platform admins or holders of `errors.view_details`; no stack trace is ever shown.
- [x] Added CSP-safe `static/js/error-handling.js`: online/offline banner, 5-second slow warning, 15-second timeout, request classification, safe retry with idempotency keys, and non-password form data kept in `sessionStorage` until retry succeeds.
- [x] Hardened `static/js/sw.js`: only explicitly marked anonymous public pages are cached; signed-in HTML is never cached and an uncached offline navigation falls back to `/offline/`.
- [x] Replaced generic login, lockout, email, upload, permission, suspension, webhook and payment messages with categorised wording. Suspended institutions now get `ORG-SUSPENDED` and a reachable `/billing/contact/` page.
- [x] Added cache-backed idempotency (`core/idempotency.py`) to online payments and public student/staff registration, so a retry after a timeout cannot charge twice or create a duplicate account. Fixed the replay page so it no longer crashes when the original registration context is reused.
- [x] Upgraded Platform → Errors with category/code filters, a seven-day trend, reference lookup and links to Email health, Jobs and Audit; added a dedicated Jobs page. Added `errors.view_details` to platform permissions and the support/compliance presets.
- [x] Added `core/tests/test_error_handling.py` (27 tests) covering all categories, classification mappings, page/JSON/inline parity, secret-free output, permissions, browser behaviour, offline caching, suspension and idempotency. Documented every code and its manual trigger in `docs/ERROR_CODES.md`.
- [x] Verification: 1,657 tests pass, `ruff check .` is clean,
  `python manage.py makemigrations --check --dry-run` reports no changes and
  `python manage.py check` shows only the expected console-email warning.

## PHASE 15 - Vanpere rebrand (session 37, 2026-11-20)
Status: complete.

The visible identity is now Vanpere, published by Vanpere Digital Solutions,
without changing any internal identifier that a live deployment depends on.

- [x] Added `config/brand.py` as the one source of truth: `PRODUCT_NAME`,
  `COMPANY_NAME`, `TAGLINE`, support contacts, website from `SITE_URL`,
  current-year copyright, logo/favicon paths, theme colours and
  `BRAND_NAME`/`BRAND_COMPANY` overrides. Registered `PLATFORM_BRAND` via a
  context processor and added `{% brand_* %}` tags in `core/templatetags/branding.py`
  so HTML emails rendered without a request read the same values.
- [x] Replaced visible old names across templates, Python messages, PDFs,
  receipts, statements, invoices, report cards, transcripts, ID cards,
  certificates, error pages, login/registration, platform admin, API/Swagger,
  management commands, docs, legal pages and fixtures. Internal names
  (`EDUSPHERE_*` variables, cache keys, loggers, headers, URL names, class
  names, migrations and DNS labels) are deliberately unchanged and allow-listed.
- [x] Added a small “Powered by Vanpere” attribution with a link to the site on
  institution pages, receipts, documents and emails. `Institution.show_platform_branding`
  (migration `core/0024`) lets platform staff switch it off per institution for
  white-label plans; institution names, logos and colours are never touched.
- [x] Added placeholder Vanpere wordmarks and icons for the brown public,
  green app and marigold themes; platform admins can upload replacement PNG,
  JPG or WebP artwork from Platform settings through the existing safe upload
  pipeline. PWA manifest, tab titles, meta/OG tags, robots, sitemap and error
  pages now read the central brand values; the manifest is served dynamically
  at `/manifest.json`.
- [x] Added `/about/` with a factual description of the product, its publisher
  and contact details. No invented statistics, clients, awards or testimonials.
- [x] Updated email identity: default sender display name “Vanpere”, subjects,
  headers and HTML/text footers use brand values; institution mail keeps the
  institution display name with a switchable “via Vanpere” footer and platform
  copyright. The send address is unchanged unless `DEFAULT_FROM_EMAIL` is set.
- [x] Updated `render.yaml`, `.env.example`, `DEPLOY.md`, `README.md` and the
  other deployment docs with the brand variables, Render service/repository
  rename steps, custom domain, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
  `SITE_URL` and permanent `OLD_SITE_URL` redirects.
- [x] Updated privacy notice, terms and data processing statement to name
  Vanpere Digital Solutions as processor with the support contact details.
- [x] Added `config/tests/test_brand.py` (12 tests): an allow-listed scan for
  old spellings plus surface tests for pages, email, manifest, errors, PDF
  attribution, the white-label switch and tenant branding preservation.
- [x] Verification: full suite **1,669 passed**, `ruff check .` clean, 299
  templates compile, `python manage.py makemigrations --check --dry-run`
  reports no changes and `python manage.py check` shows only the expected
  console-email warning.

## PHASE 16 - Configurable academic hierarchy (session 38, 2026-11-23)
Status: complete.

Institutions can now use one editable School/Faculty > Department > Programme >
Course hierarchy, with their own grades/classes, terminology and defaults. All
of the new configuration is tenant-scoped, permission-gated and audited, and
existing institutions are never rewritten by a platform-level change.

- [x] Added `university` as an institution type and `academics.AcademicSchool`
  (migration `academics/0024`), plus `core.Department.school`,
  `Programme.school`/`department`, `SchoolClass.school`/`department`/
  `capacity`/`room`/`is_active`, `Student.school`/`department` and
  `GradeLevel.promotion_to`. Departments may exist without a school and
  programmes may belong to a department or directly to a school.
- [x] Added `academics/hierarchy.py` as the single source for institution-type
  defaults, level sets (including Grade 8-13, combined primary+secondary and
  Year 1-4/5-6), term schemes (Term/Semester), stream suggestions, editable
  templates, validation, template application, hierarchy labels/context,
  breadcrumb helpers, filter helpers and tenant-safe filter application.
  `code_for()` preserves digits and only expands short alpha codes, so the
  built-in level codes stay unique.
- [x] Added versioned `platform_admin.AcademicTemplate` rows, seeded by
  `platform_admin/0007_seed_academic_templates.py`. Platform staff can edit and
  publish a new version, activate an older version or roll back; only new
  institutions copy the current template, and `apply_template()` writes an
  institution-attributed `academic.template_applied` audit entry.
- [x] Extended the setup wizard from six to eight steps with an Academic type
  step and a Structure step. Checkboxes select the default schools/departments/
  levels, each can be renamed or recoded, and new rows can be added. The
  School/Faculty controls are hidden for primary and secondary; those types
  keep one Term unless an alternative level set or Semester scheme is chosen.
  Terminology (Pupil/Student, Class/Form, Teacher/Lecturer, Term/Semester,
  School/Faculty) is stored per institution and editable afterwards in
  `core/terminology.py`.
- [x] Added dedicated Academic structure and Grades and classes screens
  (`academics/structure.py`) linked from the navigation. Grades/classes can be
  renamed, reordered, deactivated or given a promotion target; deletion is
  refused while a student, enrolment, timetable, mark or other record uses the
  row. Classes also carry a class teacher, capacity, room and bulk streams.
  Reads use admin/registrar roles; every write requires
  `academics.manage_structure`, logs a permission denial on refusal and writes
  an audit entry on success.
- [x] Extended the bulk academic setup import with `Schools or Faculties`,
  `Departments`, `Grade levels` and `Classes` sheets, code-then-name matching,
  no deletions, a downloadable Excel template and the same
  `academics.manage_structure` gate and audit trail.
- [x] Added hierarchy filters to student search, staff lists, courses,
  programmes, dashboards, timetables, marks/results, debtors, collections,
  fee structures and finance exports. A shared template partial and template
  tags render the filters and breadcrumbs. Dashboards bypass cached global
  figures when a filter is active, so filtered numbers are not masked.
- [x] Added the hierarchy to breadcrumbs and to on-screen report cards,
  transcripts and their PDF headers, with school and department columns on the
  student table.
- [x] Added `academics/tests/test_hierarchy.py`,
  `academics/tests/test_hierarchy_filters.py`,
  `core/tests/test_wizard_hierarchy.py`,
  `core/tests/test_academic_import.py` and
  `platform_admin/tests/test_academic_templates.py` covering defaults, wizard
  tick/rename/add, rename propagation, reorder/deactivate/promotion, blocked
  deletion, hierarchy/filters, import sheets, template versioning, permissions,
  tenant isolation and audit.
- [x] Verification: full suite **1,736 passed**, `ruff check .` clean,
  `python manage.py check` shows only the expected console-email warning and
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected". The staff-list query guard was updated from 17 to 21 to account
  for the four hierarchy filter choice lists; it still fails on any N+1.

## PHASE 17 - Optional safe AI assistance (session 39)
Status: in progress. Every part is independently committed and verified. No AI
feature changes an existing screen or record when it is switched off.

- [x] Part 1, provider layer: added the `ai` app with a provider interface, an
  Anthropic implementation using the standard library only, and a deterministic
  mock provider for tests. Model names (`AI_MODEL_FAST`, `AI_MODEL_SMART`) and
  the key (`AI_API_KEY`) are read from the environment at call time and never
  hard-coded. Calls are server-side, capped at 15 seconds, have no Celery
  dependency, and return clear messages for missing configuration, timeouts and
  outages. `ask_ai()` is the single entry point and bounds output length.
- [x] Added 18 provider/service tests covering environment configuration,
  request payloads, timeout, DNS/HTTP failures, invalid responses, token
  counts, mock injection, safe error messages and the absence of Celery.
- [x] Verification after part 1: baseline suite **1,736 passed**; full suite
  with the new app **1,754 passed**, `ruff check .` clean, `python manage.py
  check` shows only the expected console-email warning, and
  `makemigrations --check --dry-run` reports "No changes detected".
- [x] Part 2, controls: a platform kill switch that starts closed, per-institution
  switches for all five features, a named consent record with a data disclosure
  snapshot and withdrawal, four daily/monthly limits, a permission-gated settings
  page and an institution-scoped usage page with a redacted log. Every blocked
  call is logged with zero tokens and never reaches the provider.
- [x] Part 2 tests: 13 new tests cover default-off behaviour, kill-switch
  precedence, per-feature and per-school switches, consent evidence and
  withdrawal, permissions, institution/user limits, redacted usage logging and
  estimated cost, settings and usage screens, unknown features and the closed
  platform default.
- [x] Verification after part 2: full suite **1,767 passed** (221 warnings),
  focused `ai` suite **31 passed**, `ruff check .` clean, `python manage.py
  check` shows only the expected console-email warning, and
  `makemigrations --check --dry-run` reports "No changes detected".
- [x] Part 3, privacy: redaction runs before any provider call, health and
  counselling prompts are refused locally, another institution's name, slug or
  object can never be sent, and blocked calls are logged against the caller's
  own school with a generic summary. Seven new tests cover all of these rules.
- [x] Verification after part 3: full suite **1,774 passed** (221 warnings),
  focused `ai` suite **38 passed**, `ruff check .` clean, and
  `makemigrations --check --dry-run` reports "No changes detected".
- [x] Part 4, features in order: help assistant, drafts, translation,
  plain-language insights, natural-language search. All five screens live at
  `/ai/help/`, `/ai/draft/`, `/ai/translation/`, `/ai/insights/` and
  `/ai/search/`; each calls `ask_ai_for_user()` through `ai/assistance.py`, and
  none writes a business record. The help assistant sends only the person's
  role-guide title and matching task titles, with source links added locally
  and "I don't know." when the titles do not support an answer. Drafts are
  preview-only textareas labelled "AI draft, please review" with no save or
  send action; translations are labelled "needs review"; insights wrap only
  locally calculated labelled figures and fall back to those figures when the
  wording drops a number.
- [x] Natural-language search maps the request to fixed codes from
  `core.search.SEARCH_CODES` and runs `core.search.search_groups()`, the same
  permission-filtered search service now used by global search. The model never
  writes SQL or a condition; parsed codes are intersected with the allow-list
  before any local query, and all record access still obeys the person's
  permissions and module flags.
- [x] Added `ai/output.py`, which strips links, HTML, command-like lines and
  control characters and caps output length for every feature. Added 19 tests
  in `ai/tests/test_features.py` covering the five services, source linkage,
  "I don't know.", preview-only drafts, review labels, figure fallbacks,
  allow-list search, output cleaning and screen permissions.
- [x] Verification after part 4: focused `ai` suite **57 passed**; full suite
  **1,793 passed** (221 warnings); `ruff check .` clean, `python manage.py
  check` shows only the expected console-email warning, and
  `makemigrations --check --dry-run` reports "No changes detected".
- [x] Part 5, safety: caller text is wrapped as untrusted data with a standing
  data rule; override attempts, hidden role tags and labelled instructions are
  refused before a provider call and logged as zero-token blocked rows. Model
  output is checked for the same patterns and cleaned by `ai/output.py`; drafts
  also get local profanity and harm checks before they can be shown. Added
  cache-backed per-user and per-school/IP burst limits (`AI_RATE_LIMIT`,
  `AI_RATE_IP_LIMIT`, `AI_RATE_WINDOW_SECONDS`) that count blocked attempts too.
  Added redacted, POST-only "Report a bad answer" feedback linked only to the
  person's own `AIUsageLog`, with an institution-scoped review page.
- [x] Part 5 tests: 14 new tests in `ai/tests/test_safety.py` cover injection
  refusal before a call, data wrapping, output instruction removal, draft
  profanity/harm refusal, per-user and per-IP burst limits, feedback redaction,
  foreign-log rejection, GET/unknown-feature rejection and tenant-scoped review.
- [x] Verification after part 5: focused `ai` suite **71 passed**; full suite
  **1,807 passed** (221 warnings); `ruff check .` clean, `python manage.py
  check` shows only the expected console-email warning, and
  `makemigrations --check --dry-run` reports "No changes detected".
- [ ] Part 6, documentation and final verification.
