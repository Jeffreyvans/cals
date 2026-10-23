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

Phase 1 status (2026-10-22): complete. Tenancy, roles, workflow, notifications, audit,
the setup wizard and the bulk import are all in place, with 440 tests passing.

## PHASE 2 - Academics
- [~] AcademicYear/Term hardening (only one active per institution), GradeLevel, SchoolClass/Stream, Subject, ClassSubjectAllocation, class teacher, promotion tool
- [~] College mode: ProgrammeYear, CourseOffering, Cohort, prerequisites, credit limits, capacity - models and enrolment-to-term foreign key are in place; registration service and screen come next
- [ ] Timetable: Room, PeriodDefinition, TimetableSlot clash rules, views by class/teacher/room, printable PDF
- [ ] Course registration with open/close dates, credit limits, prerequisite + capacity checks, registrar override
- [ ] Assessment: AssessmentComponent weights, per-level GradingScale/GradeBand, AssessmentResult, final mark calculation
- [ ] Results engine: term average, cumulative GPA, position with ties, award classification
- [ ] Marks workflow: enter -> moderate -> approve -> release; results hidden until release; fee-balance withholding with bursar override (audited)
- [ ] Report cards + transcripts (PDF, branding, QR verification page)
- [ ] Attendance: register with statuses/absence reasons, summaries, parent absence notification (queued)
- [ ] Academic calendar events on dashboards
- [ ] Data migration of existing Programme/Course/Enrollment/Attendance data; seed command still works

## PHASE 3 - Finance (institutional grade)
- [ ] Chart of accounts + double-entry ledger (Account, JournalEntry, JournalLine, balancing rule, service layer)
- [ ] Financial year + period close that locks posted periods
- [ ] Fee structures with FeeItem lines; bulk invoice generation with preview
- [ ] Adjustments: discounts, bursaries, scholarships, staff-child discounts, waiver approvals
- [ ] Sponsors with sponsor statements
- [ ] Instalment plans, overdue detection, late penalties (capped, scheduled task)
- [ ] Payments: methods incl. EcoCash/mobile money, partial payments, credit balances, sequential receipts with QR
- [ ] Reversals and refunds through approval workflow (reversing entries, never deletes)
- [ ] Payment gateway: `PaymentGateway` base, Paynow implementation (initiate/poll/webhook signature/idempotency), online payment from portal
- [ ] Multi-currency (USD/ZWG + ExchangeRate), reports in base currency
- [ ] Statements (PDF), debtors ageing, daily cash-up, collection vs target, income by fee item, refunds report, CSV/Excel/PDF export
- [ ] Expenses, suppliers, department budgets and variance
- [ ] Bank reconciliation import/match
- [ ] `student_balance(student)` single service used everywhere, tested

## PHASE 4 - Admissions and student lifecycle
- [ ] Public application form (no login) with honeypot, rate limit, tracking link
- [ ] Review workflow states, entry-requirement eligibility check, interview scheduling
- [ ] Offer letter PDF, acceptance + deposit -> atomic student creation (login, number, invoice, welcome message)
- [ ] Lifecycle states with transition services (defer, suspend, transfer, withdraw, graduate) + audit
- [ ] Graduation clearance checklist; certificates PDF with serial + public verification page
- [ ] Student ID cards (front/back, photo, QR) with bulk print
- [ ] Alumni records

## PHASE 5 - Portals and dashboards
- [ ] Student portal: timetable, registration, results after release, balance, receipts, assignments, notices, ID card
- [ ] Parent portal: multi-child switch (`GuardianLink`), finance flag, pay online, results, attendance, messages
- [ ] Teacher/lecturer portal: timetable, rosters, mark entry, attendance, assignments
- [ ] Staff department portals on shared shell
- [ ] Role dashboards with Chart.js (principal, bursar, registrar, HOD, teacher)
- [ ] At-risk nightly scoring + dashboard list + optional class-teacher notification
- [ ] Global search (students, staff, courses, receipts, applications) permission-filtered

## PHASE 6 - LMS
- [ ] Course sites: sections, notes, files, links, visibility by date
- [ ] Assignments: due dates, late policy, text/file submissions, resubmission, rubrics, feedback, marks -> gradebook
- [ ] Quizzes: question bank, random order, time limit, attempts, auto-marking, per-question analytics
- [ ] Discussion forums with moderation
- [ ] Live class links with "join now"
- [ ] Gradebook with weighted categories visible to students
- [ ] Plagiarism flagging between submissions of one assignment

