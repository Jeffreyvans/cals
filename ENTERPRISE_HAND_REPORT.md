# Enterprise hand report — groups, sponsors, white-label and resellers

Date: 2026-11-26. Audience: platform operator and pilot administrator.

## 1. What was delivered

All three parts live in the single `enterprise` app and reuse the existing
school, finance, consent, audit, notification, branding and platform-billing
code. Nothing is enabled by default: the plan/module codes `groups`,
`sponsor_portal`, `white_label` and `reseller` must be switched on explicitly or
supplied by a current plan.

| Part | Commit | Main documentation |
| --- | --- | --- |
| 1 — Groups of schools | `14a0e7f` | `docs/GROUPS.md` |
| 2 — Sponsor portal | `8fb7f44` | `docs/SPONSORS.md` |
| 3 — White-label domains and resellers | `a45f097` | `docs/WHITE_LABEL.md`, `DEPLOY.md` section 8 |

### Part 1 in one paragraph
An `Organisation` links existing institutions as campuses. Group-only roles are
checked separately from institution roles; `/g/<slug>/` signs in to a campus
chooser and the dashboard is aggregates only. Student-level pages require a
live `DataAccessGrant` requested through the existing access-request flow.
Shared settings inherit, override and lock without rewriting school records.
Billing can be separate or consolidated (one ordinary platform invoice plus
group invoice lines) with plan volume discounts.

### Part 2 in one paragraph
A `SponsorAccount` is portal identity over the existing `finance.Sponsor` and
its sponsorships. Sharing levels 1-3 give totals, coded references or named
detail; named detail additionally needs sponsor consent and a live student
grant. Withdrawal sets level 0 and revokes grants immediately. Payments, credit
notes, statements, receipts, exports and donor reports use the existing finance
services and email codes. Sensitive actions reuse platform 2FA and a password
re-check; notices are opt-in.

### Part 3 in one paragraph
Verified custom domains are registered into runtime host and CSRF settings,
with feature checks outside the cache so a lapsed plan stops a host at once.
Sessions are bound to their host and flushed when presented to another host.
White-label mode removes only the small “Powered by” credit; legal wording,
sender address and publisher identity remain. Reseller clients come from
existing organisation memberships, and commission is one immutable row per
platform payment with an idempotent payout action.

## 2. Verification evidence

| Check | Result |
| --- | --- |
| Focused group tests (part 1) | 19 passed |
| Full suite after part 1 | 1,836 passed, 227 warnings |
| Focused sponsor tests (part 2) | 21 passed |
| Full suite after part 2 | 1,857 passed, 233 warnings |
| Focused white-label/onboarding/slugs/brand/performance tests (part 3) | 84 passed |
| Full suite after part 3 | **1,874 passed, 233 warnings in 525.95s** |
| `python -m ruff check .` | All checks passed |
| `python manage.py check` | Only expected `core.email.W001` console-email warning |
| `python manage.py makemigrations --check --dry-run` | No changes detected |

The existing 21-query staff-list guard still passes: the host refresh now runs
only for an unknown host and the session binding is written at login, so these
features add no per-request host or session write on established hosts.

## 3. Manual testing steps

Run these on a staging deployment with `DEBUG=false`. Start from a platform
administrator account, one active institution and one ordinary school staff
account. Enable the relevant plan modules before each scenario.

### 3.1 Feature gating and plan loss

1. Open **Platform → Plans** and confirm `groups`, `sponsor_portal`,
   `white_label` and `reseller` are all absent from a basic plan.
2. With only module switches (no plan), open `/g/<slug>/`, `/sponsor/` and
   `/reseller/<slug>/`; each should be a 404.
3. Switch `white_label` on for the institution and reload the detail screen:
   the “Powered by” checkbox appears. Switch it off again: the control
   disappears and attribution stays visible.
4. Put the institution on a current plan containing `white_label` and
   `reseller`. Confirm the custom-domain page and reseller area open.
5. Cancel or suspend that subscription. The verified domain must stop
   resolving on the very next request, the reseller portal must 404, and no
   redeploy or cache wait should be needed. Restore the plan afterwards.

### 3.2 Custom domain, DNS, TLS and CSRF

1. In **Platform → Institutions → <school> → Custom domain**, enter a host such
   as `learn.example.org`. Save it. The screen shows a one-time token and the
   DNS instructions. The host is not yet active.
2. At the DNS provider publish both records shown on the screen:
   * `TXT` at `_edusphere.learn.example.org` (substitute the real host) with the
     displayed token as its value;
   * `CNAME` from `learn.example.org` to the Render hostname (or the
     `ALIAS`/`ANAME` record Render recommends for an apex domain).
