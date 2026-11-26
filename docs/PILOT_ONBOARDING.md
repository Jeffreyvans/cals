# Pilot onboarding checklist

Use this checklist for every pilot institution. Each step has an owner and a
definition of done. Do not skip a step just because the screen appears to work.
The day-by-day sequence is in [PILOT_PLAN.md](../PILOT_PLAN.md), and the
school-facing caveats are in [PILOT_LIMITATIONS.md](../PILOT_LIMITATIONS.md).

The platform-side workflow starts at **Platform dashboard → Institutions → New
institution** (`/platform/institutions/new/`). The in-app role guides are in
[ROLE_GUIDES.md](ROLE_GUIDES.md).

---

## 1. Before the school is created — platform admin

- [ ] Application reviewed and approved (or manual institution record created).
- [ ] Plan and enabled modules chosen with the school's named contact.
- [ ] Institution slug, name, country and currency confirmed — **the slug cannot be changed later without a data migration**.
- [ ] Support phone and email set in `config/brand.py` (or environment) and visible on the error page.
- [ ] Definition of done: the institution appears in `/platform/institutions/`.

## 2. First administrator account — platform admin

- [ ] Administrator created with the `admin` role and a temporary password.
- [ ] Temporary password delivered over a separate channel (phone call or in person), never in the same email as the login link.
- [ ] Administrator has signed in, changed the password and enrolled in 2FA.
- [ ] Definition of done: `/platform/security/` shows 2FA enabled and no default-password warning.

## 3. Institution basics — school admin

- [ ] Institution details, logo, colours and contact address saved.
- [ ] Current academic year and at least one term created.
- [ ] Only the agreed modules enabled.
- [ ] Student and staff numbering formats confirmed (they are hard to change after records exist).
- [ ] Definition of done: `/settings/institution/` and `/academics/years/` show the correct values.

## 4. Roles and staff — school admin

- [ ] Named people assigned to registrar, bursar and teacher roles.
- [ ] Each person can open their own workspace and cannot open a colleague's restricted screen.
- [ ] Any health, counselling or data-access approver roles assigned explicitly.
- [ ] Definition of done: a spot-check with two different accounts shows different menus.

## 5. Data import — school admin + registrar

