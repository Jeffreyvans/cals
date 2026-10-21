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

## Bugs found
- None blocking baseline; tests pass. `makemigrations --check` clean. Lint has 194 issues (mostly import/style) but tests unrelated to style pass.

## What’s done
- [x] Read all files; ran `manage.py check` (ok), `makemigrations --check` (ok), `manage.py test` (276 passed), installed test/dev tools.
- [x] Created PLAN.md and PROGRESS.md, git init + baseline commit.

## Next up
- Phase 0 remaining: add pytest config in pyproject (ensure DJANGO_SETTINGS_MODULE present), create `conftest.py` and `factories/` for every model, update Makefile to match spec, add ruff/black configs fully and pre-commit checks, add missing baseline tests if any gaps found (payment overpayment/receipt uniqueness/student number already covered). Then tick Phase 0 done and commit.

## Open questions
- None at start.
