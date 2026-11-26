# White-label, custom domains and resellers

This document describes the optional enterprise layer that lets a school use its
own host name and branding, and lets a reseller manage several client campuses.
It adds no new money ledger: reseller commission is derived from existing
`platform_admin.PlatformPayment` rows, and every institution, student, invoice
and document remains in its original tenant.

All four feature codes are off by default:

| Code | Name | What it opens |
| --- | --- | --- |
| `groups` | Groups of schools | See [GROUPS.md](GROUPS.md) |
| `sponsor_portal` | Sponsor and donor portal | See [SPONSORS.md](SPONSORS.md) |
| `white_label` | White-label and custom domains | This document, sections 1-4 |
| `reseller` | Reseller accounts | This document, section 5 |

A feature comes from the institution's module switches or the current plan's
module list. A cancelled, suspended or expired subscription switches it off
again; `past_due` stays on during the normal grace period. When a feature is
off, its portal routes return 404 and its settings cannot be changed. Nothing
is deleted and no institution data is rewritten.

---

## 1. Give an institution a custom domain

Do this from **Platform dashboard → Institutions → <school> → Custom domain**
(`/platform/institutions/<pk>/domain/`). The button is on the institution detail
screen, but the page itself returns 404 unless `white_label` is enabled for that
institution.

1. Enter the full host name, for example `learn.example.org`, without
   `https://`. The saved value is lower-cased, trailing dots are removed and a
   duplicate host is refused. Changing the host clears verification and disables
   it again.
2. Publish the two DNS records shown on the screen:
   * a `TXT` record at `_edusphere.learn.example.org` (using your own host in
     place of the example) whose value is the one-time token printed in
     **Verification steps**; and
   * a `CNAME` (or the `ALIAS`/`ANAME` record recommended by the host) from the
     host name to the Vanpere service.
3. In Render, open **Settings → Custom Domains**, add the same host and wait for
   **Certificate issued**. HTTPS, secure cookies, proxy header handling, SSL
   redirect and HSTS are enabled when `DEBUG=false`; see [DEPLOY.md](../DEPLOY.md#8-add-a-custom-domain).
4. Add the host to the deployment environment (recommended):

   ```text
   ALLOWED_HOSTS=learn.example.org
   CSRF_TRUSTED_ORIGINS=https://learn.example.org
   ```

   Redeploy after changing these. The application also registers verified,
   enabled domains at runtime, so a database-only change still works without a
   redeploy; the environment remains the source of truth.
5. Click **Check DNS resolution**. This is a reachability check only: it proves
   the host has an A/AAAA/CNAME answer, not that the TXT token is correct. Confirm
   the TXT value and the certificate yourself, then tick the confirmation box and
   click **Verify and enable domain**. Until that confirmation, the host is
   ignored by the application.
6. Open `https://<domain>/i/<school-slug>/login/`, sign in with an account of
   that institution, and check that styles, logos and uploads load and that a
   POST (for example a settings save) does not fail CSRF.

The standard `/i/<slug>/login/` link keeps working. Disabling a domain keeps its
record, token and verification date but stops host mapping; **Remove custom
domain** clears those fields without deleting any school data. The DNS records
at the provider are not removed automatically.

## 2. Host resolution, host settings and sessions

`core.middleware.CustomDomainHostMiddleware` runs before Django's security
middleware and reconciles `settings.ALLOWED_HOSTS` and
`settings.CSRF_TRUSTED_ORIGINS` with the domains that are verified, enabled,
attached to an active institution and still covered by `white_label`. It never
removes hosts that were configured by the environment. Only `https://` origins
are trusted outside debug mode. A failed database lookup is logged and the
static environment list still applies.

`core.middleware.InstitutionMiddleware` resolves the tenant in this order:

1. the signed-in user's own institution;
2. a verified custom-domain host;
3. an institution subdomain;
4. an `/i/<slug>/` path prefix.

A signed-in user therefore wins over host mapping. `core.middleware.HostSessionIsolationMiddleware`
binds an authenticated session to the host where it was made; if that cookie is
presented to another host, the session is flushed and group, sponsor and
reseller selectors are cleared. Responses carry `Vary: Host` so a cached page
cannot be reused for another tenant.

Domain and host caches are cleared when an institution, a `white_label` module
switch, a plan or a subscription changes. The host list otherwise refreshes at
most every five minutes. If a plan lapses, the host stops mapping on the next
request even if the cached institution row is still present, because the feature
check is made outside that cache.

## 3. White-label branding

Platform staff may switch off **Show "Powered by Vanpere" attribution** on the
institution detail screen. This is only available while `white_label` is
enabled; if the feature is off or lost, a posted value cannot change the flag and
the attribution stays visible.

For a white-label institution:

* its own name, logo and colours continue to appear in the app, public pages,
  PDFs, receipts and the email header;
* institution emails say "Sent by <school>." instead of "Sent by <school> via
  Vanpere.", and the email title uses the school's name;
* the small "Powered by" link is suppressed on institution pages and documents.

The platform copyright line, legal/controller-processor wording, the verified
sending address and the publisher's legal name are **not** removed. White-label
changes branding, not legal responsibility or the email delivery domain.

## 4. Graceful loss of the feature

If `white_label` is switched off or the subscription is no longer current:

* the custom-domain admin page returns 404 and the branding checkbox is hidden;
* the attribution flag cannot be changed and remains visible;
* verified custom-domain hosts are no longer mapped or added to
  `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`, so links should use `/i/<slug>/login/`;
* the stored domain, token and branding choices stay in the database and are
  restored when the feature is re-enabled; check DNS and TLS again before use.

## 5. Reseller accounts

A reseller is an existing `Organisation` of type **Reseller** with existing
`OrganisationMembership` links to its client institutions. No student, money or
document record is copied.

1. Create the organisation at **Platform → Groups → New organisation**, choose
   type **Reseller** and attach each client campus. Reserved top-level slugs
   such as `g`, `sponsor`, `r` and `reseller` cannot be used.
2. Enable `reseller` on at least one attached campus (through its module switch
   or plan). If no client campus has the feature, the portal returns 404.
3. At `/platform/resellers/new/`, link an existing user, choose
   **Reseller administrator** or **Reseller viewer (read only)**, and set a
   commission rate between 0 and 100 per cent. Creating an account when no
   client has the feature leaves it inactive.
4. The user signs in at `/reseller/<organisation-slug>/login/`. The dashboard
   at `/reseller/<organisation-slug>/` lists client campuses with enrolment,
   collected money and subscription status, plus accrued and paid commission.
   The optional `?client=<institution-id>` filter and the CSV export use the
   same figures. There are no student names or student-level pages.
5. When platform staff record a payment on a client's ordinary platform
   invoice, one `ResellerCommission` row is accrued from that payment and the
   account's rate. Recording the same payment twice cannot accrue twice.
   **Platform → Resellers** lists the rows; an administrator can mark an
   accrued commission as paid, which is audited.

Reseller accounts are scoped to one organisation: a user cannot open another
reseller's portal, and the client list comes only from that organisation's
memberships. A lapsed client plan drops that campus from the portal while
keeping the links and commission history.

## 6. Operational limits

* TXT verification and TLS issuance are checked by a person, not by an automatic
  DNS lookup; the built-in DNS check is reachability only.
* One custom domain maps to one active institution. Wildcard hosts and multiple
  domains per institution are not supported.
* A host-bound session is not a substitute for an institution-scoped login: use
  `/i/<slug>/login/` for school staff and the group, sponsor or reseller portals
  for their own roles.
* Audit entries record domain and commission changes, never DNS tokens or
  secrets.
