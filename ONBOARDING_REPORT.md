# Institution onboarding - final report

Date: 2026-11-18. Scope: let a platform administrator take a newly created
institution from empty to working on the same day, reusing the invitation,
branding, audit, notification, central email, sign-up link and login code that
the rest of Vanpere already uses.

## Delivered in three parts

| Part | Commit | What it added |
| --- | --- | --- |
| 1 - First administrator | `5a636df` | Onboarding checklist, first admin by invitation or temporary password, one-time credential screen, forced password change, welcome email and lifecycle actions. |
| 2 - Links and first sign-in | `f82283b` | Shared slug rules, 90-day old-link redirects, branded login-link panel with QR, platform sign-up links, custom domains and the guided five-step setup after a real first login. |
| 3 - Handover | this commit | Failure-path fixes, legacy-admin protection, support contacts in the approval email, extra tests, this report and the manual testing guide. |

## Requirement coverage

| Requirement | Status | Evidence |
| --- | --- | --- |
| Onboard a new institution from `/platform/` reusing existing code | Done | `platform_admin/onboarding.py` calls `accounts.user_admin`, `core.roles.ensure_roles`, `core.notifications`, `core.audit` and `accounts.signup_links`; screens are routed in `platform_admin/urls.py`. |
| All email through `core.notifications.queue_message` / `send_message` | Done | No direct SMTP/email API in onboarding code; enforced by `core/tests/test_email_source_scan.py`. Mailjet/central queue is used for welcome, invitation, login-link, reset and approval messages. |
| Audit rows never contain passwords, invitation tokens or one-time links | Done | Audit details carry only usernames, recipients, access modes and statuses. Covered by `test_institution_onboarding.py`, `test_onboarding_part2.py` and `test_onboarding_part3.py`. Secrets live only in the one-time session display and the intended recipient's email. |
| Platform admins cannot reach student/health/counselling/finance/results data from `/platform/` | Done | `platform_admin/access.py` keeps `PROTECTED_PREFIXES` away from `/platform/`; platform pages are consent, institutions, plans, staff and aggregate health/usage counts only. Covered by `test_platform_administration.py`. |
| Branded emails include `+263774573765` and `vanhuvaonejeffrey@gmail.com` | Done | Welcome and login-link bodies/templates, approval follow-up, and in-app notice. Part 3 closed the approval-follow-up gap; asserted in tests. |
| Non-platform users get 404 from `/platform/*`; active staff without a permission get 403 | Done | `platform_required()` raises `Http404` unless there is an active `PlatformStaff`, then `PermissionDenied` for a missing code. Authenticated institution users get 404; anonymous visitors get the normal login redirect; the two public legal pages are intentionally open. Covered by onboarding and platform administration tests. |
| Email failure is detected with `OutboundMessage.status != "sent"` | Done | Welcome resend and login-link screens check the returned status. Part 3 also propagates failed invitation/reset emails to the one-time credential screen and stops a failed welcome email from being counted as delivered. Covered by six Part 3 tests. |
| No breakage of existing features | Done | Slug validation is shared with the setup wizard, the setup redirect is limited to platform-provisioned admins (legacy admins sign in normally), and all pre-existing suites still pass. |
| Documentation and commits after each part | Done | `PLAN.md` and `PROGRESS.md` were updated for Parts 1 and 2 before their commits; Part 3 updates this report, `MANUAL_TESTING.md`, the plan and progress log. |

## Verification

| Run | Result |
| --- | --- |
| Part 1: `pytest`, `ruff check .`, `makemigrations --check --dry-run` | 1,498 passed; clean; no changes. |
| Part 2: same commands | 1,549 collected, 1,548 passed; the only failure was the unrelated flaky finance concurrency test, which passed in isolation; lint clean; no migration changes. |
| Part 3 final: same commands | **1,556 passed**; `ruff check .` clean; `makemigrations --check --dry-run` reports "No changes detected"; `manage.py check` shows only the expected console-email backend warning in development. |

Onboarding-specific tests: 10 (Part 1) + 25 (Part 2) + 6 (Part 3) + 27 slug/domain/redirect tests = **68**.

## Key decisions and caveats

- `InstitutionOnboarding` stores one-time milestones only. Checklist items such as logo, academic year, modules, users and plan are derived from live rows.
- "Remove administrator" means deactivate, so history survives. The last active administrator cannot be removed.
- Custom domains are never enabled automatically. A person must publish the TXT record, add the host to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, and confirm the step in the UI. No live DNS lookup is attempted.
- A changed slug keeps the old `/i/<slug>/` and `/i/<slug>/login/` addresses working for 90 days; after that they 404 and the word can be reused.
- `SignupLink.created_by_platform_admin` is a marker only. Platform-created links obey the same rules as institution-created links.
- The setup redirect is queued from the real login form, not the auth signal, and only for an administrator recorded as platform-provisioned. `force_login` and pre-existing institutions never trigger it.
- Institution admins are not given `admin.create_institutions`; the guided setup edits the existing institution only.
- Anonymous visitors to a protected `/platform/` URL are redirected to sign in, which is the normal Django behaviour; 404 applies to authenticated non-platform users.

## Manual testing

See `MANUAL_TESTING.md` for click-by-click scenarios, including how to simulate a failed email and how to verify a custom domain.
