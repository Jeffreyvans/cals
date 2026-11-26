# Sponsor portal

The sponsor portal gives a named donor or sponsor organisation's user a narrow,
verified view of the sponsorship records that already exist in `finance`. It
does not create a second sponsor master, a second invoice ledger or a separate
login system. Every payment, scholarship discount and credit note still uses the
school's ordinary finance and ledger rows.

The `sponsor_portal` module is **off by default**. It becomes available when a
school switches it on or its current plan includes `sponsor_portal`; a
suspended, cancelled or past-due subscription closes the portal. An invited user
sees a missing page (404) rather than a permission error while the feature is
off.

## Create and verify an account

1. Platform staff with `platform.manage_billing` open
   `/platform/sponsors/`, choose **New sponsor account** and select an existing
   `finance.Sponsor`, an existing user, a role and an initial sharing level.
   Sharing above totals only needs a reason of at least 20 characters.
2. Saving the account sends the ordinary `STAFF_INVITATION` email to the user's
   email address (or the sponsor's fallback email) with the verification code.
   The code can be resent from the account detail page. The password itself is
   delivered separately; never put it in the same email as the code.
3. The user signs in at `/sponsor/` with the same username or email and password
   used elsewhere. If the user represents several sponsors, they choose which
   one to act for at `/sponsor/select/`.
4. Until verification is complete, the only reachable page is
   `/sponsor/verify/`. Entering the code from the invitation verifies the
   account and opens the dashboard. `/sponsor/logout/` clears the sponsor
   selection as well as the login session.

Platform staff can deactivate a sponsor account without deleting it, resend a
verification code, or attach an existing student sponsorship from the account
detail page. Deleting an account is deliberately not offered because it would
hide audit history.

## Roles

| Role | Can do |
| --- | --- |
| Sponsor administrator | everything below, including payments and credit notes |
| Sponsor finance officer | dashboard, invoices, statements, exports, payments and credit notes |
| Sponsor viewer | dashboard, invoices, statements, receipts and exports only |

A viewer cannot post money or change sharing level above what is already
agreed. Every dashboard and statement remains scoped to that account's
sponsor and school, even when a user has several sponsor accounts.

## Sharing levels and consent

Sharing is per sponsor account, not a global platform setting:

| Level | What the sponsor can see |
| --- | --- |
| 0 — withdrawn | no coded or named identifiers; statement rows show a generic `Student` label |
| 1 — totals only | commitments and statement amounts; no student-level identifier |
| 2 — coded student references | `Student <id>` instead of a name |
| 3 — named student detail | a student name only while a live consent grant also allows it |

Level 3 needs both the sponsor's explicit consent and an active, in-date
`DataAccessGrant` covering `personal_data` and `finance`. The grant is the same
time-limited access grant used by the platform admin area. If it expires or is
revoked, names silently drop back to coded references; there is no fallback
that reveals a name from a stale grant. Raising a level above the current one
needs a reason of at least 20 characters, and moving to level 3 also needs the
sensitive-action checks below.

**Withdraw sharing** at `/sponsor/settings/` takes effect immediately. It sets
the level to 0, clears the consent evidence and revokes any live grant held by
that user for the sponsor's school. It also marks the linked `AccessRequest`
rows as revoked, so the ordinary "Who accessed our data" view reflects the
withdrawal. Optional email and in-app notices are sent only if that sponsor
account opted in.

## Statements, receipts and exports

- `/sponsor/invoices/` lists invoices attached to active sponsorships and the
  maximum each sponsorship can cover.
- `/sponsor/statement/` lists the sponsor's existing scholarships (`Discount`),
  payments (`Payment`) and credit notes (`Waiver`) for that school, with an
  inclusive `start` and `end` date filter. Rows from another institution are
  never included, even if the payer has the same name.
- `/sponsor/receipts/` lists only payments from that statement. The PDF at
  `/sponsor/receipts/<payment_id>/pdf/` is redacted to coded references unless
  level 3 and a live grant are both in force. A payment outside the sponsor's
  statement returns 404.
- `/sponsor/export/` writes the statement as CSV, XLSX or PDF using the same
  sharing-safe student label as the screen.

Issued finance documents are never edited or deleted from the portal.

## Payments and credit notes

Only sponsor administrators and finance officers can use the POST actions at
`/sponsor/pay/` and `/sponsor/credit-note/`. Both first require:

- two-factor authentication set up at `/sponsor/security/` (a TOTP code must be
  confirmed when 2FA is enabled), and
- a password re-check within the last 15 minutes.

Payments go through `finance.services.receive_payment()` and are capped by both
the active sponsorship and the invoice balance. Excess paid amount is held as
credit by the existing finance service, not written into a new ledger. Credit
notes are ordinary immutable `finance.Waiver` rows and post through the
existing save hooks. Every action is written to the school audit and platform
access log with harmless detail only: no tokens, student names or request
bodies.

The platform-level scholarship and donor report at
`/platform/sponsors/report/` is institution-scoped, has the same date filters
and can be exported as CSV. It contains sponsor totals only, never student
names.

## Verification

The focused suite is `python -m pytest enterprise/tests/test_sponsors.py -q`.
After Part 2 it passed with **21 tests**; `ruff check .` was clean and
`makemigrations --check --dry-run` reported no changes. The whole suite was run
again before the Part 2 commit.
