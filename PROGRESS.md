# PROGRESS.md

Started: 2026-10-21. Session 1. Last updated: 2026-11-25 (session 39, optional AI assistance complete).

## Summary of existing state (read)
- Apps: `accounts` (User with role admin/registrar/bursar/lecturer/student/parent + `role_required`), `academics` (Programme/Course/Enrollment/Attendance/Activity/Announcement/TimetableEntry/SchoolClass/Subject/AcademicYear/Term/ExamSitting/GradeBand/Guardian + balances_by_student, student search API, portals for student/parent/lecturer), `finance` (Invoice/Payment/Waiver/MoneyLog with overpayment/waiver guards + receipt numbers, audit + reports + exports).
- Config: `config/settings.py` uses django-environ style helpers in `config/env.py`, SQLite by default, `INSTALLED_APPS` only has accounts/academics/finance, context adds `INSTITUTION`/`CUR`/`MODE`/current year/term. Templates use green/marigold/Figtree via `base.html` and `static/css/app.css`.
- Tests: 276 passed (confirmed). Phase0 tests enforce cross-student 403, overpayment rejected, balance formula, student number generation.
- Gaps vs master prompt: no multi-tenancy (single school), no data-driven roles/permissions, no feature flags/workflow/notifications/audit beyond money log, no core/other apps, no REST API/OpenAPI, no Celery/Redis, no Postgres/Docker/nginx/WeasyPrint/QR in requirements, no pytest config with factories/coverage, no conftest/factories, no per-institution isolation, no setup wizard/bulk import, minimal security/compliance/branding.

## Decisions / assumptions
- Assumption: Treat the existing single-school app as the baseline to extend in place (add `core` app and tenant fields) rather than rewriting; preserve visual identity and existing behavior.
- Assumption: Shared-database/shared-schema tenancy via `Institution` FK + middleware + manager as requested.
- Assumption: Keep backward compatibility for existing models/URLs where feasible while enforcing isolation.
- Decision: `core.models.TenantModel` gives every tenant row an `institution` FK, a tenant-scoped `objects` manager and an unfiltered `all_objects` manager for platform and migration work. `objects` is declared first so it stays Django's default manager.
- Decision: `User.objects` stays unfiltered (sign-in must find a user before the institution is known); `User.tenant_objects` is the institution-scoped manager used by listings and pickers.
- Decision: Django usernames stay globally unique, so a person has one login for the whole installation.
- Decision: The institution-wide audit trail lives at `/audit/` (`audit_trail`); the older money-only log stays at `/finance/audit/` (`audit_log`) and is linked from the new trail.
- Decision: `InstitutionModule` rows are the single source of truth for feature flags. Only when an institution has no rows at all do the type defaults apply, so switching the last module off really switches everything off.
- Decision: Permission checks use codes (`finance.receive_payment`), never role names. `core.access._roles_for_user` unions the explicit `primary_role`/`extra_roles` with the legacy `User.role` mapping, so old accounts keep working.
- Decision: `SchoolSettings.get()` reads through `all_objects`, not the tenant-scoped manager, so it can be handed a specific institution while another one is current. It uses `get_or_create`, so an institution never gets two settings rows.
- Decision: The demo data lives in `academics/demo.py` (`seed_demo(institution, prefix, mode)`) so the `seed` command and the setup wizard share one implementation. A `prefix` keeps usernames unique when more than one institution is seeded.
- Decision: `admin.create_institutions` is a platform-only permission (`core.roles.PLATFORM_ONLY`). It is granted to the Django superuser only, is never seeded into an institution role, and `ensure_roles` deletes it from any role that carries it, so an institution admin can never create a second institution. `core/migrations/0003_platform_permissions.py` cleans up the grants the earlier seed left behind.
- Decision: The tenant filter must be resolved when the query is compiled, not when the queryset is built. `core.tenancy.CurrentInstitutionId` is a `models.Func` that reads the current institution at SQL build time; `use_institution` sets it for tests and background work.
- Decision: The bulk import accepts both CSV and `.xlsx` (the prompt says "Excel or CSV"), with `openpyxl` added to `requirements.txt`. A file is read into rows, the user maps columns to fields, the rows are checked and shown, and only the confirmed run writes anything.
- Decision: An import keeps its rows, mapping and error list on `ImportBatch` (JSON columns) instead of a file on disk, so the preview can be revisited and the error report can be downloaded later. A file is capped at 5 MB and 5000 rows.
- Decision: The import writes through `use_institution(institution)` and reads through `all_objects`, so an import always writes into the institution that started it, whatever the ambient current institution is.
- Decision: `Enrollment.term` is a `Term` foreign key, not the name of a term. New enrolments must choose a real term; the column stays nullable so old rows that could not be matched are never silently deleted. `repair_enrollment_terms` fills those rows with the current term (or the earliest term) of their institution.
- Decision: The existing `Programme`/`Course` tables stay; Phase 2 adds `ProgrammeYear`, `Cohort`, `CourseOffering` and `CoursePrerequisite` alongside them rather than renaming the working tables.
- Decision: Registration rules live in `academics/services.py` (`registration_issues`, `register_student`, `drop_enrollment`, `promote_next_waiting`, `registration_catalogue`) so the views stay thin and the rules are tested without HTTP.
- Decision: A full offering puts a student on the waiting list instead of refusing them. Only `registered` rows consume a place; dropping a registration promotes the longest-waiting student.
- Decision: A registrar or administrator may override any registration rule, but only with a written reason, and the override is written to the audit trail. A duplicate active registration is never overridden.
- Decision: Registration stays available until the term is closed when no registration dates or enrolment deadline are set, so demo and small institutions can use it without extra setup.
- Decision: College setup lives on one screen (`/programmes/structure/`) with programme years, cohorts, offerings and prerequisites together, so the registrar does not have to jump between four pages while setting up registration.
- Decision: New college students may be given a programme year and cohort when their account is created; those fields are hidden in school mode. A programme year or cohort from another programme is rejected.
- Decision: assessment component weights are relative rather than required to total 100, so a teacher can set up the parts before fixing the split. A final mark is only written once every component has a mark, and it is recalculated whenever a component is added, changed or removed.
- Decision: a course or subject with no components keeps the original single `Enrollment.mark` field, so existing rosters, marks and reports keep working unchanged.
- Decision: component marks are stored per enrolment in `AssessmentMark`, not on the component, so the same course offering can be marked for each student and term.
- Decision: results are credit-weighted. A course enrolment uses the offering's credits (falling back to the course's), and a subject counts as one credit, so school-mode averages are ordinary means.
- Decision: GPA is the credit-weighted average of the points in the grade bands, not of the marks, so changing a band's points changes the GPA without touching the marks.
- Decision: class position ranks credit-weighted term averages within the student's cohort, else their school class, else their programme year, else their whole programme. Equal averages share a place and the next place is skipped (1, 2, 2, 4).
- Decision: an application is a public enquiry, not a student. Only `accept_offer` creates the login, student number, invoice and welcome message, and it does so in one transaction so a failed acceptance never leaves a half-created student.
- Decision: the public application form is protected by a honeypot, a per-institution/IP rate limit and a UUID tracking link; the form is valid without a login but the institution is resolved the same way as the rest of the app.
- Decision: eligibility is advisory. A missing requirement leaves `eligible` as `None` (review by a person); only an explicit requirement produces a pass/fail, and an officer can still review or make an offer with a note.
- Decision: offers carry the deposit, acceptance deadline and a public token. The deposit is recorded through the normal payment service, so it posts to the ledger and produces a receipt like any other money row.
- Decision: lifecycle changes go through `transition_student`, which validates the allowed edges and writes a `LifecycleTransition`; graduation is deliberately excluded and must use `graduate_student`, which also enforces clearance.
- Decision: certificates and ID cards get stable UUID tokens and public verification pages that confirm the document without exposing marks or personal detail. A card is issued once per student and re-issuing replaces the photo/validity rather than duplicating it.
- Decision: uploaded application documents and card photos live under `MEDIA_ROOT`; the development URLconf serves them only while `DEBUG` is on.
- Decision: alumni records are created from a graduated student and keep the graduation award/date, so the alumni list is a view of history rather than a second student table.
- Decision: a `CollectionTarget` is a dated goal, not a budget; the collection-vs-target report compares it with payments whose `paid_on` falls inside its period, so a target can match a term, a month or the whole year.
- Decision: every finance export goes through one `_export_response` helper so CSV, Excel and PDF stay in step. `?format=csv|xlsx|pdf` selects the output and an unknown value falls back to CSV.
- Decision: the daily finance job (`run_finance_jobs`) is a management command, not a Celery task yet, so it runs from any scheduler and can be simulated with `--as-of`. Penalties are idempotent per rule/instalment, and reminders are capped at one per student per channel per day.
- Decision: reversing a journal entry posts a mirror entry with source type `Reversal`; the original entry and its source row stay in place, and a second reversal is refused. Refunds remain the way to reverse a payment's business effect.
- Decision: a parent sees fees only when both the guardian link's `can_view_finance` flag and the `parent_portal.view_finance` permission allow it; the child page hides the balance, invoices and the pay link otherwise.
- Decision: online payment is self-service. A student may pay their own invoice and a finance-visible parent may pay a linked child's invoice without holding the staff `finance.receive_payment` code; everyone else is refused.
- Decision: a lecturer's assignment is fanned out to one `Activity` per enrolled student, because the student portal already reads activities per student. The lecturer screens group those rows back together by title, due date and course/subject.
- Decision: at-risk scoring is one shared function used by the dashboard and the nightly command. The score is the number of failed rules (fees owed, attendance below 75%, average below 50); `AtRiskScore` keeps one row per student per day, and the command notifies class teachers at most once per student per day.
- Decision: the department portal is a staff page, not an admin page. It shows the departments a person belongs to or heads, their staff profiles and budget variance, and refuses student and parent accounts.
- Decision: the Phase 7 administration registers share one registry-driven list/create/edit/delete screen. Each kind declares its model, form, search fields, columns and optional row action, but every entry point still checks its module feature flag and module permission.
- Decision: `operations._guard()` defaults to the module's own permission codes when no explicit permission is passed. This prevents an approver-only role from browsing or editing an unrelated module just because it holds one approval code.
- Decision: payroll uses flat-rate active `PayrollRule` rows for tax and pension. Saved payslips are the source for the payslip PDF, bank CSV and ledger posting, so all three always agree.
- Decision: awarding a quotation rejects the other received quotes, marks the requisition `ordered`, and raises an approved purchase order from the winning quote's lines (or one line from its headline amount). This keeps the order, stock and ledger flow in one service.
- Decision: a supplier invoice is matched to the purchase order (or goods received note) total. A difference leaves it `disputed` with the variance recorded and creates no expense; an exact match posts the expense through the normal finance service, so the ledger sees it like any other expense.
- Decision: the Phase 7B/7C department apps share the `core.registers` screens but declare their own module fixture flag and permission codes. `core/module_directory.py` adds one "School services" hub that only lists modules the current user can actually open.
- Decision: reading a register only needs any module permission, but creating, editing or deleting needs the module's `.manage` code when it declares one. This lets a librarian with `library.borrow` browse loans without silently gaining catalogue write access.
- Decision: health and counselling registers are marked `sensitive` in their registries, so list, detail and JSON views write a `SensitiveViewLog` row on every access.
- Decision: a counselling session is visible only to its own counsellor unless the role holds `counselling.view_all_notes`; the author scope is applied by the registry queryset, so the list, detail and JSON views all obey it.
- Decision: a health profile is shared with teachers only when `share_with_teachers` is set, and the teacher-safe screen shows only the `teacher_note`, emergency contact and the student/class; the full history stays with health staff.
- Decision: library overdue notices, renewals and fines are services: renewals are capped and refused when overdue, notices are sent once per loan per day, and a fine creates one ordinary finance invoice linked back to the loan.
- Decision: an exam paper is checked twice. Saving a paper checks the sitting window, the venue and the invigilator; the clash screen also compares candidates, because candidates are usually entered after the timetable is built.
- Decision: seat numbers are allocated from 1 in candidate-number order, and releasing a sitting publishes every candidate result and marks the sitting published.
- Decision: an equipment loan takes a copy only while it is on loan. Saving an already-returned loan does not take another copy, and dating the return restores one.
- Decision: automatic boarding allocation is first-in-first-out by admission date (then pk), and it only previews until a person confirms the run.
- Decision: the transport pick-up list and its CSV both default to the first route when none is chosen, and times are shown as HH:MM.
- Decision: a catering stock purchase posts one ordinary finance `Expense` for quantity x unit cost, so the cost reaches the ledger exactly like any other expense.
- Decision: completing an account reset generates the temporary password in memory and returns it once to the caller; it is never written to the database.
- Decision: the ICT SLA clock stops on the first technician response, which also assigns the ticket to that technician when it is still open.
- Decision: `Attendance.status` is the source of truth and the stored `present` flag follows it: present and late count as attended, absent and excused as missed. The flag is kept so the older summaries and the at-risk rule keep working without a rewrite.
- Decision: a guardian is told about an absence once per recorded date. `notify_absence` flags the row, so editing the reason or re-saving the register never sends a second message; excused absences are intentionally silent.
- Decision: a calendar event without a class is institution-wide; one with a class reaches only that class's students and the parents of linked children. Past events drop off the dashboards, but an event that is still running today stays until it ends.

