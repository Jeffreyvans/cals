# PROGRESS.md

Started: 2026-10-21. Session 1.

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

## What's done
- [x] Read all files; ran `manage.py check` (ok), `makemigrations --check` (ok), `manage.py test` (276 passed), installed test/dev tools.
- [x] Created PLAN.md and PROGRESS.md, git init + baseline commit.
- [x] Phase 0 complete: pytest config, factories, conftest, Makefile, ruff/black/pre-commit, baseline tests.
- [x] Phase 1 tenancy: `core` app with `Institution`, `TenantModel`, `TenantManager`, `InstitutionMiddleware` (user -> subdomain -> path prefix), `use_institution` context manager and a cached default-institution lookup.
- [x] Every academic and finance row now carries its institution, with per-institution unique student numbers, receipt numbers, credit-note numbers, course/programme/subject/class/year codes and grade-band letters.
- [x] Data migration `core/0002_default_institution` creates the default institution (Lindenet Institute of Technology), seeds its module switches, hands every existing row to it and seeds the default role matrix.
- [x] Roles and permissions as data: `Role`, `Permission`, `RolePermission`, `ensure_roles`, legacy role map, `permission_required_code`, `user_has_code`, and the `perms` / `perms_code` / `modules` / `module_on` template tags.
- [x] New screens: institution settings (details, branding, module switches), roles and permissions with a working permission matrix, role create/edit/delete, notifications page, institution-wide audit trail with filters and CSV export, restricted-record-view log.
- [x] Notification bell in the shell with an unread count, in-app notifications, outbound message queue with console/SMTP/SMS/WhatsApp providers.
- [x] Feature flags hide modules from the menu and return 404 from their pages.
- [x] `core/tests` covers tenancy isolation, role seeding and the permission matrix, notifications and the audit trail, and the module switches: 53 new tests. Full suite: 329 passed.
- [x] Lint is clean (`ruff check .` -> all checks passed).

## Next up
- Phase 1 remainder: institution terminology screen, departments and staff profile screens, the approval workflow screens with step notifications, the setup wizard, and the CSV/Excel bulk import.
- Then Phase 2 (academics hardening, results engine, marks workflow, report cards).

## Open questions
- None at start.
- Whether bulk import should be CSV-only for Phase 1 or also accept `.xlsx`; `openpyxl` is already a dependency, so both are possible.

