# Groups of schools

A group is an organisation (a trust, church, company, government body or
reseller) that contains existing Vanpere institutions as campuses. It is an
additional view over the schools, not a copy or a migration: detaching a campus
removes only the link. Students, invoices, documents, logs and settings inside
the campus are untouched.

The `groups` module is **off by default**. It becomes available only when an
institution switches it on or its current plan includes `groups`; a suspended,
cancelled or past-due subscription closes it again.

## Set up a group

1. Platform staff with `platform.manage_institutions` open
   `/platform/groups/`, choose **New organisation** and enter the name, slug,
   type and contact details. The slug becomes part of the branded sign-in URL.
2. On the organisation page, attach each existing school as a campus and give
   it a campus label and sort order. A school can belong to only one group;
   a second attachment is refused instead of silently moving data.
3. Assign group roles to named users. Group roles are separate from school
   roles:

   | Role | Can do |
   | --- | --- |
   | Group administrator | everything below, including settings and issuing a consolidated invoice |
   | Group finance officer | dashboard, exports, student-level access requests and invoices |
   | Group academic officer | dashboard, exports and student-level access requests |
   | Group auditor | dashboard and exports only; all write actions are refused |

4. Enable `groups` on at least one attached campus (or include it in the
   plan). Group sign-in and the group screens then become reachable at
   `/g/<slug>/`.
5. Choose **separate** or **consolidated** billing. With separate billing the
   platform continues to invoice each school exactly as before. With
   consolidated billing, `/g/<slug>/billing/` creates one standard
   `PlatformInvoice` plus a `GroupInvoice` with one line per campus. The
   platform admin area refuses a separate platform invoice for a campus that
   is already on a consolidated group invoice.

Detaching a campus never deletes it. A campus override kept from one group is
ignored if the campus later belongs to another group, so old choices cannot
leak across organisations.

## Branded sign-in and campus switching

`/g/<slug>/` shows the organisation's logo, colours and contact details and
asks for the group user's username or email, password and one of the group's
campuses. The person must hold an active role in that organisation and choose
a campus that is actually attached to it; an ordinary school login from
someone outside the group is rejected with the usual generic sign-in error.
The selected campus is stored in the session and scopes the tenant for the
group screens.

The dashboard includes a campus switcher. Switching campus is a session
change inside the same signed-in group session — there is no second password
prompt and no external identity provider is configured. "Next" URLs are
checked against the current host before they are followed.

## Dashboard, filters and exports

The dashboard at `/g/<slug>/dashboard/` is **aggregates only**. It compares
enrolment, fees collected against target, attendance, published pass rate,
staff count and subscription status per campus, with a total row. No student
name, individual mark, fee row or health detail is queried for this screen.
Alerts are derived from the same figures (fees below 80% of target,
attendance below 80%, overdue subscriptions); refreshing the dashboard updates
an existing alert instead of recreating it, so its history is preserved.

Every screen uses the same filter helper. Campus, term, school type and a date
range can be combined; an invalid term id or date is ignored rather than
filtering everything out. The campus chooser always lists all campuses, but
the rows, totals, alerts and the Excel/CSV/PDF export all use the filtered
set. Exports carry a filename based on the group slug and date.

## Student-level access

Group roles never unlock student records by themselves. To open
`/g/<slug>/students/`, an administrator, finance officer or academic officer
first uses **Request student-level access** and states a reason of at least 20
characters, the categories needed (`personal_data`, `finance` or `results`)
and a duration (at most the platform limit, 168 hours by default). The campus
administrator approves, limits or refuses it through the existing institution
consent screens. The approval creates the ordinary time-limited
`DataAccessGrant`, so expiry, idle-session checks, immediate withdrawal and
the "Who accessed our data" page are the platform's existing ones. The group
view checks the grant again on every request and records the access in the
platform audit log. A pending, expired, revoked or wrong-category grant gives
`GRANT-REQUIRED` and no student page.

## Shared settings and inheritance

`/g/<slug>/settings/` manages eight shared defaults: grading scale, fee item
templates, academic calendar, terminology, report card layout, policies,
document templates and roles. Each value is a JSON object and the form
validates it before saving. The preview lists which campuses would change if
the default were saved.

- A normal default is inherited by campuses that have no override.
- A campus can save its own override at
  `/g/<slug>/campus/<id>/settings/`; a group administrator or that campus's
  own administrator may do so.
- A locked default always wins and prevents a campus override.
- Saving or changing a default never rewrites an institution record or an
  issued document. Values are resolved when a screen reads them.

## Billing details

`GroupBilling.discount_rate` may be left empty to use the plan's volume
discount (starter 0%, standard 5%, college 10%, plus 5% at five campuses and
another 5% at ten). An explicit `0` is a real zero discount. Discounts are
rounded per campus line before the invoice total is calculated, so the line
net amounts always add up to the total. Only a group administrator may issue
an invoice.

## Verification

The focused suite is `python -m pytest enterprise/tests/test_groups.py -q`.
After Part 1 the whole suite passed with **1,836 tests**; `ruff check .` was
clean and `makemigrations --check --dry-run` reported no changes.