- [ ] Templates downloaded from `/imports/new/` and the messy samples reviewed: `core/samples/pilot_students_messy.csv` (2 ready, 1 problem), `core/samples/pilot_fee_structures_messy.csv` (2 good lines, 1 bad amount) and `core/samples/messy_students.csv` (mapping practice). Expected results are in [PILOT_PLAN.md](../PILOT_PLAN.md#2-sample-import-files-with-messy-data).
- [ ] One small CSV (5–10 rows) imported first, preview checked, then undone.
- [ ] Full import run; duplicate and invalid rows read from the error report and the result CSVs kept with the pilot ticket.
- [ ] Student logins are marked as needing a reset before anyone uses them.
- [ ] Definition of done: student count matches the source file minus documented rejects.

## 6. Finance configuration — bursar

- [ ] Currency matches the school's bank account.
- [ ] Fee structures and instalment plans entered with at least one hand-calculated test invoice.
- [ ] Tax category and rate verified against a paper calculation.
- [ ] Payment gateway credentials (if used) are in live mode with a real callback URL.
- [ ] Definition of done: a test payment and a duplicate webhook produce exactly one payment row.

## 6a. School group setup — platform admin + group admin

- [ ] Complete this section only when the school belongs to a group. Enable `groups` for at least one campus (or include it in the plan).
- [ ] Organisation created at `/platform/groups/new/` with a stable slug, type and contact details; each campus attached with the agreed label.
- [ ] Named users assigned `group_admin`, `group_finance`, `group_academic` or `group_auditor`; the auditor has been checked and cannot write.
- [ ] Billing mode chosen: separate campus invoices, or consolidated with the group discount and one line per campus.
- [ ] Branded sign-in at `/g/<slug>/` tested with a real group user and campus switcher; shared defaults and locked settings previewed before saving.
- [ ] Student-level access request approved by a campus admin through the existing consent screen; the grant expiry and withdrawal path tested.
- [ ] Definition of done: dashboard shows aggregates only, with no student names, and the export matches the selected filters.

## 6b. Sponsor portal setup — platform admin + sponsor user

- [ ] Complete this section only when the school uses the sponsor portal. Enable `sponsor_portal` for the school (or include it in the plan).
- [ ] One real `finance.Sponsor` gives one named user an account at `/platform/sponsors/new/`; role and initial sharing level agreed in writing.
- [ ] Invitation delivered by email with the verification code; password delivered separately; the user verifies at `/sponsor/verify/`.
- [ ] Sharing level tested: totals, coded references and named detail with a live grant. Named detail disappears after withdrawal or grant expiry.
- [ ] Sponsor administrator enables 2FA at `/sponsor/security/`, confirms a TOTP code, re-enters their password and records one test payment and one credit note in the existing ledger.
- [ ] Statement, redacted receipt PDF and CSV/XLSX/PDF exports checked; no rows from another school appear.
- [ ] Notification choices confirmed opt-in; audit and access logs contain no verification tokens or student names.
- [ ] Definition of done: a sponsor viewer can read only, and the donor report at `/platform/sponsors/report/` contains totals but no student names.

## 6c. White-label and reseller setup — platform admin

- [ ] Complete this section only when the plan includes `white_label` (and
      `reseller` if a reseller is involved). Both are off by default.
- [ ] Custom domain: enable `white_label`, open
      `/platform/institutions/<pk>/domain/`, save the host, publish the
      `_edusphere.learn.example.org` TXT token (substitute your own host), plus
      the CNAME/ALIAS record, and wait for Render to report a valid certificate.
- [ ] Confirm `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` contain the host (the
      app also registers verified domains at runtime), run **Check DNS
      resolution**, confirm the TXT value and TLS yourself, then verify and
      enable. Test sign-in, a POST, styles and uploads on the new host.
- [ ] Check the "Powered by" switch on the institution detail screen: it is
      available only with the feature on, and the copyright and legal wording
      stay visible either way. Test that disabling/removing the domain keeps
      `/i/<slug>/login/` working and deletes no school data.
- [ ] Reseller (if used): create an organisation of type **Reseller**, attach
      only its own client campuses, enable `reseller` on a client, and create a
      named account with a role and commission rate at `/platform/resellers/new/`.
- [ ] Sign in at `/reseller/<slug>/login/`; check the client list, the `?client=`
      filter and CSV export contain aggregates only. Record one test platform
      payment, confirm one commission row and mark it paid; check another
      reseller's portal is refused.
- [ ] Definition of done: the branded host works with HTTPS and CSRF, attribution
      and legal text behave as documented, and reseller data is isolated to its
      own campuses with no student names.

## 7. Tenant isolation spot-check — platform admin

- [ ] Create a second "dummy" institution with one student.
- [ ] Log in as a real school admin and try the dummy student's ID on a student detail page and API route.
- [ ] Confirm no dummy data appears and the request is rejected or returns local data only.
- [ ] Definition of done: no cross-tenant marker is visible; delete the dummy institution afterwards.

## 8. Notifications and help — school admin

- [ ] One test email sent to a real mailbox and delivered (not console backend).
- [ ] `ERROR_CODES.md`, `/help/` and `PILOT_LIMITATIONS.md` linked from the school's staff onboarding material.
- [ ] Support contacts on the error page confirmed correct.
- [ ] **Pilot feedback** button tested from a normal page and from an error page; the platform team can find the item in `/platform/inbox/` with the page and any `ERR-XXXXXX` reference.
- [ ] Staff told not to put names, student numbers or other personal data in feedback, and that the button is not an emergency channel (five reports per user per hour).
- [ ] Definition of done: a staff member can find the error reference, support contact and feedback button without help.

## 9. Go-live gate — platform admin

- [ ] `/status/` reports no *Outage*.
- [ ] Full test suite green on the deployed commit (see `PRODUCTION_READINESS.md` for the recorded run).
- [ ] Backup command run and a restore **dry-run** completed on staging.
- [ ] Rollback owner and contact number written down.
- [ ] Payment, report and support-request measures in [PILOT_PLAN.md](../PILOT_PLAN.md#5-success-measures) recorded with evidence.
- [ ] Definition of done: named person signs off in the pilot ticket.

---

## Post-pilot review (first 7 days)

- [ ] Platform **Errors** page reviewed daily; repeated codes turned into fixes or docs.
- [ ] Platform **Inbox** pilot feedback acknowledged within one business day; page, error reference and sender recorded.
- [ ] Slow-query log (`edusphere.slow`) reviewed for tenant-scoped pages.
- [ ] Email health checked for bounces and suppressions.
- [ ] Any money correction traced to a ledger entry and an audit log record.
- [ ] Success measures from `PILOT_PLAN.md` reviewed with the school; unresolved limitations accepted in writing or assigned an owner.