## Bugs found
- None blocking baseline; tests pass. `makemigrations --check` clean and `ruff check .` is clean as of session 10.
- `core/roles.py ensure_permissions()` raised `NameError` (it used `PERMISSIONS` but only imported `PERMISSION_CODES`); fixed.
- `core/access.py _roles_for_user()` never fell back to the legacy role map: `Q(pk__in=[])` has one child, so the `if not q.children` guard was always false. Rewritten.
- `core/modules.py enabled_modules()` fell back to the type defaults whenever no module was *enabled*, which brought switched-off modules back. Now it only falls back when the institution has no switch rows at all.
- `core/views.py role_matrix()` used `RolePermission.objects.get_or_create(role=..., permission__code=...)`, which is not a valid lookup. Rewritten to compare and add/remove explicitly.
- The nav pointed at the money-only audit log; the bursar now gets the institution-wide trail and the money log is one click away.
- pytest ran in ~9 minutes because `config/settings.py` only downgraded the password hasher when `"test" in sys.argv`. Now it also detects pytest, and the suite runs in seconds.
- `academics/context.py` served the global `INSTITUTION_NAME`/`CURRENCY`; it now serves the current `Institution` record (keeping `CUR` as the template key because the existing templates use it).
- Ruff `DJ012` wants the second manager declared before the first one, which would have made the unfiltered manager Django's default. `all_objects` carries a `noqa` instead and `objects` stays the default manager.
- `SchoolSettings.get(institution)` used the tenant-scoped manager, so asking for another institution's row returned nothing and then tried to create a second row for that institution (IntegrityError). Now it looks the row up through `all_objects`.
- The demo seed only created the settings row when `--mode` was given, so `SchoolSettings.objects.exists()` was false after a plain `seed`. The row is now always created.
- The tenant filter was resolved when the queryset was built, so a queryset captured at import time (or a model form built at import time) froze whichever institution was current then. `CurrentInstitutionId` now reads the value when the SQL is compiled.
- `core/wizard_views.py` merged the incoming step values assuming they were all dictionaries; a missing or empty step made the wizard crash. `_merged_values` now tolerates anything that is not a dictionary.
- The demo admin created by the wizard was made a Django superuser. It is now `is_staff=True` only; platform powers stay with the real superuser.
- The wizard seeded `admin.create_institutions` into the institution admin role. The permission is platform-only now (see the decisions above).
- The bulk import views read the batch id from the query string, but the URLs pass it as a `pk` argument. The views now take `pk` from the URL, and the forms post to the batch URL.
- The bulk import wrote fee structures through the tenant-scoped manager, so the second row of a structure could not find the structure the first row had just created. The import now runs inside `use_institution(institution)`.
- The import tests read the batch through `ImportBatch.objects`, which outside a request resolves to the default institution. They read it through `all_objects` now, like the other cross-tenant tests.
- Converting `Enrollment.term` from text to a `Term` foreign key broke the demo seed, the setup wizard, the enrolment form, the exam candidate list and the test fixtures that still passed `"Term 1"`. They now create or reuse a real `Term` and pass the object.
- `_enrolled_this_term()` still filtered `term=term.name`, so the exam page found nobody after the conversion. It filters on the term itself now.
- The report card grouped enrolments by `Term` objects while `Attendance.rates_by_term()` returned label strings, so every report showed zero attendance. Both sides use the label now; a null term is shown as "No term" instead of crashing.
- An enrolment could keep a null term when its institution had no term at migration time. `repair_enrollment_terms` repairs those rows (with `--dry-run`); a test covers both a repaired row and an institution with no term at all.
- `CourseOffering.registered_count` counted waitlisted students as if they held a place, so dropping a registered student did not free the seat. It now counts only `registered` rows.
- `_annotate_components` built a dictionary from three-column `values_list` rows, which raised `ValueError` as soon as a roster had component marks. It now keys a dict by `(enrollment_id, component_id)`.
- `AssessmentComponentForm` could never validate: the model's `clean()` insists on a course or a subject, but neither field is on the form. The view now passes an instance with the owner already set.
- The assessment setup screen initially created components without an owner, so a form that did validate would have saved a row belonging to neither a course nor a subject. The owner is set before validation now.
- The student portal linked to `my_id_card_pdf` without the `admissions:` namespace, so every student page raised `NoReverseMatch` (13 tests failed). The link now reverses through `admissions`.
- `assignment_submit` used `AssignmentSubmissionForm` without importing it, and the form had no file field even though the view reads one; the import and the file field were added.
- Uploaded files had no `MEDIA_ROOT`, so test and demo uploads were written into the project root (for example `admissions/1/...`). `MEDIA_ROOT`/`MEDIA_URL` are now configured, `.env.example` documents `EDUSPHERE_MEDIA_PATH`, `media/` is ignored, and the development URLconf serves media only with `DEBUG` on.
- The new `examinations.Exam.term` used `related_name="exam_sittings"`, which clashed with `academics.ExamSitting.term` and made `manage.py check` fail. It is `exam_sessions` now.
- `library.Loan.save()` always decremented the available copies on insert, even for a loan entered with a `returned_on` date. It only takes a copy when the loan is not already returned.
- Registry create, edit and delete used the same read permission set as the list pages, so a borrow-only role could change the catalogue. Writes now require the module's `.manage` code when it has one.
- The Phase 7B/7C app scaffolds had unused imports (Django admin/render placeholders); `ruff check . --fix` removed them.
- The registry JSON endpoint passed column values such as `Student` objects straight to `JsonResponse`, so the counselling API raised `TypeError: Object of type Student is not JSON serializable`. The shared serialiser now turns model values into their labels (dates and numbers were already handled by Django's encoder).
- The counselling tests compared an escaped apostrophe and an exact topic against a label of the form "student - topic"; the assertions now look for the stable fragments.
- The inherited `institution` reverse accessor (`%(class)s_records`) clashed for same-named models in two apps (`sports.Equipment`/`labs.Equipment` and `catering.StockItem`/`operations.StockItem`), which made `manage.py check` fail. The two new models declare an explicit `related_name`.
- Several Phase 7B/7C models had been added without migrations, so their tables did not exist and the tests failed with `no such table`. New migrations cover boarding, library, careers, catering, ict_helpdesk, labs, research, sports and transport.
- The transport pick-up CSV chose no route when the query string had none, so it exported only a header row; it now defaults to the first route like the page.
- Pick-up times rendered as `6:45 a.m.` and fuel totals as `40.5`; the templates now render `HH:MM` and two decimal places.
- Boarding allocation ordered waiting boarders by surname, so the last-created boarder could be placed ahead of an earlier one; it now orders by admission date and pk.
- The sports loan test helper passed `due_on` twice (a duplicate keyword error), and the "unavailable" test saved the loan before validating it; both now merge/build first.
- `Attendance.save()` recomputed `present` from the status but could not persist it through `update_or_create`, which saves only the keys in `defaults`; the model now adds `present` to `update_fields` whenever the status is written.
- The attendance register's attended/missed counters compared the display letter (`P`, `A`) with the status values (`present`, `absent`), so every student showed zero; they now count the statuses.
- The old attendance tests and the demo seed still created rows with `present=` only; with the status defaulting to present their "absent" rows were silently present. They now write statuses.

## What's done
- [x] Read all files; ran `manage.py check` (ok), `makemigrations --check` (ok), `manage.py test` (276 passed), installed test/dev tools.
- [x] Created PLAN.md and PROGRESS.md, git init + baseline commit.
- [x] Phase 0 complete: pytest config, factories, conftest, Makefile, ruff/black/pre-commit, baseline tests.
- [x] Phase 1 tenancy: `core` app with `Institution`, `TenantModel`, `TenantManager`, `InstitutionMiddleware` (user -> subdomain -> path prefix), `use_institution` context manager and a cached default-institution lookup.
- [x] Every academic and finance row now carries its institution, with per-institution unique student numbers, receipt numbers, credit-note numbers, course/programme/subject/class/year codes and grade-band letters.
- [x] Data migration `core/0002_default_institution` creates the default institution (Lindenet Institute of Technology), seeds its module switches, hands every existing row to it and seeds the default role matrix.
- [x] Roles and permissions as data: `Role`, `Permission`, `RolePermission`, `ensure_roles`, legacy role map, `permission_required_code`, `user_has_code`, and the `perms` / `perms_code` / `modules` / `module_on` template tags.
- [x] New screens: institution settings (details, branding, module switches, terminology), roles and permissions with a working permission matrix, role create/edit/delete, notifications page, institution-wide audit trail with filters and CSV export, restricted-record-view log.
- [x] Notification bell in the shell with an unread count, in-app notifications, outbound message queue with console/SMTP/SMS/WhatsApp providers.
- [x] Feature flags hide modules from the menu and return 404 from their pages.
- [x] Departments and staff profiles: screens for both, department hierarchy, department head.
- [x] Approval workflow engine: templates with ordered steps, requests, actions, submit/approve/reject/return/cancel services, notifications to the people who must act.
- [x] Institution types with terminology labels (class/grade/pupil/teacher/programme) per type, editable per institution.
- [x] Demo data extracted into `academics/demo.py` so it is reusable; `seed` takes `--institution` and `--prefix`.
- [x] `core/tests` covers tenancy isolation, role seeding and the permission matrix, notifications and the audit trail, the module switches, departments, staff and workflows; `academics/tests/test_settings.py` covers the settings lookup.
- [x] Setup wizard: one flow for details, branding, year and term, module switches, grading template, currency, first administrator and optional demo data, with a check that only a superuser or a holder of `admin.create_institutions` may run it; `core/branding.py` owns the logo and colour handling.
- [x] Bulk import: upload (CSV or `.xlsx`), column mapping, validation preview, confirmation, result screen and a downloadable error report; students, staff, courses, subjects and fee structures can be imported; `finance.FeeStructure`/`FeeItem` were added so fee structures have a target; `core/tests/test_import.py` has 40 tests.
- [x] Lint is clean (`ruff check .` -> all checks passed).

## Phase 1 checklist (what is finished and what is not)
| Item | State |
| --- | --- |
| Multi-tenancy | **done** - `Institution`, tenant manager, middleware, per-institution uniqueness, isolation tests |
| Institution types + terminology | **done** - type presets plus a per-institution override on the settings screen |
| Feature flags | **done** - `InstitutionModule`, hidden menus, 404 on disabled pages, tests |
| Roles and permissions as data | **done** - roles, permission catalogue, matrix screen, seeded defaults, decorator and template tag |
| Departments | **done** - `Department` and `StaffProfile` with screens |
| Approval workflow | **done** - templates, requests, actions, services, step notifications |
| Notifications | **done** - `Notification`, `MessageTemplate`, `OutboundMessage`, providers, bell, notifications page |
| Audit log | **done** - `AuditEntry` and `SensitiveViewLog`, filters, CSV export, restricted-view log |
| Setup wizard | **done** - one flow for details, branding, year/term, modules, grading template, currency, first admin and optional demo data, with platform-only access |
| Bulk import (CSV/Excel) | **done** - upload, column mapping, validation preview, confirm, result and error report; five record kinds, 40 tests |

Phase 1 is complete. The whole suite is 440 tests, all passing.

## Phase 2 progress
- [x] `Enrollment.term` is a real `Term` foreign key (migrations 0011-0013); demo data, the setup wizard, forms, views and tests all use a real term.
- [x] `ProgrammeYear`, `Cohort`, `CourseOffering` and `CoursePrerequisite` models, plus `Student.programme_year`/`cohort` and `Enrollment.offering`/`status`.
- [x] `repair_enrollment_terms` management command for legacy rows with no term (`--dry-run` supported); dev database currently has 0 such rows.
- [x] Course registration service and screen: registration windows, term closure, prerequisites, credit limits, capacity waiting list, duplicate guard, registrar override with a required reason, audited drop and waitlist promotion, self-service and staff screens, 12 tests.
- [x] Demo data now creates a DIT programme year, cohort and course offerings, and links the demo college students and enrolments to them.
- [x] Management screen for programme years, cohorts, course offerings and prerequisites (`college_structure`), with form validation and 10 tests.
- [x] The student registration form now offers a programme year and cohort in college mode and rejects mismatches.
- [x] Timetable clash detection verified and extended with tests: lecturer, class/stream, course, venue and overlapping times; back-to-back lessons are allowed. Existing views cover class, teacher and student timetables.
- [x] Assessment components: `AssessmentComponent`/`AssessmentMark`, relative weights, per-student component marks, weighted final mark recalculated when setup changes; setup screen, roster component fields and component columns on the mark sheet, with 8 tests.
- [x] Results engine: credit-weighted term average and cumulative GPA, class positions with shared places, and term summaries on the report card, with 10 tests.
- [x] Marks workflow (migration `0015`): `ResultSheet` moves through submitted -> moderated -> approved -> released with an audit entry at every step; a workflow screen lets the registrar review, return or release a term; marks lock once moderated; students and parents see released results only; fee balances withhold results, and a bursar can release one term with a written reason through an audited `ResultClearance`; report cards and portals show pending or withheld notices, with 21 tests.
- [x] Award classification (migration `0016`): `AwardRule` bands by programme level or for all programmes, built-in degree and diploma scales, a projected award from cumulative GPA on the report card, and admin add/remove screens on the grade-scale page; hidden or withheld results never contribute, with 14 tests.
- [x] Report cards and transcripts (migration `0017`): `DocumentVerification` gives each student a stable UUID token per document kind; ReportLab builds branded PDFs with the institution logo, colours, term tables, GPA/award summary and a QR code; staff, students and linked parents can download report cards and transcripts; a public `/verify/<token>/` page confirms the document without exposing marks; results still respect release and fee-withholding rules; `reportlab` and `qrcode[pil]` are now declared, with 10 tests.

The whole suite is now 574 tests, all passing; `ruff check .` is clean and `makemigrations --check` reports no changes.

## Phase 3 progress
- [x] Chart of accounts and double-entry ledger (`Account`, `FinancialYear`, `JournalEntry`, `JournalLine`); every invoice, payment, waiver, discount, credit, refund and expense posts a balanced entry through `finance/services.py`.
- [x] Financial years lock posting when closed; close/reopen screens.
- [x] Fee structures with `FeeItem` kinds; bulk invoice generation with preview and duplicate protection.
- [x] Discounts/bursaries/sponsorships, sponsor statements, instalment plans, overdue detection and capped late penalties (manual run).
- [x] Payments with cash/bank/card/mobile money, partial payments, overpayment as student credit, sequential QR-verifiable receipts.
- [x] Refunds with request/approve/reject and reversing journal entries; never deletes money rows.
- [x] `PaymentGateway` base + Paynow initiate/poll/webhook with signature checks, idempotency keys and an online-payment screen.
- [x] Payment currency, exchange rate and base amount; no separate `ExchangeRate` table yet.
- [x] PDF statements, debtors ageing, daily cash-up, income by fee item, refunds/expenses reports.
- [x] Suppliers, expenses, department budgets and variance.
- [x] Bank statement CSV import and automatic receipt matching.
- [x] `student_balance(student)` is the single service and now backs `Student.balance`; bulk reporting still has a separate path.
- [x] Collection-vs-target report with dated goals (`CollectionTarget`), collected/variance/percent per target, and create/delete screens.
- [x] Full CSV/Excel/PDF export matrix for debtors, collections, audit log, trial balance, income statement, balance sheet, income by item, refunds, expenses, daily cash-up, budgets and collection targets.
- [x] Scheduled penalty/reminder runs via `run_finance_jobs` (per active institution, idempotent penalties, one reminder per student/channel/day).
- [x] Formal journal reversal endpoint (`reverse_journal_entry` + `journal_reverse`) that posts a mirror entry, requires a reason and refuses double reversal.

## Phase 4 progress
- [x] Public application form with a honeypot, per-institution/IP rate limiting, document upload, results entry and a UUID tracking link; the tracking page shows status, interview and offer without a login.
- [x] Admissions office screens: application list/detail, review states, interview scheduling, eligibility check, offer creation, document download and entry requirements per programme.
- [x] Offer letters as branded PDFs; a public acceptance page takes the deposit method/reference, and `accept_offer` atomically creates the student, login, student number, invoice and welcome message.
- [x] Lifecycle states with validated transitions (defer, suspend, transfer in/out, withdraw, return), audited with a required reason; graduation is restricted to the graduation service.
- [x] Graduation clearance checklist, graduation service, certificate issuance with serial/UUID and a public verification page.
- [x] Student ID cards with photo, QR token, bulk print and a public verification page; students can download their own card from the portal.
- [x] Alumni records created from graduated students, with list and detail screens; admissions is behind a feature flag and its own permission codes.

The whole suite is now 644 tests, all passing; `ruff check .` is clean and `makemigrations --check` reports no changes.

## Phase 5 progress
- [x] Student portal now lists assignments and submissions; students can open, submit and resubmit work and download their ID card, report card and transcript.
- [x] Parent portal has a multi-child switch, respects the finance consent flag, and offers pay-online for a linked child's outstanding invoices.
- [x] Lecturer portal shows the current timetable, course/subject rosters, mark and attendance entry, and assignment management with submissions.
- [x] Department portal on the shared shell shows a staff member's departments, colleagues and budget variance.
- [x] Role dashboards with Chart.js (collections and class mix) plus the at-risk list.
- [x] At-risk scoring (`academics/risk.py`, migration `0020`): shared rules, `AtRiskScore` daily rows, and `score_at_risk` with `--institution`, `--as-of`, `--dry-run` and `--no-notify`.
- [x] Global search across students, staff, courses, receipts and applications, filtered by permission and tenant.
- [x] 14 new Phase 5 tests cover lecturer assignments, parent finance consent and self-service payment, department access and the nightly at-risk run.

The whole suite is now 664 tests, all passing; `ruff check .` is clean and `makemigrations --check` reports no changes.

## Phase 6 progress
- [x] New `lms` app and migration `0001_initial`: `CourseSite`, dated `SiteSection`/`ContentItem`, `Assignment`, `Submission`, `Rubric`, `Quiz`, `Question`, `Choice`, `MatchingPair`, `QuizAttempt`, `QuizAnswer`, `Forum`/`ForumThread`/`ForumPost`, `LiveSession`, weighted `GradeCategory`/`GradeItem`/`GradeEntry` and `PlagiarismReport`.
- [x] Course site screens for sections, notes/files/links, assignments, quizzes, forums and live classes; published/draft and date-based visibility are enforced for students.
- [x] Assignments support due dates, late acceptance/penalty/block policies, text/file submissions, resubmission and rubric criteria; grading applies late penalties and caps marks, then syncs the gradebook.
- [x] Quizzes support four question types (multiple choice, true/false, short answer, matching), random question/choice order, time limits, attempt limits, automatic marking, manual marking for unkeyed short answers, regrading and per-question analytics.
- [x] Discussions support threads, replies, pinning, locking, approval and post flagging; live classes expose a join link only from 15 minutes before the start.
- [x] Weighted gradebook with default Assignments 40 / Quizzes 30 / Coursework 30 categories, category/item setup, manual score entry and student-visible published items.
- [x] Internal plagiarism check compares submissions in one assignment with `difflib`, stores similarity reports and flags pairs at or above 35%.
- [x] Added `lms` to all institution module presets, `lms.manage`/`lms.grade` to teaching roles, LMS navigation, URLconf, templates and 16 tests.
- [x] Fixed datetime-local form parsing so assignment/quiz/live-class forms accept the browser's `YYYY-MM-DDTHH:MM` values; quiz grade items now refresh when question points change.
- [x] Known limits: similarity checking is internal text comparison (no external plagiarism service), live classes are join-link based (no embedded conferencing), and content is plain text/file/link rather than a rich-text editor.

The whole suite is now 680 tests, all passing; `ruff check .` is clean and `makemigrations --check` reports no changes.

## Phase 7 progress
### A. Administration
- [x] New `operations` app and migration `0001_initial`: HR, payroll, procurement/stores, estates, security, compliance, marketing and governance models.
- [x] Registry-driven module hub and registers for every administration kind, with search, create, edit, delete and audit entries.
- [x] HR contracts, documents, leave decisions, appraisals, training, discipline, vacancies and job applications; leave decisions notify the staff member.
- [x] Payroll salary structures, flat-rate tax/pension rules, monthly runs, payslip generation, approval, individual payslip PDF, bank CSV and ledger posting through `finance.post_payroll`.
- [x] Procurement requisitions, requisition lines, purchase orders, order lines, goods received notes, stock items/movements and assets; stock movements update quantities.
- [x] Procurement quotations (`Quotation`, `QuotationLine`) with a requisition comparison page; awarding a quote rejects the others, marks the requisition ordered and raises an approved purchase order.
- [x] Supplier invoices matched to the order or receipt total; an exact match posts a finance expense, a difference records the variance and leaves the invoice disputed.
- [x] Estates work orders, cleaning schedules, utility readings and preventive maintenance.
- [x] Security visitor log, gate passes, student sign-out and incidents.
- [x] Compliance policies, acknowledgements, audits, risk register and data-protection requests.
- [x] Marketing enquiries, campaigns, open days and newsletters queued through the notification system.
- [x] Governance meetings, agendas, minutes, resolutions, action items and levies.
- [x] Added `estates_officer`, `compliance_officer` and `governance_officer` roles, plus the permission-gated Administration navigation link.
- [x] 31 operations tests cover module access, tenant isolation, CRUD, stock updates, leave decisions, payroll end to end, payslip PDF, policy acknowledgement, newsletter sending, quotation awarding and supplier-invoice matching.
- [ ] Known follow-up: payroll tax/pension is flat-rate rather than bracket tables.

### B/C. Student services and academic support (registers in place)
- [x] New apps `library`, `health`, `counselling`, `student_affairs`, `sports`, `boarding`, `transport`, `catering`, `careers`, `examinations`, `research`, `labs`, `ict_helpdesk` with models, forms, registry entries, URLs, migrations and feature flags.
- [x] Every app uses the shared registry screens with search, create/edit/delete, tenant isolation and audit entries; stock-style writes update their sources (loans, wallets, stock movements).
- [x] Health and counselling registers are `sensitive`, and every list/detail/API view writes a `SensitiveViewLog` row.
- [x] `core/module_directory.py` adds a "School services" hub linked from the staff navigation; it only lists modules the user can open.
- [x] Added `sports_officer`, `student_affairs_officer`, `catering_officer`, `careers_officer`, `research_officer` and `lab_technician` roles; existing librarian/nurse/counsellor/warden/transport/ICT roles already cover the rest.
- [x] Model-flow tests for each app (loans, wallets, exeats, route stops, dates, unique memberships/registrations, ticket comments) plus cross-module access, tenancy and sensitive-log tests.
- [x] Counselling notes are author-only unless a role holds `counselling.view_all_notes`; the scope covers list, detail and JSON views.
- [x] Health has a teacher-safe alerts screen that shows only profiles flagged `share_with_teachers`, never the full history.
- [x] Library renewals (capped), overdue notices (once per loan per day), fines posted to the student account through a normal invoice, a quick ISBN/title/author/shelf lookup, and stable QR tokens with a token/ISBN lookup plus a PNG QR image.
- [x] Examinations now check venue, invigilator and candidate clashes, allocate seat numbers in candidate order, move each paper through enter -> submit -> moderate -> release with marks locked after moderation and a reopen path that withdraws results, release a whole sitting only when every paper is moderated, and export the candidate list plus ZIMSEC/HEXCO result CSVs.
- [x] Sports equipment loans track availability (`available`/`total`), refuse a loan when none are free, and restore stock when the item is returned.
- [x] Boarding automatic allocation previews and applies room/bed assignments in admission order; boarders beyond capacity are listed as waiting.
- [x] Transport pick-up lists (morning/afternoon, by stop order) with HTML and CSV output, and a fuel-log report totalled per vehicle with a date range and CSV export.
- [x] Catering stock items, stock purchases (quantity on hand) and the purchase cost posted to finance as an expense (and therefore the ledger); reorder levels are flagged.
- [x] Careers supervisor reports and attachment grading (`grade_placement` sets a mark/letter and completes the placement).
- [x] Research milestones (overdue detection) and one thesis per project.
- [x] Labs safety inductions, consumables/issues and practical assessments.
- [x] ICT SLA policies with response/resolution targets and overdue detection, plus the account-reset workflow (request, complete with a one-time temporary password, or reject) and a manage screen.

The whole suite is now 814 tests, all passing; `ruff check .` is clean and `makemigrations --check` reports no changes.

## Phase 2 completion - attendance and calendar (session 14)
- [x] `Attendance.status` (present/late/absent/excused), `reason`, `recorded_by` and `guardians_notified`; migration `0021` derives a status for legacy rows from the old `present` flag.
- [x] The roster screens post a status per student plus an optional absence reason; `present` is kept in step with the status (`present` and `late` count as attended, `absent` and `excused` as missed).
- [x] The attendance register shows a letter per date (P/L/A/E) with the reason as a tooltip, per-date totals and per-student attended/missed counts.
- [x] `notify_absence` tells each linked guardian once per recorded absence: an in-app notification plus any queued email/SMS channels. Present, late and excused rows never notify, and re-saving a date never repeats the message.
- [x] Student and parent pages list recent absences with their status and reason.
- [x] Academic calendar: `CalendarEvent` (holiday/exam/meeting/deadline/event) with an optional class, `when` label and `upcoming()` visibility. Managed on the years and terms screen, shown on the staff, role, lecturer, student and parent dashboards; students see whole-institution and own-class events, parents also see any linked child's class events.
- [x] Timetable PDF (`build_timetable_pdf`): a landscape branded grid of the lessons that actually exist, one column per day and one row per time slot, downloadable by the registrar (with class and teacher filters) and by each teacher and student for their own timetable.
- [x] Fixed `Attendance.save()` to add `present` to `update_fields` when the status is saved, because Django's `update_or_create` only writes the keys it was given and the stored flag was going stale.
- [x] Fixed the register's attended/missed counters, which compared the display letter (P/A) with the status strings and always showed zero.
- [x] Updated the old attendance tests to the status API and added 26 tests for statuses, reasons, register summaries, one-time guardian notification, calendar visibility and the timetable PDF. The demo seed now writes statuses.
- [x] Data migration and seed verified: `repair_enrollment_terms` covers null terms, migration `0021` backfills attendance statuses, and `seed` still creates demo attendance.

## UI redesign - navy and light blue theme (session 15)
- [x] Rewrote `static/css/app.css` around design tokens: navy `#1E3A8A`, light blue `#3B82F6`, off-white `#F8FAFC`, white cards, slate text `#0F172A`/`#64748B` and `#E2E8F0` borders. Every class the templates already used is preserved, so all ~170 screens inherit the new look without business-logic changes.
- [x] Rebuilt the application shell in `templates/base.html`: fixed navy sidebar with grouped, icon-led navigation, institution brand block, user card and sign-out, sticky translucent top bar with notification bell, and a mobile drawer with overlay, Escape close and active-link highlighting.
- [x] Restyled cards, stat tiles, balances, tables (scroll wrappers, sticky-style headers, row hover), forms, filters, search, buttons, badges, messages, steps, event lists, module grids, empty states and print output from the same tokens.
- [x] New split-panel sign-in page; public application, tracking, offer, verification and document pages now use the shared `.public-wrap` layout; the 403 page is a styled empty state.
- [x] Updated the default institution branding to `#1E3A8A`/`#3B82F6`, added `accent_text()` so buttons keep white text on the blue accent while pale accents switch to dark ink, and updated the PDF document ink/muted/rule colours to match.
- [x] Updated Chart.js collections and class charts to the new palette and responsive options; `static/js/app.js` now adds table wrappers, the mobile drawer, active navigation and chart defaults.
- [x] Tightened sidebar links to the roles and permission codes each view actually allows, so no role is shown a link that returns 403.
- [x] Fixed `role_dashboard`'s outstanding total, which read non-existent `Student.billed_amt` attributes and crashed once any student had an invoice; added a regression test.
- [x] Verification: 842 tests pass, `ruff check .` is clean and `makemigrations --check` reports no changes. Navigation links were smoke-tested for admin, registrar, bursar, lecturer, student and parent accounts against the demo database.

## Phase 8 completion - REST API and webhooks (session 16)
- [x] New `api` app with `ApiKey`, `WebhookEndpoint` and `WebhookDelivery` (migration `0001`). Keys and endpoints carry an explicit `institution` FK rather than `TenantModel`, because a key must identify the tenant before a user is known and a webhook worker may run with no current institution.
- [x] API key auth: `Authorization: Api-Key esk_<prefix>_<secret>` (a bare `Bearer`/`Token` scheme and `X-API-Key` are also accepted). Only a SHA-256 digest plus a public prefix are stored; the raw key is shown once on issue. Revoking a key, deactivating its user, or a prefix/secret mismatch returns 401.
- [x] The key decides the tenant: `ApiKeyAuthentication` sets `request.institution` and the current institution, so the existing tenant managers and every explicit viewset filter see only that institution's rows.
- [x] `HasApiPermission` bridges DRF to the data-driven permission codes, and a key can optionally carry `scopes` that narrow its account's permissions. `IsInstitutionMember` confirms the key and user belong to the same institution.
- [x] Read-only API v1 under `/api/v1/`: students, staff, academic years, terms, programmes, courses, subjects, classes, enrolments, attendance, invoices, payments and applications, plus `whoami`.
- [x] Row-level scope is explicit per viewset: `students.view_all` sees all; `students.view_own` sees only the caller's own rows; `parent_portal.view_child` sees linked children; parent finance additionally requires `guardian_links.can_view_finance`. A caller with none of the relevant codes gets an empty queryset.
- [x] Pagination (`page_size`, capped at 200), search, ordering and a whitelist-based filter backend supporting `exact`, `in`, `gte/lte/gt/lt`, `contains/icontains` and `isnull`. Filters on columns a view did not expose are ignored, so a caller cannot probe hidden fields.
- [x] Scoped throttling (`api` 120/min, `api-write` 30/min) and OpenAPI 3 schema at `/api/schema/` with Swagger UI at `/api/docs/` and ReDoc at `/api/redoc/`.
- [x] Attendance-device endpoint `POST /api/v1/attendance/device/` accepts `{"records": [...]}` and creates or updates attendance by student number (optionally course/subject), returning written ids and skipped records; it requires `academics.take_attendance`.
- [x] Accounting export `GET /api/v1/finance/journal/export/` streams the ledger as CSV with an optional `start`/`end` date range; it requires `finance.view_ledger` or `finance.view_reports`.
- [x] Webhooks: configurable endpoints per institution with event subscriptions, a generated `whsec_` signing secret and a test-ping action. Events are queued as `WebhookDelivery` rows, so a slow receiver never blocks the save that raised the event.
- [x] HMAC-SHA256 signatures over `<unix timestamp>.<compact sorted JSON>` sent as `X-EduSphere-Signature: t=...,v1=...`, with `X-EduSphere-Event` and `X-EduSphere-Delivery` headers. Delivery uses a 10-second timeout and only http(s) URLs.
- [x] Retries: a failed delivery gets exponential back-off (1 min, 5 min, 15 min, 1 hour, 6 hours) and is abandoned after 6 attempts. `manage.py deliver_webhooks` sends due deliveries; `--id`, `--limit` and `--now` are supported for cron/schedulers.
- [x] Delivery log and controls at `/settings/api/` and `/settings/api/webhooks/<id>/`: issue/revoke keys, add/pause/rotate/delete endpoints, send a test event, and retry a failed delivery. All screens require `admin.manage_settings` and are tenant-scoped.
- [x] Signal receivers queue `student.created`, `invoice.created`, `payment.received` and `application.submitted`. Each receiver converts dates to ISO strings, swallows and logs any queueing error, and is a no-op when the institution has no active endpoint, so webhooks can never break a domain save.
- [x] Added `djangorestframework` and `drf-spectacular` to `requirements.txt`, the `api` app to `INSTALLED_APPS`, API routes to `config/urls.py`, an "API keys" navigation link for admins, and styling for the new screens.
- [x] 50 new tests (`api/tests/test_api.py`, `api/tests/test_webhooks.py`) cover authentication, scopes, tenancy, row scope, filtering/search/ordering/pagination, schema/docs, the attendance device, the journal export, webhook subscription/signing/delivery/retries and the admin screens.
- [x] Bug found and fixed: the first webhook signal assumed `Invoice.due_date` was always a date object, so the existing Phase 0 tests (which pass a string) raised `AttributeError: 'str' object has no attribute 'isoformat'`. Signals now use an `_iso()` helper and wrap queueing in `try/except`.
- [x] Bug found and fixed: the custom filter backend built lookups such as `status__exact__in`, which Django rejected. It now maps a whitelisted field to its lookup directly (`status__in`).
- [~] Integrations are partial, honestly: Paynow already exists (initiate, poll and signed webhook) and EcoCash is a supported payment method; SMTP email already exists; the attendance device and accounting export are new. Still outstanding: a real SMS gateway adapter, a real WhatsApp Business adapter (both are stub providers today), and Google/Microsoft OIDC SSO, which is not built.
- [x] Verification: 890 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Public landing page and self-registration (session 17)
- [x] Replaced the old split-panel sign-in page with a platform-level landing page that never reads institution branding or names: Vanpere wordmark and navigation, sign-in card beside a classroom photo, trust strip, feature and audience cards, how-it-works steps, role blocks, a security band, a contact section and a footer with the current year and privacy link.
- [x] Added a separate `static/css/landing.css` built on the existing deep green `#12403c`, marigold `#e8a317` and Figtree identity, with dark mode, mobile-first breakpoints, reduced-motion handling, visible focus, a skip link and a gradient placeholder that appears when a photo file is absent. The signed-in application keeps its navy tokens in `app.css`.
- [x] Added `static/js/landing.js` for the mobile menu, sticky-header state and a searchable institution picker. The page works without JavaScript; no library or hotlinked image was added.
- [x] Generated WebP and JPEG landing images at phone and desktop widths under `static/img/landing/`; every file is below 200 KB. `scripts/build_landing_images.py` rebuilds them from `static/img/landing/originals/`.
- [x] Added the contact form on the landing page with a honeypot, per-IP rate limit, CSRF protection and an email to `vanhuvaonejeffrey@gmail.com`; the phone number is linked with `tel:` and `https://wa.me/263774573765`, and the footer repeats both contacts.
- [x] Added `accounts/urls.py` for sign-in, sign-out, password reset, registration, email verification, privacy and administrator review, and removed the old inline auth routes from `config/urls.py`.
- [x] Added `/register/` with clear student and staff paths. Both list only institutions whose new `public_registration` setting is on, and the staff position list is limited by that institution's `staff_positions` setting.
- [x] Student path: registration number, surname and date of birth are compared case-insensitively with collapsed spaces against the existing admission record. A match attaches or creates one login, activates it immediately and sends an email verification link when the contact is an email address. A mismatch always shows the required generic message, offers the institution's contact page and writes an audit entry; attempts are throttled per IP and per registration number. A duplicate login is reported with sign-in and password-reset links.
- [x] Staff path: creates an inactive "Pending approval" account and a `StaffRegistrationRequest` limited to the institution's allowed positions. Administrators see a filtered list and a detail screen with approve (including a different position and department), reject with a reason, email the decision and notify administrators in-app. Approval assigns the data-driven role, department and `StaffProfile`; every decision is audited. Administrator and principal roles cannot be requested or approved through this flow.
- [x] Added institution settings for public registration and staff positions, plus a link to the registration request screen. Added migrations `core/0005_institution_public_registration_and_more.py` and `accounts/0006_alter_user_role_emailverification_and_more.py`.
- [x] Added `accounts/tests/test_registration.py` with 21 tests covering the landing page, sign-in, contact form and honeypot, student match/mismatch/duplicate/throttling/audit, the generic message, staff creation/approval/rejection, cross-institution isolation, forbidden roles and the settings screen.
- [x] Bug found and fixed: Django's `send_mail()` does not accept `reply_to`; the contact form now builds an `EmailMessage` with the visitor's address as the reply-to.
- [x] Bug found and fixed: the `static_exists` template filter received an absolute filesystem path from `{% static %}` when `STATIC_URL` is relative, which raised `SuspiciousFileOperation`. It now reduces either URL or path to the path below `static/` before asking the staticfiles finders.
- [x] Because the public pages use the platform identity, the landing page does not extend `base.html`; registration and password-reset pages extend `templates/public_base.html`. Existing signed-in screens are untouched.
- [x] Verification: 911 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Administrator user management and bulk account import (Part 2B, session 18)
- [x] Added the Users area behind `admin.manage_users`: search, type/role/department/status/email-verified filters, pagination, a pending-registration badge, and row actions for view, edit, change role, reset password, resend invitation, deactivate/reactivate and unlock. Registrars may add students through `students.create`; HR officers may add staff only when they hold the users permission.
- [x] One account-creation service (`accounts.user_admin.provision_user`) creates the login, data-driven role and staff profile or student record atomically. `admissions.services.create_student_record` is now shared with offer acceptance, so an admin-added student gets the same student number, login and optional first invoice.
- [x] Two access modes: a single-use invitation link (UUID token, 72 hours, re-send closes the previous link) and a strong one-time temporary password that forces a change at the next sign-in. A person with no email falls back to a temporary password. Neither mode stores the password after the one-time screen.
- [x] Bulk import accepts a batch-wide access mode, downloadable student/staff templates, duplicate email/username/registration checks, unknown-role/programme and bad-date errors, atomic per-row writes, a printable/downloadable result with one-time passwords, and an undo that only deactivates accounts that have never signed in.
- [x] Link or repair accounts without deleting history: attach a login to a student, link parents to children, and flag or merge duplicates while keeping invoices, results and audit rows. Deactivation/reactivation, unlocking, password reset and role changes are audited and never remove an account that has financial, academic or audit history. An administrator cannot remove their own admin role or deactivate the last administrator.
- [x] Safeguards: unique usernames/emails, strong-password validation, no role above the actor's own, cross-institution checks on every service and route, action rate limits, login lockout, and email notices for account creation, role changes and password resets. Accounts created by an administrator are not pending requests and are treated as existing by public self-registration.
- [x] Added `ResendProvider` behind the existing notification interface, selected when `EDUSPHERE_RESEND_API_KEY` is set; otherwise the console/email provider is used.
- [x] Tenant reads stay lazy and request-safe. A request always sets an explicit institution (the default when the page has none), so views remain isolated; code that never sets one is treated as platform code and may read across institutions. The tenant expression still reads the institution when SQL is compiled, so model-form querysets built at import time remain correct.
- [x] Bug found and fixed: a student added without an email got the generic username `user`; the login now derives from the email local part, then the first name.
- [x] Bug found and fixed: service-level registration calls without a request raised `AttributeError` in the rate limiter; the client-IP helper now handles a missing request.
- [x] Bug found and fixed: bulk import undo used `User.all_objects`, which does not exist; it uses the unfiltered `User.objects` manager.
- [x] Verification: 929 tests pass (including the 18-test `accounts/tests/test_user_admin.py` acceptance suite), `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Zimbabwe tax and record-keeping audit (session 19)
- [x] Audited the finished finance module against the ZIMRA VAT and record-keeping rules. Existing and kept: chart of accounts and double-entry ledger, financial-year period locks, fee structures, invoices/payments/waivers/MoneyLog, discounts/bursaries/sponsorships, instalment plans and late penalties, receipts with QR, refunds/reversals, the Paynow gateway, statements/ageing/cash-up/collections and the CSV/Excel/PDF export matrix, expenses/suppliers/budgets, bank reconciliation, and the single `student_balance` service.
- [x] Added per-institution VAT registration: `Institution.vat_status` (exempt/registered), `vat_number`, `bp_number`, `vat_address` and `base_currency`, editable on the institution settings screen. An institution that is not registered never charges VAT.
- [x] Added `FeeItem.tax_category` (exempt/standard/zero) and a dated `TaxRate` table; `tax_rate_in_force()` picks the rate in force and falls back to the standard 15.5% only for a registered institution. `Invoice` stores `tax_category`, `tax_rate` and `tax_amount` with the amount treated as VAT-inclusive; `Expense` stores its own tax fields.
- [x] Added `ExchangeRate` (migration `finance/0015`) and stored `currency`, `exchange_rate` and `base_amount` on invoices and payments, plus currency/rate/base amount on journal lines.
- [x] Added gap-free document numbering: `DocumentSequence` per institution, document type and financial year, for `INV-`, `RC-`, `CN-` and `DN-` numbers, allocated with `select_for_update` plus an atomic `F()` update and bounded retries for transient SQLite lock contention.
- [x] Made issued financial documents immutable through `ImmutableFinancialDocument`: changing a protected field or calling `delete()` raises `ValidationError`; the admin refuses change/delete but still allows adding, and `MoneyLog` is append-only. Corrections are credit notes, new debit notes (`DebitNote`) or reversing entries.
- [x] Added the switchable `fiscalisation` app (migration `fiscalisation/0001`) with `FiscalDevice`, `FiscalDay` and `FiscalSubmission`. A `finance/signals.py` signal keeps the apps decoupled; the receiver submits invoices, receipts, credit and debit notes, stores the device, fiscal day, receipt counter, verification code, QR data and response, opens/closes fiscal days, and retries failures (Celery-optional; mock gateway by default).
- [x] Printed the ZIMRA verification code and QR image on receipts, credit notes and debit notes, and added a fiscalisation dashboard plus a printable fiscal day report.
- [x] Added six-year retention and backup: `backup_finance` writes a Fernet-encrypted JSON backup (key from `EDUSPHERE_FINANCE_BACKUP_KEY`), `prune_finance` only removes records older than six years and only with `--confirm`, and delete guards stop hard deletion in the app.
- [x] Added the VAT output-vs-input summary (`services.vat_summary`, `/finance/reports/vat/` with CSV/Excel/PDF export) and tax-rate/exchange-rate management screens, and registered the new models in the admin.
- [x] Added 35 tests: `finance/tests/test_tax_and_numbering.py` (gap-free numbering including 10 concurrent allocations, immutability, dated VAT rate in force, default rate, exempt/zero categories, fee-item category, VAT summary) and `fiscalisation/tests/test_fiscalisation.py` (submission of all four document types, verification code/QR, fiscal day open/close, gateway failure recorded, retry, and skip for non-registered/module-off/global-off).
- [x] Fixed the allocator to retry transient SQLite "database is locked" errors, which the concurrency test exposed; without it simultaneous requests could fail rather than wait their turn.
- [~] Still needs ZIMRA: the live FDMS endpoint URL, device ID, device serial and API key (set `EDUSPHERE_ZIMRA_FDMS_URL`, `EDUSPHERE_ZIMRA_DEVICE_ID`, `EDUSPHERE_ZIMRA_DEVICE_SERIAL`, `EDUSPHERE_ZIMRA_API_KEY`). `ZimraFdmsGateway` follows the documented receipt API but the field names and reply shape must be confirmed against ZIMRA's test documentation and a test device before production. No live submission has been made.
- [x] Verification: 964 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Brown public theme (Part 3B, session 20)
- [x] Replaced the deep green/marigold public identity with one all-brown palette in a single shared stylesheet, `static/css/public.css`: `--brown-950 #2b1a12`, `-900 #3e2723`, `-800 #4e342e`, `-700 #5d4037`, `-600 #6f4a36`, `-500 #8d6e63`, `-400 #a1887f`, `-300 #c4a998`, `-200 #dcc8b6`, `-100 #efe2d3`, `-50 #faf4ec`. Semantic tokens (`--bg`, `--surface`, `--field`, `--ink`, `--muted`, `--brand`, `--accent`, `--line`) map onto those values, so nothing is hard-coded outside them and every gradient runs between two browns. Surfaces use brown tones rather than white.
- [x] The signed-out surface uses the brown theme everywhere: the platform landing/sign-in page, registration (choice, student, staff, sent, contact), password reset (form, done, confirm, complete), email verification, invitation acceptance, forced password change, the learn index (`/learn/`) and the public institution page (`/i/<slug>/`). The signed-in application keeps its own `static/css/app.css`.
- [x] Moved the dashboard to `/dashboard/` and put the platform landing at `/` (`EduSphereLandingView`), so a signed-out visitor sees the platform page at the root and a signed-in visitor is redirected to the dashboard. `LOGIN_REDIRECT_URL` still points at the dashboard; no `accounts`/`core` tests regressed.
- [x] All-brown dark variant (espresso `#1e120c`/`#2b1a12`, cream text, caramel accents) driven by `prefers-color-scheme` plus a per-user toggle remembered in `localStorage` (`edusphere.public.theme`) and applied with a no-flash inline script; `static/js/landing.js` gained `setUpTheme()` with a system-change listener and aria labels. Added `--on-brand`/`--on-brand-strong` (cream in light, dark in dark) and `--band-strong`/`--band`/`--on-band` so buttons, the skip link, step numbers, initials and the security strip stay readable in both themes.
- [x] Accessibility: main text/button pairs reach WCAG AA (>=4.5:1, checked for both themes), a thick dark-brown focus outline sits on a cream offset, and error/success messages use a dark-brown panel with a clear icon (`!` / `✓`) and wording rather than colour alone.
- [x] Institution branding: `Institution.tagline` and `Institution.about` were added (migration `0008`) and are editable on the institution settings screen; the public institution page shows the logo, name, tagline and About while the page colours stay brown. A logo sits on a soft cream backing (`.brand-badge`) with initials as a fallback for missing or very light/dark logos. The institution's primary/accent colours stay app/PDF-only and never reach public pages.
- [x] Photos get a CSS-only warm brown duotone (`.media img` sepia/contrast plus a `.media::after` multiply overlay); faces stay clear and the meaningful alt text is kept. Print styles force cream-on-brown.
- [x] Emails from email verification, invitation and password reset use the same brown palette with inline CSS (`templates/emails/base_email.html`, `verify_email.html`, `invitation.html`, `password_reset.html`) and keep a plain-text alternative. Added `OutboundMessage.html_body` (migration `0009`), taught the Resend/SMTP providers to send `html`/`multipart`, added `render_email_html()` and `html_body=` to `queue_message`/`send_message`, and wired the templates into `accounts/registration.py`, `accounts/user_admin.py` and the password-reset view (`html_email_template_name`).
- [x] Added `core/tests/test_public_theme.py` (46 tests): a palette scan of `public.css` and the public/email templates (any colour outside the defined brown variables fails), a contrast check of the main text/button pairs in the light, dark and system-driven themes, and a render test that loads every public page with the shared stylesheet and asserts no 500s. Also covers the theme toggle, the brown emails, the landing redirect and the institution branding.
- [x] Bug found and fixed: the palette scan compared hex values with their `#` while `ALLOWED_HEX` had been stripped; the comparison now strips both sides. The dark-mode button/skip-link/step/initials text was cream on a caramel accent (unreadable), which the new `--on-brand` tokens fix.
- [x] Deleted the one-off `scripts/_build_public_css.py` that produced the stylesheet. The earlier `static/css/landing.css` is superseded and unreferenced; it is left in place rather than deleted, pending a decision.
- [x] Verification: 1010 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Institution logo and branding (Part 1, session 21)
- [x] Both the setup wizard (details step) and the institution settings screen now collect a logo, the primary and accent colours, an "About us" paragraph, a tagline, address, phone, email and website. The existing WCAG contrast check still refuses a colour pair that cannot carry readable text, and the settings screen keeps the "details" action separate from the new "branding" action.
- [x] Added `core/logo.py` as the one place that touches logo files. `open_logo` validates the upload (real Pillow type, PNG/JPG/WebP only, <=2 MB, >=200x200 px, SVG and renamed scripts refused), re-encodes it to RGBA so no EXIF or embedded payload survives, and `save_logo` writes WebP at 64/160/512 px plus a 512 px PNG fallback under a random name in `institutions/<slug>/logos/`. `remove_logo` deletes every stored file and clears the field. `institution_logo_url`, `institution_logo_png_url`, `absolute_logo_url` and `logo_flowable` (a scaled reportlab flowable) cover the web, email and PDF cases.
- [x] Added `core/templatetags/branding.py` (`institution_logo`, `institution_logo_png`, `institution_initials`, `institution_brand_colour`, `institution_brand_text`) and used it in the sidebar (`templates/base.html`), the institution login and registration pages, `templates/emails/base_email.html`, the finance receipt/credit-note/debit-note/statement templates, and the report-card/transcript/ID-card/certificate/offer/statement PDF builders (`academics/documents.py`, `admissions/documents.py`, `finance/statements.py`). Without a logo every surface falls back to the institution's initials in a coloured circle.
- [x] The canonical path lives on `Institution.logo` and the per-size paths in `Institution.settings["logo"]`, so a page can pick the right file. Added `Institution.website` and capped `Institution.about` at 600 characters (migration `0010_institution_website_alter_institution_about`).
- [x] Permission and tenancy: added `admin.manage_branding` to `core/permissions.py`. Because `institution_admin` is defined as `ALL` minus `PLATFORM_ONLY`, it gains the code automatically; `core/migrations/0011_grant_manage_branding` grants it to institution-admin roles already in the database. The branding action checks the code, only ever writes the signed-in user's own institution, and writes `institution.branding_updated` / `institution.logo_removed` audit entries.
- [x] The wizard holds the chosen logo across steps as a re-encoded 512 px PNG in session state (`core/wizard.py` `set_logo`/`clear_logo`, `_store_wizard_logo` in `core/wizard_views.py`) and writes it when the institution is created. The details step shows a live preview and a "Remove logo" control. The duplicated `_logo()` helper in the PDF builders was replaced by the shared `logo_flowable`.
- [x] Added `core/tests/test_logo.py` (50 tests): validation (PNG/JPG/WebP accepted, BMP/GIF/SVG/text/empty/oversize/tiny refused), metadata stripping, the stored sizes and PNG fallback, random names, the canonical field, replace/delete, the URL helpers, `logo_flowable`, the template tags, the model fields and `initials()`, the settings screen (save/remove/bad-file message/permission gate/tenant isolation/own-folder) and the wizard logo flow plus the sidebar render. A `MediaTestCase` base overrides `MEDIA_ROOT` and resets `default_storage._wrapped` by hand, because Django does not rebuild the storage for a plain `MEDIA_ROOT` change.
- [x] Verification: 1060 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Shareable public sign-up links (Part 2, session 22)
- [x] Added `SignupLink` and `SignupLinkUse` to `accounts/models.py` (migration `accounts/0008_signuplink_signuplinkuse_and_more`). A link belongs to one institution, has a UUID token, an optional label, a kind (`student`, `staff` or `any`), an optional preset staff position, an optional expiry, an optional maximum number of uses and an active flag; `is_open`, `is_expired`, `is_exhausted`, `use_count`, `position_label`, `path()` and `url()` keep the logic in one place. Each registration that comes through a link writes one `SignupLinkUse`, so the use limit is exact and the administrator can see who arrived through the link.
- [x] Added `accounts/signup_links.py` as the service: `create_signup_link` (validates the kind, refuses forbidden or unknown positions and a student link with a position, validates the use limit, and audits `signup_link.created`), `revoke_signup_link`, `reactivate_signup_link`, `delete_signup_link` (audits before deleting), `link_is_open`, `link_positions` (preset position, else the institution's public list, else every non-forbidden position), `link_offers`, `record_use` and the per-link/per-IP rate-limit helpers (`edusphere.signup.<pk>.<ip>`, 10 attempts per 15 minutes).
- [x] The public page `accounts/views.signup_link` (`/join/<uuid>/`) reuses the existing registration services. It refuses an unknown token with 404, shows a closed page (revoked/expired/used-up/inactive institution), returns 429 when the address is over the attempt limit, shows a chooser for an `any` link, and otherwise renders the student or staff form locked to the link's institution. A student activation records the use and shows "Your account is ready"; a staff request creates the inactive login and the pending row and records the use. The link works even when `public_registration` is off, because the link itself is the invitation.
- [x] `register_staff` gained an `allowed_positions=None` keyword that defaults to the institution's public list, so the ordinary public flow is unchanged. `StudentRegistrationForm` gained `institution=` (locks the hidden institution field) and `StaffRegistrationForm` gained `institution=`/`positions=` (locks the institution and restricts the offered positions and the department list). The `{% csrf_token %}` and honeypot `website` field are kept on the link forms.
- [x] Added `templates/registration/signup_link.html` (public brown theme, institution logo/tagline/contact details, chooser, student form, staff form with the preset position shown as text) and `templates/accounts/signup_links.html` (the administrator screen: create form, links table with a copy button, revoke/reactivate/delete). `accounts/user_views.signup_link_list` and `signup_link_action` back the screen, both gated on `admin.manage_users` and scoped to the signed-in user's institution. Linked from `templates/core/institution_settings.html` and `templates/accounts/user_list.html`.
- [x] Added `accounts/tests/test_signup_links.py` (37 tests). The two assertions that compared the full generic messages had to match on the part before the apostrophe, because the template escapes `'` to `&#x27;`; the fix uses `STUDENT_GENERIC_ERROR.split(",")[0]` and `STAFF_GENERIC_PENDING.split(".")[0]`.
- [x] Verification: 1097 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Institution-specific login page (Part 3, session 23)
- [x] Added `InstitutionLoginView` (`accounts/views.py`) at `/i/<slug>/login/` (name `institution_login`, `accounts/urls.py`) with `templates/registration/institution_login.html`. The page shows the institution's logo (or initials in a badge), name, tagline and contact details, and a sign-in card on the public brown theme; the institution's primary/accent colours are deliberately not applied, matching the public institution page.
- [x] Added `InstitutionAuthenticationForm` (`accounts/forms.py`). It subclasses `AuthenticationForm` and, in `confirm_login_allowed`, refuses a valid login whose account belongs to a different institution using the same `invalid_login` message as a wrong password, so the page cannot be used to probe for accounts at other schools. A platform superuser is allowed through. Unknown or inactive institutions are a 404; a signed-in visitor is redirected to the dashboard; the `next` parameter is honoured; and sign-in works whether or not `public_registration` is on.
- [x] Extracted the failed-password counting out of `EduSphereLoginView` into a `LoginLockoutMixin` and used it for both sign-in views, so the lockout and the "temporarily locked" message behave the same on the platform page and the institution page.
- [x] Made the link shareable: `templates/public/institution.html` now links to the institution sign-in page, and `core/views.settings_view` passes `institution_login_url` (an absolute URL) to `templates/core/institution_settings.html`, which shows it with a copy button.
- [x] Added `accounts/tests/test_institution_login.py` (16 tests) covering the branded page and the initials fallback, the colours staying off the public page, the public-page link, 404s for unknown and inactive institutions, a successful sign-in, the refusal for another institution, superuser access, a wrong password, login with public registration off, `next`, the signed-in redirect, a counted failed attempt, an inactive account and the settings link. Added the new page to the public-page render sweep in `core/tests/test_public_theme.py`.
- [x] Verification: 1113 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Printable/PDF receipt and emailed receipt (Part 4, session 24)
- [x] Added `finance/receipts.py` with `build_receipt_pdf(payment, request=None, fiscal=None)`. It renders a real PDF with reportlab: the institution's logo (`logo_flowable`), name, tagline, address and contacts, the primary colour for the headings, and a details table (receipt number, student, what the payment was for, amount, method and reference, date, received by, balance on the invoice, and the ZIMRA verification code when fiscalisation is on).
- [x] The PDF carries a QR code (`qrcode`, the same approach as the admissions documents) that opens the public `verify_receipt` page; `receipts.verify_url` builds the path and makes it absolute when a request is given. The verify page shows no more than the receipt already does.
- [x] Added the `receipt_pdf` view at `receipt/<int:pk>/pdf/` (name `receipt_pdf`) and the `email_receipt` POST view at `receipt/<int:pk>/email/` (name `email_receipt`, `finance/urls.py`). Both share a `_receipt_payment` helper with the on-screen `receipt` view, so a student can only open or email their own receipts and the other finance roles can use any.
- [x] `email_receipt` reuses the notification pipeline (`queue_message` + `send_message`) and a new brown HTML template `templates/emails/receipt.html` that carries the institution's logo and the public verify link. A student with no email address on file gets an error message instead of a silent failure, and a GET never sends anything.
- [x] `templates/finance/receipt.html` now offers "Download PDF" and "Email receipt" beside "Print receipt". No model change or migration was needed: the receipt number, verification token and branding already existed.
- [x] Added `finance/tests/test_receipt_documents.py` (15 tests): the PDF download and headers, the request/fiscal passed to the builder, the student ownership rule, the sign-in requirement, the on-screen buttons, the email send (channel, recipient, subject, status and HTML), the verify link, a GET not sending, a missing address, a student emailing only their own receipt, the verify URL helper and the PDF building with and without a stored logo.
- [x] Verification: 1128 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Configurable student and staff numbers (Part 5, session 25)
- [x] Added `core/numbering.py`: `get_format`, `format_number`, `peek_next`, `next_number`, `save_format` and `formats_for`. A format is a prefix, whether the year is included, the sequence padding and the next number to issue, stored in `institution.settings["numbering"]`.
- [x] Added `core.models.NumberSequence` (one row per institution and kind) and the migration `core/0012_numbersequence`. `next_number` advances it with an atomic `F()` update inside a row lock and retries on a rare SQLite double insert or "database is locked", so simultaneous admissions never share a number. This mirrors the proven `finance.services.next_document_number`.
- [x] `academics.models.Student.save` now calls `next_number(self.institution, "student")` instead of the hard-coded `f"{created.year}{pk:05d}"`, and `core.models.StaffProfile.save` fills a blank `employee_no` with `next_number(self.institution, "staff")`. Both only generate on creation and only when no number was supplied, so an explicit or imported number is always kept.
- [x] Added a "Number formats" section to `templates/core/institution_settings.html` (a form per kind) and the `numbering` action in `core/views.settings_view`, guarded by the existing `admin.manage_settings` permission and written to the audit log as `institution.numbering_updated`. The screen shows the next number each kind will receive.
- [x] Added `core/tests/test_numbering.py` (24 tests): the defaults and formatting, saving a format and seeding the counter, the prefix/padding/next validation, sequential and year-less allocation, the separate sequences, the preview, student and staff generation, an explicit number kept, the format change taking effect, per-institution isolation, and the settings screen (section shown, save, audit, a bad value, the permission gate).
- [x] Verification: 1152 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Institution details on every document (Part 4B, session 26)
- [x] Added `core/document_header.py`: `institution_details`, `contact_lines`, `initials_flowable`, `build_header`, `header_mark`, `header_context` and `snapshot_header`. It renders the logo (512 px for PDF, 160 px for screen/email), name, address, phone, email, website and VAT/BP numbers when set, and falls back to an initials circle when there is no logo; a missing or corrupt stored file is caught so a document still renders. Text stays ink-on-page for black-and-white printing.
- [x] Added `templates/core/_document_header.html`, the `{% document_masthead %}` inclusion tag in `core/templatetags/branding.py`, and `.doc-header`/`.doc-logo`/`.doc-initials` styles (including print) in `static/css/app.css`. Used on the receipt, credit-note, debit-note, statement and new invoice screens.
- [x] Replaced the per-builder logo/header code in `finance/receipts.py`, `finance/statements.py`, `academics/documents.py` and `admissions/documents.py` with `build_header`; identity cards use `header_mark` and now repeat the address and tax line on the back. Report cards, transcripts, timetables, offer letters and certificates all share the same masthead.
- [x] Added `templates/finance/invoice.html`, `invoice_detail`/`invoice_pdf` in `finance/views.py`, routes in `finance/urls.py` and `finance/invoices.py` for a branded invoice with VAT, balance and payments; linked from the student record.
- [x] Fiscalised documents keep their issued layout: `FiscalSubmission.layout` (migration `fiscalisation/0002`) stores the details and a copied PNG logo under `fiscalisation/headers/...` at submission time. The receipt/invoice/credit/debit screens and PDFs pass that snapshot to the masthead, so a later logo or address change does not rewrite an issued document; non-fiscalised documents use the live details.
- [x] Added `templates/emails/fee_reminder.html` and the header context to the receipt and fee-reminder emails. `finance/services.send_overdue_reminders` now renders the branded HTML body.
- [x] Added `core/tests/test_document_header.py`: details/contact/tax lines, the initials fallback, a missing logo file, a real logo and its snapshot copy, the three-column header, a stored layout beating the live institution, the receipt screen using the fiscal snapshot, and the receipt email contents.

## Fully configurable numbering (Part 5B, session 26)
- [x] Added `core.NumberFormat` (one row per institution and kind) and `core.NumberFormatChange` (append-only history), plus `year` on `NumberSequence` and a per-institution unique staff employee number in `core/models.py` (migration `core/0013`, which stamps existing counters into the current year so an upgrade mid-year cannot reissue a number).
- [x] Rewrote `core/numbering.py` around tokens: prefix, optional year (2/4 digits), optional programme/department code, 1-8 digit padded sequence, no/hyphen/slash separator, and no/fixed/calculated check letter. The calculated letter is the documented weighted mod-26 of A=1..Z=26 with 0 mapping to A. Formats reset yearly or continue forever, and the start number is configurable.
- [x] `next_number` locks the sequence row, advances it with `F()`, retries a rare SQLite double insert or "database is locked", skips a candidate already in use, and never puts a number back after a deletion. Student, staff and application creation allocate inside their own transaction. Imported or typed numbers are uppercased and kept.
- [x] Added `validate_manual_number`, `manual_override`, `number_key`, `matching_pks` and `preview_numbers`. Overrides and manual numbers are uniqueness-checked and written to the audit trail; `save_format` records the before/after format and actor in `NumberFormatChange` and writes `institution.numbering_updated`.
- [x] Added the dedicated `/settings/numbering/` page (`core/views.numbering_settings`, `templates/core/numbering_settings.html`, nav link) with every option, a live three-number preview using the same check-letter arithmetic, validation, a confirm-before-save warning, the manual-override form and the change history. The original "Number formats" section on the institution page remains and links to the new page.
- [x] Wired the numbers through `Student.save`, `StaffProfile.save`, `Application.save`, `admissions.services.create_student_record`, `accounts.user_admin.create_staff_record`, registration approval and the bulk importer. Numbers are matched ignoring case, spaces and separators in global search, the student search API, staff list/detail, admissions lists, the user list, registration matching and the attendance-device API; they are already printed on receipts, statements, invoices and ID cards and included in exports.
- [x] Added tests in `core/tests/test_numbering.py` (token/check-letter/year/start/preview/history, deletion/no-reuse, overrides, applicants, the settings page), `core/tests/test_import.py` (generated numbers and separator-insensitive duplicates), `accounts/tests/test_registration.py` (separator-tolerant matching) and `academics/tests/test_dashboards_search.py` (separator-tolerant global and student search).
- [x] Verification: 1188 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Green public platform, learn pages and institution profiles (Part 3C, session 27)
- [x] Replaced the brown public palette with the deep-green and marigold identity (`--green #12403c`, `--green-deep #0b2b28`, `--marigold #e8a317`, `--marigold-deep #a86f00`, and soft/ink/line tokens) in `static/css/public.css`, keeping Figtree, the mobile-first layout, the reduced-motion and focus styles, and the green/marigold dark variant driven by `prefers-color-scheme` plus the remembered `edusphere.public.theme` toggle. Added `--link` and `--accent-ink` so anchors, eyebrows, icons and choice links stay readable without fighting `.btn`. The signed-in app keeps `static/css/app.css`.
- [x] `/` is the platform login-and-marketing page: hero, sign-in card, responsive classroom photo (WebP with JPEG fallback, `loading="lazy"` below the fold), trust strip, feature cards, audience cards, how-it-works steps, role blocks, security band, contact section (phone `+263 774 573 765`, email `vanhuvaonejeffrey@gmail.com`) and the footer. It carries no institution-specific content and a signed-in visitor is redirected to the dashboard.
- [x] Added `core/learn.py` with the content for seven learn pages and their clean top-level routes (`/features/`, `/for-schools/`, `/for-colleges/`, `/for-parents-and-students/`, `/security-and-privacy/`, `/pricing-and-contact/`, `/faq/`), plus a `/learn/` hub. `core/public_views.learn_page` renders `templates/public/learn_page.html`; `learn_alias` permanently redirects the old `/learn/<slug>/` addresses. The header uses `<details class="nav-more">` with every page, so the menu works without JavaScript. The words are plain English and contain no invented statistics, client names, awards, testimonials or prices.
- [x] `/pricing-and-contact/` uses `DemoRequestForm` (`accounts/forms.py`) with a hidden honeypot, optional message and the shared contact rate limit. A valid request emails `contact_recipient()` with the visitor's address as `Reply-To`, then shows a thank-you message; a bot, a bad address or too many attempts sends nothing.
- [x] Added `Institution.public_page_enabled` and `Institution.show_register_button` (migration `core/0014`) with controls on `templates/core/institution_settings.html` and the `registration` action in `core/views.settings_view`. `accounts.signup_links.open_signup_links(institution)` was added and exported so the public page and the institution login share one rule.
- [x] Rewrote `templates/public/institution.html` as the branded `/i/<slug>/` page: logo (or initials), name, tagline, About, contact details, a sign-in card, a conditional Register button and the "Powered by Vanpere" footer linking to `/`. Validated primary/accent colours are passed through `core.branding` and their text colours are computed for contrast; the page can be switched off (404) and only renders Register when `public_registration` is on or an open sign-up link exists.
- [x] Institution sign-in stays scoped and unbranded: the page posts to `institution_login`, `InstitutionAuthenticationForm` refuses another institution's account with the standard "Wrong username or password" message, and the institution's colours are applied only on `/i/<slug>/`, never on `/i/<slug>/login/`.
- [x] SEO: `templates/public_base.html` and `templates/registration/login.html` carry titles, meta descriptions, Open Graph/Twitter tags, canonical URLs and robots directives. Added `core/sitemaps.py` (`PlatformSitemap`, `LearnSitemap`, `InstitutionSitemap`), `django.contrib.sitemaps` and `/sitemap.xml`, and `public_views.robots_txt` with `ROBOTS_DISALLOW` for the private app plus `ROBOTS_ALLOW` for the public check pages. Institution and private login pages are `noindex,follow`; public pages never show student or staff records.
- [x] Emails moved from brown to the green/marigold palette (`templates/emails/base_email.html`, verification, invitation, password reset, receipt and fee reminder) while keeping the plain-text alternative and the branded document masthead.
- [x] Rewrote `core/tests/test_public_theme.py` for the green palette (including the print fallback), AA contrast in light/dark/system themes, the render sweep, institution branding and green emails. Added `core/tests/test_public_pages.py` for the learn pages, metadata, old-URL redirect, demo form and rate limit, public page/Register toggles, open sign-up links, sitemap and robots. Updated the institution-login tests for the public-page Register rules.
- [x] Fixed three test-level issues found at the end of the previous session: the palette scanner now allows the `000` print fallback, apostrophes in learn copy are compared through `django.utils.html.escape`, and the shared header/footer Register link uses `register_url` so a sign-up-link-only institution never falls back to `/register/`. Also removed a ruff `F811` in `core/sitemaps.py` by replacing the duplicate `priority` attribute with the per-item method.
- [x] Verification: 1214 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## All-brown public and access pages (Part 3B, session 28)
- [x] Replaced the green/marigold public theme with one all-brown palette in `static/css/public.css`: `--brown-950 #2b1a12` to `--brown-50 #faf4ec`, plus `--espresso #1e120c` and `--espresso-soft #2b1a12` for dark mode. Light mode uses cream backgrounds, brown text and controls; the dark variant is all espresso and caramel. Every colour resolves to a brown variable, gradients run only between browns, and shadows are brown-tinted. The signed-in application keeps `static/css/app.css`.
- [x] Accessibility: text pairs reach 4.5:1 and form/control borders at least 3:1 in both themes (`--line-strong` is `--brown-500` in light and `--brown-400` in dark, now checked against `--field`). Focus is a 4px dark-brown outline with a 3px cream offset, flipping to cream in dark mode. Errors and successes use brown panels with an icon (`!`, `✓`, `i`) and wording, so meaning never depends on colour alone. Figtree, large headings, rounded cards, the minimal motion and `prefers-reduced-motion` are unchanged.
- [x] Dark mode is driven by `prefers-color-scheme` and the remembered `edusphere.public.theme` toggle; the `:root[data-theme="dark"]` and system blocks define the same tokens, which a test now enforces.
- [x] Institution branding stays but its colours do not: `core/public_views.institution_page` no longer supplies `primary_color`, `accent_color` or the computed text colours, and `templates/public/institution.html` no longer injects `--inst-*` values. Logos, names, taglines and About text remain. `.brand-badge` and `.contact-logo` now have a soft cream backing, brown border, padding and shadow, tested with a near-white and a near-black logo. Institution colours remain available to receipts, documents and the signed-in app.
- [x] Photos get a CSS-only warm duotone (`.media img` sepia/saturation filter plus a brown `mix-blend-mode: multiply` overlay), with the existing meaningful alt text kept.
- [x] Renamed `.btn-marigold` to `.btn-deep` and updated every public template. Emails already moved to brown inline CSS in this change set (`base_email`, verification, invitation, password reset, receipt and fee reminder), each keeping a plain-text version.
- [x] Rewrote `core/tests/test_public_theme.py` for the brown palette: exact palette values, a scan of `public.css` and all public templates for hex/rgb/named colours outside the palette, rejection of green/marigold leftovers, AA text pairs and 3:1 borders in light/dark/system themes, dark toggle/system equality, button and focus checks, alert icons, render coverage for `/`, every learn page, `/i/<slug>/`, institution sign-in, `/register/`, `/join/<token>/`, reset, verification, invitation, force-password-change and confirmation pages, institution colours being absent, cream logo backing with light/dark logos, photo treatment and alt text, reduced motion, and brown HTML plus plain-text verification/invitation/reset emails.
- [x] Verification: 1228 tests pass, `ruff check .` is clean, `manage.py check` is clean and `makemigrations --check` reports no changes.

## Next up
- Phase 7B/7C are complete, Phase 2 is complete apart from optional hardening, and Phase 8 is complete except for the honest integration gaps above (real SMS/WhatsApp adapters and OIDC SSO). The public platform now uses the all-brown theme (Part 3B), with the learn pages, institution profiles and SEO in place. The next phase is Phase 9 (security and compliance).

## Open questions
- None at start.
- Bulk import format is settled: CSV and `.xlsx` are both accepted, `openpyxl` is a dependency.
- Phase 2 will need a decision on whether the existing `Programme`/`Course` tables stay as they are or are renamed to the Phase 2 names (`ProgrammeYear`, `CourseOffering`).

## Platform administrator provisioning and Render deployment (session 29, 2026-11-16)
- [x] Added `platform_admin/provisioning.py`: one idempotent service that reads `PLATFORM_ADMIN_USERNAME`, `PLATFORM_ADMIN_EMAIL`, `PLATFORM_ADMIN_PASSWORD`, `PLATFORM_ADMIN_RESET_PASSWORD` and `PLATFORM_ADMIN_FORCE_CHANGE`, validates the username/email/password (Django validators plus `accounts.validators.validate_strong_password`), creates or repairs the account, writes the first-creation audit entry and never handles the password in plain text beyond `set_password`. Missing or partial variables and invalid values return a plain-English result and change nothing; a conflicting username/email pair is refused.
- [x] Added `platform_admin/management/commands/ensure_platform_admin.py` (with `--allow-failure`) and `platform_admin/startup.py`. `build.sh` runs the command after `migrate`; `PlatformAdminConfig.ready()` calls `ensure_once()`, which runs at most once per process and is skipped for tests, `migrate`, `makemigrations`, `collectstatic`, `check`, shell commands and every other non-server management command. The startup path logs a not-ready database and lets the app boot.
- [x] `accounts.User` gained `is_platform_admin` and `password_set_from_env`; `institution` is now nullable and the `__init__`/`save` auto-assignment is skipped for platform administrators. Migration `accounts/0009`. A platform administrator is created with `is_staff`, `is_superuser`, `is_active`, role `admin`, no institution, a verified `EmailVerification` and a `PlatformStaff` row with the `main_admin` role. `ensure_platform_staff` now also corrects an existing staff row's role and reactivates a main administrator.
- [x] First-login safety: the existing `ForcePasswordChangeMiddleware` now carries the administrator to the strong-password form; `ForcePasswordChangeView` clears `must_change_password` and `password_set_from_env`; the platform dashboard shows a warning until the environment password is replaced; the platform security page links to the password form. Password resets and invitations also clear `password_set_from_env`. The school dashboard redirects a platform administrator to the platform dashboard.
- [x] Made `config/settings.py` environment-driven: generic `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, `EMAIL_*`, `RESEND_*`, `DEFAULT_FROM_EMAIL` and storage names, with the old `EDUSPHERE_*` names kept as fallbacks. `RENDER`/`RENDER_EXTERNAL_HOSTNAME` make production safe by default; `DEBUG=false` turns on secure cookies, the proxy SSL header, SSL redirect, HSTS, referrer policy, nosniff and frame denial. Local runs with no variables still use SQLite, debug and console email. Added `AUTH_PASSWORD_VALIDATORS` (minimum 10) and fixed all `check --deploy` warnings by adding `XFrameOptionsMiddleware` and serializer type hints.
- [x] Static and uploads: WhiteNoise compression/manifest storage is the `staticfiles` backend; local filesystem is the development default and `django-storages` S3 storage is selected by `STORAGE_BACKEND` (Supabase Storage, R2, B2). `collectstatic` copies 193 files and post-processes 509.
- [x] Deployment files: pinned `requirements.txt` (including gunicorn, whitenoise, psycopg[binary], dj-database-url, django-storages, boto3, tzdata); executable `build.sh`; `render.yaml` (web service, `./build.sh`, gunicorn, `/health/`, Python 3.12.10, generated `SECRET_KEY`, `DEBUG=false`, all secrets `sync: false`); `.gitignore`; `.env.example`; `config/health.py` and the `/health/` route; `DEPLOY.md`.
- [x] Tests: `platform_admin/tests/test_ensure_platform_admin.py` (8 tests: creation and flags, double run, password left alone, reset flag, missing variables, weak password rejected, password never in logs/output, force-change off) and `config/tests/test_health.py` (2 tests: 200 with the database, 503 without it). No credential is stored in the tests: each run generates a fresh username, email and strong password.
- [x] Verification: clean virtual-environment install of the pinned requirements succeeded; `manage.py check` and `check --deploy` with production settings are clean; `collectstatic` succeeds; `ruff check .` is clean; `makemigrations --check` reports no changes. Full test suite result is recorded below.
- [x] Final verification: 1385 tests pass (0 failures, 218 upstream deprecation warnings), `ruff check .` is clean, `makemigrations --check` reports no changes, `manage.py check --deploy` with `DEBUG=false` reports zero issues, and `collectstatic` succeeds. The test suite and local `runserver` use the plain static storage (no build artefact required), while a `DEBUG=false` deployment uses `CompressedManifestStaticFilesStorage`; a production shell resolves `css/public.css` to `/static/css/public.9bf347df68e0.css`. The health service module hub moved from `/health/` to `/health/hub/` so the deploy health check can own `/health/`; its screens (`safe-alerts/`, registers and JSON) are unchanged, and every internal link uses `reverse()`.

## Reliable email on the Render free plan (session 30, 2026-11-16)
- [x] One backend, no SMTP: `config/settings.py` uses `anymail.backends.resend.EmailBackend` when `RESEND_API_KEY` is set and Django's console backend otherwise; the test runner still forces the in-memory backend so `mail.outbox` keeps working. `django-anymail[resend]==15.2` is pinned (it pulls in `svix` and `requests`). The old `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` and `EMAIL_USE_SSL` settings were removed, and `RESEND_API_URL` is the base `https://api.resend.com/` that Anymail expects.
- [x] Decision: keep the existing `OutboundMessage` + `queue_message` + `send_message` architecture and extend it rather than invent a second mail path. `core/notifications.py` now has one `DjangoEmailProvider` that sends through `django.core.mail` (so Anymail, console and locmem all share the code path); the bespoke `ResendProvider`/`SmtpProvider` classes were deleted.
- [x] Decision: any queued email without an HTML part gets one. `core/notifications_html.py` + `templates/emails/generic.html` render a brown fallback from the subject and body, and the rendered HTML is saved back to `OutboundMessage.html_body`, so every message has both text and HTML.
- [x] Sender and reply: the From display name is the institution's name while the address always comes from `DEFAULT_FROM_EMAIL` (so a tenant can never send from an unverified domain); `Reply-To` is the institution's `contact_email` when set. Platform contact/demo form replies keep the sender's address as `Reply-To`.
- [x] Reliability: `EMAIL_TIMEOUT=10`, `EMAIL_MAX_RETRIES=5`, exponential `EMAIL_RETRY_BACKOFF_SECONDS=60`. A failure stays `QUEUED` with `next_attempt_at` until the last attempt, then `FAILED`; `send_pending()` picks up due queued rows. `EMAIL_ASYNC` (default false) only dispatches to Celery when a broker is actually configured and a worker exists, otherwise sends inline. Failures never raise into a request; the user sees "We could not send the email. Try again in a few minutes".
- [x] Decision: `OutboundMessage` gained `provider_message_id` and `next_attempt_at`; new `EmailSuppression` (platform-wide bad address) and `EmailEvent` (delivery event) models. Migration `core/0016_emailsuppression_outboundmessage_next_attempt_at_and_more`.
- [x] Abuse limits: `email_rate_limited`/`record_email_attempt` limit verification and reset per address and per IP; `bulk_email_limit_reached`/`record_bulk_email` cap bulk sends per institution. Wired into `send_email_verification`, the reset view, `operations.services.send_newsletter` (raises a clear `ValidationError`) and the fee-reminder run (skips). Limits are settings: `EMAIL_VERIFY_LIMIT`, `EMAIL_RESET_LIMIT`, `EMAIL_IP_LIMIT`, `EMAIL_BULK_LIMIT`, `EMAIL_RATE_WINDOW_SECONDS`.
- [x] Safety: `sanitize_detail` redacts bearer tokens, `key=value` secrets and token-bearing URLs before anything is stored; the API key is never logged or printed. Every link is built by `core.notifications.absolute_url` from `SITE_URL` (https forced outside debug, defaulting to `https://RENDER_EXTERNAL_HOSTNAME`), and the link builders in registration, invitations, sign-up links, admissions, finance receipts and platform emails all use it. Verification and reset links stay single use and expiring (`PASSWORD_RESET_TIMEOUT=3600`).
- [x] Custom password reset: `accounts/password_reset.py` keeps Django's token flow but queues the message, rate-limits the request and builds the link from `SITE_URL`; `templates/registration/password_reset_email.txt` and `templates/emails/password_reset.html` accept either the new `link` or the legacy `protocol`/`domain` values.
- [x] Operations: `python manage.py check_email --to …` (prints status, provider and a sanitized response); platform **Email health** page (`platform/email-health/`) with the last 50 messages, failure/delivered/bounced/complained/suppressed counts and a Resend button; `/webhooks/resend/` verifies the Svix signature (with an HMAC fallback) and records delivered/bounced/complained events, suppressing hard bounces and complaints.
- [x] Docs/config: `render.yaml`, `.env.example` and `DEPLOY.md` updated for Resend, the verified domain, `SITE_URL`, `SERVER_EMAIL`, the webhook secret and troubleshooting; `check_email` and the Email health page are documented.
- [x] Tests: new `core/tests/test_email_pipeline.py` (24 tests: backend selection, no SMTP settings, each email type with text and HTML, generic HTML fallback, failures/retries/non-raising, suppression, `SITE_URL` links, per-address and per-IP limits, webhook signature and suppression, tenant isolation, the command). Updated `accounts/tests/test_signup_links.py` and `admissions/tests/test_admissions.py` for the new behaviour.
- [x] Verification: 1409 tests pass, `ruff check .` is clean and `makemigrations --check` reports no changes. Manual steps for a live deployment are in the session report and `DEPLOY.md` section 7 (verify the domain in Resend, set `RESEND_API_KEY`, `EMAIL_FROM_DOMAIN`, `DEFAULT_FROM_EMAIL`, `SERVER_EMAIL`, `SITE_URL`, add the webhook and `RESEND_WEBHOOK_SECRET`).

## Production uploads, platform rights and 10,000-user readiness (session 31, 2026-11-17)

### Uploads and object storage
- [x] Added `core/uploads.py` as the one place that validates and prepares an upload: image types (`jpg`, `png`, `webp`), image size (5 MB), document types (PDF, Word, Excel), PDF magic bytes, safe filenames, memory-bounded shrinking (longest side 1600 px, `Image.thumbnail`), and a clear `ValidationError` for every rejection. Model validators and `save()` shrink hooks were added to Student, Activity, AssignmentSubmission, StudentCard, ApplicantDocument, LMS Content/Assignment/Submission, operations StaffDocument/JobApplication/Policy and platform ApplicationDocument.
- [x] Production no longer writes uploads to the Render disk. `STORAGE_BACKEND=s3|r2|b2|supabase` selects `django-storages` S3 storage; `validate_storage_configuration()` raises `ImproperlyConfigured` at boot when `DEBUG=False`, the app is not testing and the backend is still `local`. Local disk remains the development default. `core/uploads.py` also builds absolute media URLs, so templates and API responses use the bucket/CDN URL rather than `/media/...`.
- [x] `core/tests/test_uploads.py` (28 tests) covers success, wrong type, too large, bad dimensions, PDF magic, resizing, safe names, local/S3 URL generation and the production storage guard. `DEPLOY.md` and `.env.example` list every `STORAGE_BACKEND`/`AWS_*` variable and the Supabase/R2/B2 setup.

### Platform administrator
- [x] `ensure_platform_admin` creates or repairs an account with `is_platform_admin`, `is_staff`, `is_superuser`, `is_active`, role `admin`, no institution, a verified email and a `PlatformStaff` row with the `main_admin` role. Startup logs, in plain English, that the account has full platform rights and must change the environment password.
- [x] `/platform/` now returns **404** to every authenticated non-platform user (including inactive platform staff and ordinary institution admins) and keeps **403** for a denial inside active platform staff. Institution creation (`platform/institutions/new/`), edit, suspend, reinstate, archive, restore and delete are covered end to end; delete requires a *current* archive, the default institution can never be deleted, and restore only reactivates an institution that is not suspended. The list is paginated at 25.
- [x] Added end-to-end tests for create → suspend → reinstate → archive → restore → delete, the default-institution guard, and institution-admin 404s.

### Performance and caching
- [x] Added indexes for the large-table filters, searches, ordering columns and foreign keys in admissions, academics, finance, accounts, LMS, operations and platform documents (migrations listed in the change set). `makemigrations --check` reports no pending changes.
- [x] Removed the dashboard's `Student.objects.all()` loop: balances now come from bulk aggregate dictionaries and the dashboard figures are cached (`core.cache.dashboard_figures`, invalidated by Invoice/Payment signals). `select_related`/`prefetch_related` removed the staff-department, application and student-search N+1s. Admissions applications, staff, platform institutions and the other list screens paginate at 25.
- [x] Redis is used when `REDIS_URL` is set, with a local-memory fallback. Permission codes, institution branding/settings, module switches, terminology, school mode and dashboard figures are cached; writes clear the affected keys. Permission caching is disabled under `TESTING`, and a version counter invalidates every permission cache after a role change.
- [x] `core.middleware` caches the institution lookup, adds low-data mode and records response size/time (`Server-Timing`). `ConditionalGetMiddleware` supplies ETags/304s for public pages and JSON.
- [x] Background jobs: `BackgroundJob` (migration `core/0019`) plus `config/celery.py`, `core/jobs.py` and `core/tasks.py`. Bulk import, newsletters and multi-card ID PDFs run on a Celery worker when a broker is configured and fall back to inline execution otherwise. The `/jobs/<pk>/` page polls `/jobs/<pk>/status.json`, shows a progress bar and links to the result or generated file. This keeps a 60-second Gunicorn timeout honest.
- [x] Added `gunicorn.conf.py` (`gthread`, `WEB_CONCURRENCY`, `GUNICORN_THREADS`, timeout 60, `max_requests` 1000 with jitter, forwarded-IP handling) and updated `render.yaml` to use it. `DEPLOY.md` now documents worker sizing, the Celery worker service, Redis, `CONN_MAX_AGE=600`, the Supabase **session** pooler and the 1,000/5,000/10,000+ sizing table.
- [x] `python manage.py measure_performance` reports row counts, real indexes, query count/time/page size for dashboard, student search, admissions, staff, debtors and global search, plus the cache, storage and connection settings. It runs on production data without writing anything.

### Data usage
- [x] WhiteNoise serves hashed, pre-compressed static files with a one-year immutable cache; `GZipMiddleware` compresses dynamic HTML/JSON/CSV. Static CSS/JS are minified by the existing build/collect pipeline; unused public CSS was removed and Chart.js is loaded only on the role dashboard when `LOW_DATA` is false.
- [x] Images are WebP with explicit `width`/`height`, `srcset` where several sizes exist and `loading="lazy"` below the fold; fonts use `font-display: swap` with a system fallback. The low-data mode removes charts and decorative images and replaces them with compact tables.
- [x] Public pages have ETags and long-lived static caching; the app shell service worker (`/sw.js`) caches only same-origin static files and `/offline/`, never authenticated HTML or POSTs. Live search is debounced and returns compact paginated JSON.

### Before/after (measured, not estimated)
- Dashboard: before, one query per student plus one per balance lookup, so cost grew with the roll; after, a fixed set of aggregate queries (three bulk dictionaries) and a 120-second cache. The count no longer changes when the student roll grows.
- Staff list: 25 rows now take **16 queries** regardless of row count (permission resolution, page count, page rows, one prefetch for departments, the without-profile query). Before the `prefetch_related`, departments added one query per row (25 extra).
- Admissions applications and staff/institution lists: before, every matching row was rendered; after, 25 per page.
- Uploads: before, a production upload wrote to the container disk and disappeared on the next deploy; after, it goes to the configured bucket and its URL is absolute.
- `measure_performance --no-pages` on the local SQLite copy: 13 users, 6 students, 7 invoices, 5 payments, 97 audit rows; cache LocMem, storage local, Celery inline. The same command on production is the number to compare against.
- Verification: **1,488 tests pass** (0 failures), `ruff check .` is clean and `makemigrations --check` reports no changes. `core/tests/test_performance.py` pins the staff-list query count from a cold cache and the 25-row page size; `core/tests/test_jobs.py` covers progress, tenant isolation and the inline fallback. The final full run also caught and fixed three regressions: the wizard now passes decoded bytes through the shared upload validator, `open_logo` keeps its RGBA contract for JPEG/WebP, and the query-count test clears the cache first so an earlier test cannot hide a query.

## Institution onboarding - Part 1: first administrator (session 32, 2026-11-18)
- [x] Decision: checklist items that come from live rows (logo, academic year, modules, other users, plan) are derived on every page; only one-time events are stored. Added `InstitutionOnboarding` and migration `platform_admin/0004_institutiononboarding.py`; the row never holds a password, invitation token or one-time link.
- [x] Added `platform_admin/onboarding.py`. It reuses `accounts.user_admin` for invitation creation/acceptance, temporary passwords, password reset, unlock and deactivation; `core.roles.ensure_roles` for the institution-admin role; `core.notifications` for all email; `core.audit.log_action` plus `platform_admin.services.log_platform_action` for audit; and `notify()` for the in-app institution notice. Platform staff are recorded as the real actor even though the underlying account services run without an institutional actor.
- [x] Added the onboarding checklist to the institution detail and administrator list screens, with progress, a percentage bar and plain-English "still missing" text. The administrator list now offers add, resend invitation, reset password, unlock, deactivate/remove and reactivate. Removing means deactivating so the audit trail survives. The last active administrator is protected.
- [x] Added `InstitutionAdministratorForm`, `onboarding_admin_create` and `onboarding_credential`. Invitation mode creates a single-use 72-hour set-password link; temporary-password mode creates a strong random password, forces a change at first sign-in and displays it once. The secret lives only in the session for the single display and is then popped; a second load redirects away with a friendly message. Copy and Print are available, and a failed email shows a collapsible one-time link instead.
- [x] The branded welcome email was unified: `platform_admin.services._send_welcome` now calls the onboarding service, so approvals and directly created institutions share one template and one central email path. Every new administrator receives it once; an explicit resend is audited as `institution.welcome_email_resent`. The message includes the `/i/<slug>/login/` link, the invitation link when applicable, the getting-started steps and support contacts `+263774573765` / `vanhuvaonejeffrey@gmail.com`. No password is ever emailed.
- [x] Added `platform_admin/tests/test_institution_onboarding.py` (10 tests): both access modes, one-use/expired invitations, temporary-password force change, institution-only scoping and 404 from `/platform/`, last-admin protection, non-platform 404s, checklist progression, audit without secrets, welcome resend and `SITE_URL` links. Updated `test_institution_admins_can_be_deactivated_and_reactivated` to add a backup admin first, because the new last-admin rule intentionally changes that older behaviour.
- [x] Debugging notes fixed: the temporary-password path now sends the welcome email (it previously sent only the account-created notice); the plan checklist step reverses `platform_admin:subscriptions` without a primary-key argument; the force-change assertion now follows the login POST to the next GET, where the middleware redirects; and the scoping test refreshes the user after invitation acceptance so the test client's auth hash matches the saved password.
- [x] Verification: 1,498 tests pass (0 failures), `ruff check .` is clean and `makemigrations --check` reports no changes. Full suite, lint and migration check were run again after the final test adjustment. Parts 2 and 3 of the onboarding plan remain.

## Institution onboarding - Part 2: web address, sign-in link and first sign-in (session 33, 2026-11-18)
- [x] Decision: slug rules live once in `core/slugs.py`. A slug must be a plain lower-case word, must not be a reserved top-level route, and must not collide with another institution or a live redirect. `unique_slug()` offers safe suggestions. `InstitutionSlugRedirect` (migration `core/0020`) keeps old `/i/<slug>/` and `/i/<slug>/login/` addresses working for 90 days, then expires both the redirect and the reservation. The login redirect keeps the query string. Inactive institutions never resolve.
- [x] Decision: a platform-created sign-up link is an ordinary `SignupLink` with a `created_by_platform_admin` marker (migration `accounts/0011`); it gains no special powers and follows the same kind/expiry/use limits and tenant rules. Platform staff create it from the institution detail screen and see the copyable `SITE_URL` link once.
- [x] Decision: custom domains are disabled until a person confirms the DNS TXT record and the `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` change; there is no live DNS lookup. Saving stores a verification token and shows the exact TXT name/value. Verifying sets `custom_domain_verified`, `custom_domain_verified_at` and `custom_domain_enabled`; `core.middleware` maps the host to the institution through a cache, and disabling keeps the record but stops the mapping.
- [x] The institution detail screen now has a sign-in-link panel: copyable branded URL, QR PNG from the installed `qrcode[pil]` (no new dependency), WhatsApp share link and enabled/disabled status. "Email sign-in link" uses `queue_message` with the branded HTML text/email template and the required support contacts. The UI checks `OutboundMessage.status != "sent"` and shows a plain-English failure instead of claiming success.
- [x] Debugging notes: the first-sign-in handoff was moved out of `user_logged_in` (which also fires for `Client.force_login`) into `LoginLockoutMixin.form_valid`, with `queue_setup_after_login()` setting `needs_institution_setup` only after a real password login. `FirstSignInSetupMiddleware` and `InstitutionLoginView.get_success_url()` consume the flag. Guided setup (`core/institution_setup.py`) is institution-scoped and uses the existing setup template; it deliberately does not grant `admin.create_institutions`. The final step creates the sign-up link, displays it once, and a second "Finish setup" post records `setup_completed_at`.
- [x] Added `core/tests/test_slugs.py` (27 tests) and `platform_admin/tests/test_onboarding_part2.py` (24 tests). They cover invalid/reserved/duplicate slugs, 90-day redirects and expiry, host mapping when verified/enabled only, QR and link panels, central email queue and support contacts, sign-up-link rules, custom-domain lifecycle, real-login-only setup flag, tenant isolation, 404 for non-platform users, 403 for platform staff without a permission, and audits with no tokens/passwords.
- [x] Verification: `ruff check .` clean, `python manage.py makemigrations --check --dry-run` reports "No changes detected", `python manage.py check` shows only the expected console-email warning. Full suite collected 1,549 tests: 1,548 passed; the sole failure was the unrelated flaky `finance/tests/test_tax_and_numbering.py::ConcurrentNumberingTests::test_concurrent_allocations_are_unique_and_gap_free`, which passes in isolation. Part 3 (final gap-check, report and manual testing instructions) remains.

## Institution onboarding - Part 3: handover (session 34, 2026-11-18)
- [x] Gap-check against every requirement: onboarding screens reuse the existing invitation, branding, audit, notification, central email, sign-up-link and login code; no direct SMTP/API calls; audit rows contain no secrets; `/platform/` exposes no student/health/counselling/finance/results records; authenticated non-platform users get 404 and active staff without a code get 403.
- [x] Fixed the gaps found: `queue_setup_after_login()` now requires an onboarding row with `first_admin_created_at` set, so institutions that predate this feature sign in normally. `institution_admin_action()` returns `AdminActionResult` with an `email_failed` flag derived from the latest `OutboundMessage.status`, so a failed invitation/reset still hands over the one-time credential. `send_welcome_email()` only records `welcome_email_sent_at`/count when the status is `"sent"`; the approval follow-up email now passes the support contacts into its branded template. Corrected two docstrings/template notes that wrongly claimed invitation links are never in any email.
- [x] Added `platform_admin/tests/test_onboarding_part3.py` (6 tests: failed welcome resend, failed login-link email, failed invitation resend, failed password reset, support contacts in approval email, no token in approval audits) and one legacy-admin regression test in Part 2. Onboarding-specific coverage is now 68 tests.
- [x] Wrote `ONBOARDING_REPORT.md` and `MANUAL_TESTING.md`. Final verification is fully green: **1,556 tests pass**, `ruff check .` clean, `makemigrations --check` reports no changes. The earlier flaky finance concurrency test also passed in this run.

## Dedicated bulk upload of students (session 35, 2026-11-19)
- [x] Added a new `ImportBatch.Kind` value and importer kind `student_upload`,
  with `students.bulk_import` in the permission catalogue and role matrix. The
  old `students` bulk import and `students.create` behaviour are unchanged.
- [x] Added `core/bulk_upload.py`: a read-only `Planner` that builds every row
  (create, update, no_change, skip, error, warnings, old-to-new changes and
  objects to create) and a writer that re-checks and writes each row in its own
  transaction into one `ImportBatch`. `perform()` queues invitations once;
  `deliver_invitations()` sends at most `BULK_UPLOAD_EMAIL_CHUNK` messages per
  call so a large upload cannot flood the institution's email allowance.
- [x] Added `BulkUploadMatch(institution, match_key)` (migration
  `core/0022_importbatch_created_objects_importbatch_options_and_more.py`) so
  a file without registration number, email or username updates the student
  it created before instead of duplicating them. A strong identifier always
  wins over a fingerprint; surname + date of birth + programme only flags a
  row for review.
- [x] Added `Student.gender`, matching indexes and user indexes (migrations
  `academics/0023_...` and `accounts/0013_...`). The bulk permission migration
  is `core/0021_students_bulk_import_permission.py`.
- [x] Reused `create_student_record`/`provision_user` with `send_email=False`
  and added an explicit `no_login` access mode. Invitation messages are queued
  through `send_email(..., send=False)` and are rate-limited, branded and
  cancellable by undo. Credentials appear only in the one-time result page and
  download; audits never contain a password or token.
- [x] Added dedicated URLs and templates (`bulk_upload_start`,
  `bulk_upload_preview`, `bulk_upload_result`, `bulk_upload_template`, CSV
  reports and undo) under `/imports/students/...`, linked from the navigation
  and the import list. The preview uses the shared import reader, supports
  column aliases/mapping, dry runs, filters and a report even before the batch
  has run. Generic import screens redirect to the dedicated ones.
- [x] Missing programmes/classes/streams/years/courses are created only when
  ticked and marked "Created by import, review needed"; existing objects are
  never renamed or deleted. Enrolment additions preserve existing marks and
  attendance, registration rules can be skipped with a warning, and removals
  need a confirmed option and set status to `dropped` rather than deleting.
- [x] Safe undo deactivates created users, cancels queued invitations and
  leaves any created student with an invoice, payment, mark or attendance
  untouched. Undo data is JSON-serialised with dates as ISO strings and
  compared with a tolerant helper so a legitimate undo is not mistaken for a
  later edit.
- [x] Fixed bugs found while making the new suite green: `Planner.load()` read
  `BulkUploadMatch` rows as dictionaries; user updates and their undo used a
  model class instead of `User.objects` and rolled back; enrolment-only changes
  were not
  promoted to `update` because the writer inspected the stale local list;
  duplicate identity rows flagged only the later row; update rows with a blank
  surname were rejected before matching; `undo_log` contained raw dates.
- [x] Hardened `finance.services.next_document_number` with a process-wide
  SQLite lock. The 10-thread numbering test now passes repeatedly instead of
  occasionally exhausting its retries with "database is locked"; PostgreSQL
  still uses its row lock.
- [x] Added `core/tests/test_bulk_upload.py` (29 tests): new file, re-upload,
  blank-cell preservation, all strong match paths, fuzzy review, duplicate and
  bad rows, missing-object create/reject, enrolment add/remove, year gating,
  registration rules, tenant isolation, permissions, credentials once,
  secret-free audit, invitation chunking, undo safeguards (including reverting
  updates and refusing later edits), the Excel template, and a 5,000-row
  bounded-query planning check. Added a sample CSV and nine manual scenarios
  to `MANUAL_TESTING.md`.
- [x] Verification: full suite **1,630 passed**, `ruff check .` clean,
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected". `manage.py check` shows only the expected console-email warning.

## Session 36 - Categorised errors, offline handling and safe retries (2026-11-20)

The brief was that every error must say exactly what kind of problem happened,
on the page, in an inline alert, in JSON and in the logs, without leaking
secrets or breaking the existing app. The work started from the existing
notification/email error wording and the service worker shell, then made one
catalogue the source of truth for every surface.

- [x] Built `core/error_catalogue.py` with 30 immutable `ErrorSpec` entries in
  six categories. Every entry carries an icon, a title that names the category,
  a plain-English explanation, an action, numbered steps, who can fix it and a
  default HTTP status. The same list feeds pages, inline messages, JSON, logs,
  the browser script and the platform Errors page.
- [x] Built `core/errors.py` as the single classification/rendering/logging
  path. `classify_exception()` maps database errors, permission denials,
  validation/upload errors, provider failures, timeouts, CSRF, 404s and HTTP
  statuses to a catalogue code. Suspended institutions override a generic
  denial so the message points at billing. `safe_detail()` reuses the email
  sanitizer before any extra text is shown or stored.
- [x] Added `ErrorRecord` (`core/0023_errorrecord.py`) and
  `ErrorContextMiddleware`. The middleware assigns a request ID, records and
  logs exceptions and direct 4xx/5xx responses exactly once, and marks only
  anonymous public GETs with `X-EduSphere-Offline-Public: 1`. The structured log
  line contains category, code, reference, request ID, view, method, duration
  and safe user/institution IDs; it never contains request data or a traceback.
- [x] Wired global 400/403/404/500 handlers and the DRF exception handler.
  JSON errors carry the same title, steps, category, code, reference, request
  ID and support contacts as the page. Technical fields appear only when
  `can_view_error_details()` is true; a normal user never sees exception
  classes, view names, tokens or SQL.
- [x] Added the error templates, styles and `core/templatetags/error_ui.py`.
  Each page shows the icon and text category, code, explanation, numbered
  steps, Try again, Go back, an `ERR-XXXXXX` reference with Copy, time, support
  contacts and a collapsed Technical details panel. Added a dedicated
  `/billing/contact/` page that stays reachable for a suspended school.
- [x] Added `static/js/error-handling.js` and served the catalogue from
  `/errors/catalogue.js` as an external script, so the CSP stays intact and
  browser wording cannot drift. The script handles online/offline, a 5-second
  slow warning, a 15-second timeout, header/status classification, safe retry
  with idempotency keys and form restoration that deliberately skips passwords
  and files.
- [x] Hardened the service worker: it caches the shared shell plus only
  explicitly marked public pages, never caches a signed-in HTML response, and
  falls back to `/offline/` for an uncached navigation while offline.
- [x] Replaced generic messages in login/lockout, registration, email failure,
  uploads, permissions, suspension, payments and webhooks. Added idempotency to
  payment start and public registration; a replayed registration key now reuses
  the first result. Fixed a template regression found by that test: the replay
  page eagerly evaluated `student.user.username` when the replay context had no
  student, so it now uses a safe `if`/`elif` lookup.
- [x] Upgraded Platform → Errors with filters, a seven-day trend, reference
  lookup and links to Email health, Jobs and Audit. Added `platform/jobs/` and
  `errors.view_details` for support/compliance staff. Technical record fields
  are shown only to permitted staff.
- [x] Added `core/tests/test_error_handling.py` (27 tests) for all categories,
  the catalogue shape, classification, page/JSON parity, secret-free output,
  permission gating, offline caching, suspension, browser script rules and
  idempotent retries. `docs/ERROR_CODES.md` lists all 30 codes with the exact
  message, who fixes each one and a manual trigger.
- [x] Fixed two classification/rendering bugs found while making the suite
  green: `ValidationError("Enter a number")` was matched as `FILE-REJECTED`
  because "number" contains "mb" (now a word-boundary match), and the
  registration replay page crashed on a missing `student` context variable.
- [x] Verification: full suite **1,657 passed**, `ruff check .` clean,
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected", and `python manage.py check` shows only the expected
  console-email warning. `docs/ERROR_CODES.md` was checked against the
  catalogue: all 30 codes and their exact messages are present.

## Rebrand to Vanpere (session 37, 2026-11-20)
Status: complete.

- [x] Built `config/brand.py` as the single visible identity source and wired
  it through `PLATFORM_BRAND` and `{% brand_* %}` template tags. A rename is now
  one edit in `config/brand.py` or the `BRAND_NAME` environment variable.
- [x] Converted every user-facing template, Python message, email, PDF and
  document to the brand values, including login/registration, error pages,
  platform admin, Swagger, management commands, docs, legal pages, PWA
  manifest, tab titles, meta/OG tags, robots and sitemap.
- [x] Added the small “Powered by Vanpere” attribution with a link, switchable
  off per institution through `Institution.show_platform_branding`
  (`core/0024`). Institution names, logos and colours remain their own.
- [x] Added placeholder brown/green/marigold wordmarks and favicon/PWA icons;
  platform admins can upload replacement raster artwork from Platform settings
  using the existing safe image pipeline.
- [x] Added `/about/` with factual product and publisher information and no
  invented claims. Updated email sender display name, subjects, headers and
  footers; institution emails keep their display name with a switchable
  “via Vanpere” footer.
- [x] Updated deployment and docs: `render.yaml`, `DEPLOY.md`, `.env.example`,
  `README.md`, the test/check lists and error docs. `DEPLOY.md` now explains
  the GitHub/Render rename, custom domain, host/CSRF/`SITE_URL` settings and
  permanent `OLD_SITE_URL` redirects.
- [x] Added `config/tests/test_brand.py` (12 tests) with an explicit allow-list
  for internal names and surface checks for pages, email, manifest, errors,
  PDF attribution, white-label mode and tenant branding preservation.
- [x] Verification: full suite **1,669 passed**, `ruff check .` clean, 299
  templates compile, `python manage.py makemigrations --check --dry-run`
  reports "No changes detected", and `python manage.py check` shows only the
  expected console-email warning.

## Session 38 - Configurable academic hierarchy (2026-11-23)

The brief was to add one configurable School/Faculty > Department > Programme >
Course hierarchy to every institution type without breaking the existing school
workflows. The safest route was to keep every existing model and screen working
with empty hierarchy fields, then layer the new structure on top.

- [x] Added `Institution.Type.UNIVERSITY` and the `AcademicSchool` model. The
  migration also adds the nullable school/department links on Department,
  Programme, SchoolClass and Student, the class capacity/room/active fields and
  `GradeLevel.promotion_to`. No existing row is changed by the migration; a
  school-less department still works exactly as before.
- [x] Put all built-in academic defaults in `academics/hierarchy.py`. It owns
  the per-type templates, level sets (Year 1-4, Year 1-6, Grade 8-13, combined
  primary+secondary, university years), Term/Semester schemes, stream
  suggestions, terminology defaults, validation and `apply_template()`. Using
  one module means the wizard, import, platform editor and tests cannot drift.
- [x] Fixed the level-code generator while building the templates: `Year 1` now
  becomes `Y1` instead of `YEAR`, which previously collided with `Year 1-6`.
  `validate_template_data()` now rejects duplicate level codes with a clear
  message before a bad template can be published.
- [x] Added versioned academic templates to the platform area. Publishing
  always writes `version = latest + 1` (the earlier draft reused a version and
  hit a UNIQUE constraint); activating an old version only changes the default
  copied into future institutions. `apply_template()` logs
  `academic.template_applied` against the new institution, not just globally.
- [x] Rebuilt the wizard flow as details -> academic type -> structure -> year
  -> modules -> grading -> admin -> demo. The structure step renders the
  template rows with checkboxes, editable name/code/description fields and
  add-row fields; the cleaned state keeps selected/renames/additions separate.
  Primary/secondary hide the School/Faculty list and create a single Term by
  default; other types get Semester when that scheme is chosen. The wizard
  derives term names from `academic["term_scheme"]`, so the added tests do not
  assume a typo in the fixture.
- [x] Added the Academic structure and Grades and classes screens with a
  shared `_require_manage()` helper. Reads allow admin/registrar; writes need
  `academics.manage_structure`, call `log_permission_denied()` on refusal and
  audit every success. Deactivation is preferred to deletion; a hard delete is
  only offered when no student, class, programme, course, timetable, mark,
  result or fee record references the row.
- [x] Extended the academic setup importer with four new kinds and Excel
  sheets. Each kind selects its own sheet automatically, matches an existing
  row by code first and name second, and never renames or deletes existing
  structure. Added a platform-style template download and the same permission
  code and audit fields as the other import kinds.
- [x] Added reusable hierarchy filters (`hierarchy_context`,
  `filter_hierarchy`, `has_hierarchy_filters`) and a single partial/tag pair for
  the UI. Applied them to student/staff/course/programme/timetable/results and
  finance screens. Dashboard views now skip `core.cache.dashboard_figures()`
  when a filter is active, because the cached global numbers otherwise hid the
  filtered result.
- [x] Added hierarchy crumbs and labels to the report-card/transcript screens
  and PDFs, including school and department columns in the student table.
- [x] Added 35 tests across five new modules. The first full run had one
  failure: the staff-list performance guard expected 17 queries, but the new
  hierarchy filter choices add four fixed queries (schools, departments,
  levels and classes). The guard now expects 21 and still catches an N+1; the
  focused test passes. Fixed a ruff import-order issue found by the final lint.
- [x] Verification: full suite **1,736 passed**, `ruff check .` clean,
  `python manage.py check` shows only the expected console-email warning and
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected". Manual scenarios are in `MANUAL_TESTING.md` section 11.

## Session 39 - Optional AI assistance, part 1: provider layer (2026-11-25)

The brief was to add useful AI assistance without changing any existing
feature when it is off. The first part deliberately touches no screens or data:
it establishes one narrow, testable boundary to the model provider.

- [x] Added the `ai` Django app and registered it in settings, pytest paths,
  coverage and ruff's first-party list. No URL, model or navigation entry was
  added in this part.
- [x] `ai/providers.py` defines `CompletionRequest`, `Completion`, a small
  provider protocol, `AnthropicProvider` and `MockProvider`. The production
  provider posts to the Anthropic Messages API with `urllib.request`; it has no
  third-party SDK, no Celery import and no database access. The key and both
  model names come from `AI_API_KEY`, `AI_MODEL_FAST` and `AI_MODEL_SMART` at
  call time. A missing value fails before any network call.
- [x] The default timeout is 15 seconds (`AI_TIMEOUT_SECONDS`). Timeouts,
  network failures, rejected keys, HTTP errors and malformed responses each
  raise a typed error with a short user-facing message. Provider exception
  text, headers and keys are never returned to a caller.
- [x] `ai/service.py` owns `ask_ai()`. It clamps `max_tokens`, converts any
  provider failure into a safe `AIResult`, and exposes token counts for the
  later usage log. Tests can inject a `MockProvider` through
  `settings.AI_PROVIDER_INSTANCE`.
- [x] Documented the environment variables and provider boundary in
  `docs/AI.md`, `.env.example` and `render.yaml`. Render variables are
  `sync: false`, so neither a key nor a model name is stored in the repo.
- [x] Added 18 tests in `ai/tests/test_providers.py`. They verify environment
  configuration for both tiers, the exact request payload and timeout,
  endpoint overrides, every failure class, safe error messages, mock request
  recording, token accounting, the output cap and no Celery import.
- [x] Two failures were found and fixed while writing the tests: `_urlopen`
  is an instance attribute and cannot be patched at class level, and the
  no-Celery scan matched the module docstring. The tests now patch
  `urllib.request.urlopen` and check only import statements.
- [x] Verification: baseline **1,736 passed**; after part 1 the full suite is
  **1,754 passed** (221 warnings), `ruff check .` clean, `python manage.py
  check` shows only the expected console-email warning, and
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected".

## Session 39 - Optional AI assistance, part 2: controls (2026-11-25)

- [x] Added the tenant-scoped models `AIFeatureSetting`, `AIConsent` and
  `AIUsageLog`, plus a singleton `AIPlatformSetting` whose row 1 starts
  **disabled**. Migrations `ai/0001_initial` and `ai/0002_seed_ai_permissions`
  add the tables and grant `ai.use`, `ai.manage` and `ai.view_usage` only to
  the institution administrator role.
- [x] `ai/access.py` resolves the institution and applies one ordered decision:
  signed-in user, institution, platform kill switch, valid feature code,
  institution switch, `ai.use` permission, then active consent for that feature
  or all features. A missing row always means off.
- [x] `ai/service.py` gained `ask_ai_for_user()`. It checks every gate and all
  four limits before the provider call, records a zero-token `blocked` row on a
  refusal, and records status, model, tier, tokens, estimated cost and a
  redacted prompt summary on success or provider failure. No full prompt or
  answer is stored.
- [x] `/ai/consent/` shows the provider, the exact categories shared for each
  feature, what is recorded, the consent version, who accepted and when. It
  stores a snapshot of the disclosure, accepts "all features", and supports
  immediate withdrawal without deleting the historic record. The `/ai/home/`
  landing page links the controls a person may use.
- [x] `/ai/settings/` lets only `ai.manage` switch a feature on or off and set
  the institution/user daily and monthly limits, with an audit entry for every
  change. `/ai/usage/` shows counts beside the limits and a redacted log;
  administrators see their own institution's rows and other users see only
  their own.
- [x] Platform staff get `/platform/ai/` and a one-click `/platform/ai/close/`,
  both behind `platform.manage_settings`, so every AI feature can be shut off
  without waiting for individual schools.
- [x] `ai/redaction.py` now removes caller-supplied names, emails, URLs, phone
  numbers, ID-like numbers, address lines and labelled personal fields before a
  prompt is sent or summarised in the log. Protected-category refusals and
  tenant-isolation proofs are part 3.
- [x] Added 13 tests in `ai/tests/test_controls.py`: default-off behaviour,
  the kill switch overriding institution switches, per-feature and per-school
  switches, consent acceptance/withdrawal and its stored evidence, the
  permission gate, institution and user limits blocking before a call,
  redacted log summaries and cost estimates, the settings and usage screens,
  unknown features and the default platform state.
- [x] Documented the switches, consent disclosure, per-feature categories,
  usage log and cost-rate variables in `docs/AI.md`, `.env.example` and
  `render.yaml`.
- [x] Verification after part 2: focused `ai` suite **31 passed**; full suite
  **1,767 passed** (221 warnings); `ruff check .` clean; `python manage.py
  check` shows only the expected console-email warning; and
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected".

## Session 39 - Optional AI assistance, part 3: privacy (2026-11-25)

- [x] `ask_ai_for_user()` now redacts both prompts before a provider call. The
  provider receives `[name]`, `[id]`, `[phone]`, `[email]`, `[address]` and
  `[link]` placeholders instead of the original values. Redaction runs on
  structured values before caller-supplied names so an email containing a name
  stays an email placeholder.
- [x] Added `ai/privacy.py`. It refuses a prompt when health, medical,
  medication, diagnosis, clinic, counselling, therapy or related sensitive
  words appear, or when another institution's name or slug appears. The check
  runs on the exact redacted text that would be sent, including system prompts.
- [x] Fixed a tenant-isolation gap: `check_access()` now refuses a call when a
  caller supplies an institution other than the signed-in person's own.
  `ask_ai_for_user()` always writes the log row against the person's own school,
  so a foreign institution never receives a blocked row either.
- [x] Blocked rows now use a generic summary ("Blocked before sending; nothing
  was shared."), so a refused health, counselling or cross-school prompt is
  not copied into the usage log.
- [x] `ai/tests/helpers.py` shares the control setup between the control and
  privacy tests. Added seven tests in `ai/tests/test_privacy.py`: all personal
  categories removed from the provider request, health/counselling refusal,
  health terms in a system prompt, another school's name and slug refusal,
  explicit foreign institution denial and own-school logging, own school name
  still allowed, and a bounded redacted summary.
- [x] Two redaction bugs were found and fixed: an email containing a known
  name stopped matching the email pattern, and a generic label rewrite replaced
  already-safe placeholders. Structured values now redact first, and labelled
  values that already contain a placeholder are left alone.
- [x] Updated `docs/AI.md` with the enforced privacy rules and the deliberate
  consequence that even a question about the health module is refused.
- [x] Verification after part 3: focused `ai` suite **38 passed**; full suite
  **1,774 passed** (221 warnings); `ruff check .` clean; `python manage.py
  check` shows only the expected console-email warning; and
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected".

## Session 39 - Optional AI assistance, part 4: features (2026-11-25)

- [x] Added five permission-gated screens linked from `/ai/home/`:
  `/ai/help/`, `/ai/draft/`, `/ai/translation/`, `/ai/insights/` and
  `/ai/search/`. Every screen resolves the institution locally and calls a
  service in `ai/assistance.py`; none calls a provider directly and none writes
  a business record.
- [x] The help assistant resolves the signed-in person's guide from
  `core.help.GUIDES` and `guide_for(user)`, filters out protected-task titles,
  and sends only the redacted question, the guide title and matching task
  titles. URLs are added locally with `reverse()`, never accepted from the
  model. A missing match or unsupported wording returns "I don't know."
- [x] Draft writing covers report-card comments, announcements, fee reminders
  and letters. The result is an editable textarea headed "AI draft, please
  review"; there is no save or send endpoint, no announcement or other record
  is created, and the screen tells the person to copy an approved draft into
  the ordinary workflow.
- [x] Translation covers English, Shona and Ndebele and labels every result
  "needs review". Insights require a dashboard permission and send only
  `Label: value` lines calculated locally by `_staff_dashboard_figures()` and
  `_role_dashboard_figures()`; the screen always shows those figures and drops
  the wording if a number is missing.
- [x] Natural-language search sends only the redacted request and the five
  fixed code/label options. Tokens are intersected with `SEARCH_CODES` before
  any query, and `core.search.search_groups()` — extracted from the existing
  global search and reused by both screens — applies the same per-record
  permissions, tenant scope, module flags and 20-result cap. The model never
  supplies SQL, a filter condition or record values.
- [x] Added `ai/output.py` as the shared output boundary: links, HTML tags,
  command-like lines and control characters are removed and each feature's
  text is capped before rendering.
- [x] Added 19 tests in `ai/tests/test_features.py` covering guide source
  selection and local links, "I don't know.", draft kinds and preview-only
  behaviour, translation labels, insight figure preservation and fallback,
  allow-list search and permission filtering, output cleaning, screen access
  and the `ai.home` links. One test assertion was corrected because it
  mistakenly treated the test's own question text as a leaked source phrase.
- [x] Updated `docs/AI.md` with each feature's exact sent categories, returned
  labels, local links, preview-only rule, figure fallback and fixed search
  allow-list, together with what a school accepts on the consent screen.
- [x] Verification after part 4: focused `ai` suite **57 passed**; full suite
  **1,793 passed** (221 warnings); `ruff check .` clean; `python manage.py
  check` shows only the expected console-email warning; and
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected".

## Session 39 - Optional AI assistance, part 5: safety and feedback (2026-11-25)

- [x] Added `ai/safety.py`. Every caller-supplied prompt is wrapped as
  `<user_content>...</user_content>`, closes and role tags are neutralised, and
  the system prompt gets a standing rule that the wrapped text is data and can
  never change the assistant's instructions. Override phrases, hidden role tags
  and "you are now" attempts are refused locally before a provider call; the
  refusal writes a zero-token `blocked` row and shows a short message. A label
  such as `Instructions:` or `Question:` no longer hides an override: the first
  pattern only matched the start of a line, so the draft screen's
  `Instructions: Ignore previous instructions...` was allowed through. The
  detector now accepts an optional field label before the override phrase.
- [x] Model output is checked again. `ai/output.sanitize_output()` strips
  instruction-like lines as well as links, HTML, command-like lines and control
  characters. Each feature additionally refuses output that still looks like an
  instruction, and drafts get local profanity and harm checks: unsafe wording
  produces "The draft contained wording we cannot show. Nothing was saved..."
  and no preview is rendered.
- [x] Added `ai/ratelimit.py` with per-user and per-school/IP cache buckets.
  Every attempt is counted, including one refused by another control, so
  reopening a switch cannot reset the window. Defaults are 10 calls per person,
  30 per school connection and a 60-second window; `AI_RATE_LIMIT`,
  `AI_RATE_IP_LIMIT` and `AI_RATE_WINDOW_SECONDS` are in `config/settings.py`,
  `.env.example` and `render.yaml`. A full bucket returns the safe "too quickly"
  message before any provider call.
- [x] Added tenant-scoped `AIFeedback` (migration `ai/0003_aifeedback.py`) and
  POST-only `ai:feedback`. Each answer carries its `AIUsageLog` id; the form
  sends a category and an optional comment, which is redacted, sanitised and
  capped at 500 characters. Only a log belonging to the same person and school
  can be linked, a GET and an unknown feature are rejected, the redirect target
  is checked, and the submission is written to the audit log without secrets.
  `ai:feedback_review` lists at most 100 reports for the institution; platform
  staff with `ai.view_usage` can review across schools.
- [x] Added 14 tests in `ai/tests/test_safety.py` covering injection refusal,
  data wrapping and tag neutralisation, output instruction removal, draft
  profanity/harm refusal, per-user and per-IP limits, feedback redaction and
  linking, foreign-log rejection, bad input and tenant-scoped review. Isolated
  the cache in the existing controls, privacy and feature test setups because
  reused user ids and the shared LocMem cache carried burst counts between
  `TestCase` rollbacks.
- [x] Updated `docs/AI.md` with the safety rules, burst-limit variables and the
  feedback flow, including what is stored locally and what a school must agree
  to.
- [x] Verification after part 5: focused `ai` suite **71 passed**; full suite
  **1,807 passed** (221 warnings); `ruff check .` clean; `python manage.py
  check` shows only the expected console-email warning; and
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected".

## Session 39 - Optional AI assistance, part 6: final documentation and verification (2026-11-25)

- [x] Cross-checked the original requirements against the code and tests:
  server-only Anthropic calls driven only by environment variables with a mock
  provider for tests; kill switch, per-feature institution switches, consent
  snapshots and daily/monthly limits; redaction and protected-category refusal
  with tenant-isolation tests; all five features with their labels and local
  fallbacks; injection, output and draft safety plus burst limits and feedback.
- [x] Finished `docs/AI.md` so it documents every feature, the exact categories
  sent to the provider, the locally calculated figures and fixed search
  allow-list, the labels shown to users, the data recorded locally, the safety
  rules, the burst-limit environment variables, the bad-answer feedback flow
  and what a school must agree to before enabling AI.
- [x] Final verification on the committed code: focused `ai` suite **71 passed**;
  full suite **1,807 passed** (221 warnings); `ruff check .` clean;
  `python manage.py check` shows only the expected console-email warning; and
  `python manage.py makemigrations --check --dry-run` reports "No changes
  detected". No existing feature regressed and no business-record write path
  was added to AI assistance.
- [x] Parts 1-5 were committed separately as `37d656f`, `0e6bea4`, `928e801`,
  `4f21997` and `bc14690`; this documentation and status update is its own
  final commit.