3. In Render open **Settings → Custom Domains**, add the same host and wait for
   **Certificate issued**. Confirm the certificate with a browser before
   continuing.
4. Add the host to the deployment environment, then redeploy:
   `ALLOWED_HOSTS=learn.example.org` and
   `CSRF_TRUSTED_ORIGINS=https://learn.example.org`. The application also
   registers verified hosts at runtime, so the environment is recommended but
   not the only path.
5. Return to the domain panel and run **Check DNS resolution**. A successful
   lookup does **not** enable the domain by itself; tick the explicit DNS/TLS
   confirmation and press **Verify and enable domain**.
6. Browse `https://learn.example.org/login/`: the page loads over HTTPS, static
   files and uploaded logos load, and a real form login succeeds. The POST
   proves the CSRF origin is trusted.
7. Open the original platform host in the same browser after signing in on the
   custom host. The session must be discarded and the person sent to sign in
   again; a stored group, sponsor or reseller selection must not follow them.
8. Press **Disable custom domain**. The record and token remain on screen, but
   the host no longer resolves an institution. Press **Remove custom domain**
   and confirm the school, its users and its money rows are untouched.
9. Try to save the same host on a second institution: it must be refused.

### 3.3 White-label branding and legal attribution

1. With `white_label` on, untick **Show “Powered by Vanpere” attribution** on
   the institution detail screen and save.
2. Open a school page, a PDF (for example a receipt) and a queued/emailed
   notification. The small platform credit is absent, but the school's own
   name, logo and colours are unchanged.
3. In the email footer and PDF small print confirm the copyright year,
   publisher/legal identity and the verified sender address are still present;
   only the credit was removed.
4. Send an **Email sign-in link** from the institution detail screen. The `From`
   address stays on the verified platform domain with the school's display
   name, even in white-label mode.
5. Tick the attribution back on and confirm the credit returns everywhere.

### 3.4 Reseller isolation and commission

1. Switch on `reseller`, create an organisation of type **Reseller**, and
   attach two client campuses to it. Create a second reseller organisation with
   a different client.
2. Create an administrator and a viewer account at
   `/platform/resellers/new/`. Sign in at `/reseller/<slug>/login/` with each.
3. On the dashboard confirm only the two attached campuses appear in the
   client list, filter selector and CSV export. Try the other reseller's slug:
   access is refused. Try an out-of-scope `?client=` value: no extra rows or
   commissions appear.
4. As platform billing, record a payment against a client's ordinary platform
   invoice at **Platform → Invoices**. Open
   `/platform/resellers/` and confirm exactly one commission row at the
   configured rate. Recording the same payment action again must not create a
   second row.
5. Confirm a payment for the other reseller's client accrues to that reseller
   only. Press **Mark paid** on the first commission; a second press leaves the
   paid timestamp unchanged.
6. Check **Platform → Audit** for `reseller.commission_accrued` and
   `reseller.commission_paid` entries with amounts but no client names, tokens
   or session data.

### 3.5 Group and sponsor smoke checks

1. Groups: create a group under `/platform/groups/new/`, attach two campuses,
   sign in at `/g/<slug>/`, switch campuses, and confirm the dashboard shows
   only aggregates. Open a student-level page: it must ask for a grant. After a
   grant is approved, withdraw it and confirm the page is refused again.
2. Sponsors: invite a sponsor at `/platform/sponsors/new/`, verify the code and
   attach a sponsorship. At sharing level 1 confirm no student identifiers;
   at level 2 confirm coded references only; at level 3 with consent and a live
   grant confirm named detail. Withdraw consent and confirm the name disappears
   immediately. Run `/platform/sponsors/report/` and its CSV: totals only.

## 4. Known limits and care points

* DNS publication, Render certificate issuance and the TXT/TLS confirmation are
  deliberately manual. The reachability check does not read TXT records and
  never enables a domain by itself.
* All four enterprise modules are off by default and close on a cancelled or
  suspended subscription. Do not expect a domain or portal to survive a plan
  downgrade.
* A reseller commission is derived from a platform payment, not a school
  payment. Delete-and-recreate flows keep the original commission row; use the
  payout action, not deletion, to close it.
* Existing limitations elsewhere in the product (live fiscalisation, real SMS
  and WhatsApp gateways, OIDC SSO) are unchanged by this work.
* `config/brand.py` remains the single visible brand source; the white-label
  switch only hides the credit and must not be used to change legal identity or
  the verified sender address.
