# Vanpere role guides

This document is the written companion to the in-app **Help and guides** page
(`/help/`). The in-app page picks the right guide from the signed-in user's role;
this file keeps the full text in one place for training and onboarding.

> **Rule of thumb:** if a menu item is missing, your role does not hold that
> permission. Ask an administrator rather than trying to work around it.

## Pilot feedback (all signed-in roles)

The **Pilot feedback** button is in the top bar of every signed-in page. Use it
to tell the platform team about a broken screen, confusing wording or an error
you cannot resolve. The form asks for a short message and records the page you
were on. If an error page showed a reference such as `ERR-A1B2C3`, the button
carries it across automatically.

- Do not include names, student numbers, health details or other personal information.
- This is not an emergency channel. For urgent help, use the phone/email on the error page or contact the school office.
- Five reports per user per hour are accepted; a sixth is refused with a clear message.
- Platform staff see the report in `/platform/inbox/` and receive a notification.

See [PILOT_PLAN.md](../PILOT_PLAN.md) for the pilot scope and
[PILOT_LIMITATIONS.md](../PILOT_LIMITATIONS.md) for what is not covered.

---

## Platform administrator

Platform staff manage institutions, plans and platform-wide operations. They
never see institution student, health or counselling records unless a consent
grant explicitly allows it.

**Common tasks**

| Task | Screen |
| --- | --- |
| Review new institution applications | `/platform/applications/` |
| Create and configure an institution | `/platform/institutions/` |
| Manage plans and subscriptions | `/platform/plans/` |
| Record platform payments | `/platform/invoices/` |
| Review consent requests | `/platform/access/` |
| Check errors and background jobs | `/platform/errors/`, `/platform/jobs/` |
| Review the platform audit log | `/platform/audit/` |
| Review pilot feedback from the school | `/platform/inbox/` |
| Keep your account secure | `/platform/security/` |

**Good to know**

- Use a staging institution for training before onboarding a real school.
- Check `/status/` before diagnosing reported outages.
- Acknowledge pilot feedback in `/platform/inbox/` within one business day; the page and error reference are metadata, not a way to look up another person's data.
- Every action is written to the tamper-evident platform audit log.

---

## Institution administrator

Institution administrators configure the school, manage users and roles, and
control which modules are enabled. They are the first point of contact for data
access requests.

**Common tasks**

| Task | Screen |
| --- | --- |
| Set up the institution and branding | `/settings/institution/` |
| Add the current academic year and term | `/academics/years/` |
| Manage users and roles | `/accounts/users/` |
| Review registration requests | `/accounts/registration-requests/` |
| Configure numbering and grade bands | `/settings/numbering/` |
| Review data access requests | `/platform/consent/` |
| Read the audit trail | `/audit/` |

**Good to know**

- Set the current year and term before registering students or recording results.
- Bulk import is the fastest way to add students; dry-run with a small file first.
- Enable only the modules the school will actually use.

---

## Registrar

Registrars own student records, admissions, registration, timetables and results.

**Common tasks**

| Task | Screen |
| --- | --- |
| Find a student | `/academics/students/` |
| Register a student | `/academics/students/new/` |
| Bulk import students | `/imports/new/` |
| Review applications and offers | `/admissions/applications/` |
| Manage years, terms and classes | `/academics/years/` |
| Build the timetable | `/academics/timetable/` |
| Post notices | `/academics/notices/` |

**Good to know**

- Check duplicate student numbers before approving an import.
- Student numbers are normalised for comparison but stored exactly as entered.
- Results become visible to students and guardians only once published.

---

## Bursar / Accounts

Bursars and accountants manage fee structures, invoices, payments, refunds and
financial reports. Every money movement is written to the ledger.

**Common tasks**

| Task | Screen |
| --- | --- |
| Review debtors | `/finance/reports/debtors/` |
| Review collections | `/finance/reports/collections/` |
| Create fee structures | `/finance/fee-structures/` |
| Generate invoices | `/finance/generate-invoices/` |
| Reconcile the bank | `/finance/bank/` |
| Review refunds | `/finance/refunds/` |
| Read the trial balance | `/finance/reports/trial-balance/` |

**Good to know**

- Always record the payment reference; it is the audit link to the bank statement.
- Refunds cannot exceed the original payment, including pending requests.
- Use the money log and audit trail to investigate a disputed balance.

---

## Teacher / Lecturer

Teachers manage their courses, assignments, attendance and marks. They only see
the students they teach.

**Common tasks**

| Task | Screen |
| --- | --- |
| Open my courses | `/academics/lecturer/` |
| View my timetable | `/academics/my-timetable/` |
| Set assignments | `/academics/lecturer/assignments/` |
| Browse learning modules | `/lms/` |
| Post notices | `/academics/notices/` |
| View teacher-safe health alerts | `/health/safe-alerts/` |

**Good to know**

- Marks are private to the student and their guardians.
- Health alerts are deliberately limited; full records require a separate grant.
- If a student is missing from your course, ask the registrar to check enrolment.

---

## Student

The student workspace shows registration, courses, results and fees. Nobody else
can see personal or health information.

**Common tasks**

| Task | Screen |
| --- | --- |
| Open my account | `/academics/me/` |
| Register for courses | `/academics/me/registration/` |
| Open learning materials | `/lms/` |
| Change my password | `/accounts/password-change/` |

**Good to know**

- Check your profile details at the start of each term.
- Never share your login or one-time codes with anyone.
- If a fee or result looks wrong, contact the school office with the reference shown on screen.

---

## Parent / Guardian

Parents see their children's academic progress and fees. Access is limited to
learners explicitly linked to the account.

**Common tasks**

| Task | Screen |
| --- | --- |
| View my children | `/academics/my-children/` |
| Open learning materials | `/lms/` |
| Change my password | `/accounts/password-change/` |

**Good to know**

- Only children linked to your account appear in the portal.
- You cannot see another learner's records, even if you know their student number.
- Contact the school office to update a guardian link.

---

## Other staff

A staff member's workspace is shaped by the permissions attached to their role.

**Common tasks**

| Task | Screen |
| --- | --- |
| Open the overview | `/academics/dashboard/` |
| Browse school services | `/services/` |
| Open my department | `/department/` |
| Search the school | `/search/` |

**Good to know**

- If a menu item is missing, ask an administrator about your role permissions.
- Report a suspicious message or login immediately to the school office.
- `/status/` shows whether a service is actually available.