## PHASE 7 - Department modules (each behind a feature flag)
### A. Administration
- [ ] HR: staff file, contracts, documents, recruitment, leave workflow, appraisals, training, discipline (restricted)
- [ ] Payroll: structures, allowances, deductions, tax/pension tables, monthly run, payslip PDF, bank file, ledger posting
- [ ] Procurement and stores: requisition -> quotes -> PO -> GRN -> supplier invoice; stock; assets
- [ ] Estates: work orders, cleaning schedules, utility readings, preventive maintenance
- [ ] Security: visitor log, gate passes, student sign-out, incidents
- [ ] Compliance: policy library + acknowledgements, audits, risk register, data-protection requests
- [ ] Marketing: enquiries, lead pipeline, open days, newsletters via notification system
- [ ] Governance: meetings, agendas, minutes, resolutions, action items, levies
### B. Student services
- [ ] Library: catalogue, loans, renewals, reservations, fines to student account, overdue notices, QR lookup
- [ ] Health (restricted, every view logged, safe flag for teachers)
- [ ] Counselling (restricted, author-only notes, logged)
- [ ] Student affairs: discipline cases, clubs, council, events
- [ ] Sports: teams, fixtures, results, house points, equipment loans
- [ ] Boarding: hostels/rooms/beds, allocation, roll call, exeat workflow
- [ ] Transport: routes, stops, buses, drivers, assignments, pick-up lists, fuel log
- [ ] Catering and tuck shop: meal plans, dietary list, stock, student wallet posted to ledger
- [ ] Careers and industrial attachment: employers, placements, supervisor reports, logbooks, grading
### C. Academic support
- [ ] Examinations: candidate numbers, timetable clash check, invigilation, seating, moderation, results release, ZIMSEC/HEXCO export
- [ ] Research and postgraduate: supervisors, proposals, milestones, ethics, thesis tracking
- [ ] Labs and workshops: equipment, bookings, safety induction, consumables, practical assessments
- [ ] ICT helpdesk: tickets with SLA, devices, account reset requests

## PHASE 8 - API and integrations
- [ ] REST API v1: token auth, pagination, filtering, ordering, throttling, tenant isolation, role permissions, OpenAPI docs
- [ ] Webhooks: configurable URLs, HMAC signatures, retries, delivery log
- [ ] Integrations: Paynow/EcoCash hardened, SMS/WhatsApp adapters, SMTP, Google/Microsoft OIDC SSO, accounting exports, attendance device endpoints

## PHASE 9 - Security and compliance
- [ ] Two-factor (TOTP) with recovery codes for privileged roles
- [ ] Login throttling/lockout, password rules, reset, forced change on first login, session timeout, sign out everywhere
- [ ] Production hardening: DEBUG off, secure cookies, HSTS, CSP, X-Frame-Options, referrer policy
- [ ] Backup and restore management commands (encrypted dump + media) with documented restore test
- [ ] Data protection: retention settings, per-person export, anonymise/delete with exceptions, consent records, privacy notice
- [ ] `docs/SECURITY.md` (OWASP Top 10) + tests for tenant isolation, IDOR, CSRF, XSS, upload abuse

## PHASE 10 - Mobile, offline and languages
- [ ] PWA: manifest, icons, service worker, offline page
- [ ] Offline attendance and mark entry (IndexedDB) with sync + conflict handling + pending counter
- [ ] i18n with English, Shona, Ndebele; per-user language; locale date/number formats
- [ ] Print styles: receipts, statements, report cards, timetables, ID cards, certificates
- [ ] Low-bandwidth mode (no charts/images)

## PHASE 11 - Engineering and deployment
- [ ] Celery workers + beat: messages, report generation, at-risk scoring, penalties, reminders, backups, scheduled emails
- [ ] Query/index review on largest tables; caching for permissions, dashboards, settings
- [ ] Structured logging, Sentry-ready, `/health/` and `/ready/`
- [ ] GitHub Actions CI: lint, type checks, tests with 80% coverage gate, migration check, Docker build
- [ ] Docker multi-stage build + compose (web, db, redis, worker, beat, nginx), `.env.example`, prod compose
- [ ] Deployment guide for a low-cost VPS incl. HTTPS, backups, rollback, disaster recovery

## PHASE 12 - Documentation and demo data
- [ ] README: purpose, architecture, modules, quick start, roles table, env vars, commands
- [ ] docs/: per-role user guides, onboarding guide, API guide, developer guide (tenancy, workflow, ledger)
- [ ] `seed_demo` with four institutions (primary, secondary+boarding, college, Lindenet) and printed logins

---

## Definition of done (project)
- [ ] All phases behind feature flags; every phase "done when" met
- [ ] Migrations clean; `check --deploy` passes with production settings
- [ ] 80%+ coverage; no N+1 on list pages; every view permission-checked; tenant isolation proven
- [ ] `docker compose up` runs and seeds four demo institutions
- [ ] Setup wizard creates a configured institution in under 15 minutes
- [ ] Per-role documentation; PLAN.md ticked; PROGRESS.md gaps listed honestly
