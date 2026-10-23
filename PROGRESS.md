# PROGRESS.md

Started: 2026-10-21. Session 1. Last updated: 2026-10-23 (session 5, Phase 2 in progress).

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

## Bugs found
- None blocking baseline; tests pass. `makemigrations --check` clean. Lint has 194 issues (mostly import/style) but tests unrelated to style pass.
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
- [ ] Timetable clash detection, assessment components, GPA and class positions, marks workflow, report cards and transcripts.

The whole suite is now 491 tests, all passing; `ruff check .` is clean and `makemigrations --check` reports no changes.

## Next up
- Continue Phase 2: timetable clash detection, assessment components and weighting, GPA and class positions, the marks workflow, report cards and transcripts.
- Phase 3 starts with the chart of accounts and the double-entry ledger; the `FeeStructure`/`FeeItem` models added for the import will be completed there.

## Open questions
- None at start.
- Bulk import format is settled: CSV and `.xlsx` are both accepted, `openpyxl` is a dependency.
- Phase 2 will need a decision on whether the existing `Programme`/`Course` tables stay as they are or are renamed to the Phase 2 names (`ProgrammeYear`, `CourseOffering`).
