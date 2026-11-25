# Vanpere one-school pilot plan

**Feature freeze:** 2026-11-25. No new feature is part of this pilot. Only
pilot-readiness work, fixes, documentation and verification are in scope.

This is the working plan for one school. It defines what we will actually run,
how the first two weeks go, what each role checks, and how we decide the pilot
was successful. Read it with:

- [PILOT_LIMITATIONS.md](PILOT_LIMITATIONS.md) — what we can honestly tell the school.
- [docs/PILOT_ONBOARDING.md](docs/PILOT_ONBOARDING.md) — the owner-by-owner go-live checklist.
- [docs/ROLE_GUIDES.md](docs/ROLE_GUIDES.md) — the role guides shown in `/help/`.
- [MANUAL_TESTING.md](MANUAL_TESTING.md) — click-by-click checks, including feedback.
- [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) — the wider audit and remaining risks.

---

## 1. Exact pilot feature set

### Frozen in scope

Only the following journey is accepted for this pilot. It is the same journey
covered by `core/tests/test_pilot_scenario.py`, which must pass on the deployed
commit.

| Step | Area | What the pilot uses | Owner |
| --- | --- | --- | --- |
| 1 | Platform setup | Create one institution; confirm slug, name, country/currency and contact details. | Platform admin |
| 2 | First administrator | Create the first school admin with a temporary password or invitation; hand the secret over on a separate channel. | Platform admin |
| 3 | First sign-in | Real login, forced password change, five-step setup: password, logo, institution details, year/term, sign-in link. | School admin |
| 4 | Structure | School mode, one grade level, one class, one programme, one subject with a named lecturer. | School admin |
| 5 | People | Registrar, bursar and lecturer accounts with explicit roles. | School admin |
| 6 | Student import | Four-step CSV/XLSX import (`/imports/new/`): upload, map, preview, run, read the row report. | School admin + registrar |
| 7 | Enrolment | Enrol the imported students in the pilot subject for the current term. | Registrar |
| 8 | Fees and money | Fee structure and item, hand-checked invoice generation, one cash payment, receipt screen and receipt PDF, public receipt verification. | Bursar |
| 9 | Teaching | Subject roster, attendance, marks and submitted result sheet. | Lecturer |
| 10 | Results | Registrar moderates, approves and releases the sheet; report card screen and PDF; public document verification. | Registrar |
| 11 | Help and feedback | “Pilot feedback” in the top bar, error references on error pages, `/help/`, `/status/`, and feedback visible in `/platform/inbox/`. | Everyone |

The only modules enabled for the pilot institution are **Core academics** and
**Finance and fees**. Every other switch in `core/modules.py` stays off unless a
written change is approved after the pilot. In particular, admissions, LMS,
exams, library, health, counselling, payroll, procurement, API, SSO and
fiscalisation are **not** part of this pilot.

Student and guardian portals are not acceptance gates for this pilot. If the
school asks for them, they may be demonstrated, but the pilot sign-off is based
on the ten steps above.

### Explicitly out of scope

- New features, new modules and configuration changes not listed above.
- Live Paynow payments, live callbacks and ZIMRA FDMS fiscalisation.
- Automatic platform billing, dunning, suspension or credit notes.
- SMS and WhatsApp delivery; the providers are not configured.
- Background worker, Celery beat, cron jobs and automatic queue draining.
- Data subject access request export/erasure workflow.
- Custom domains, SSO, API integrations and multi-school reporting.
- Production-shaped load testing, Sentry alerting and external uptime monitoring.

### Acceptance path

The exact acceptance path is one command:

```bash
python -m pytest core/tests/test_pilot_scenario.py -q
python -m pytest platform_admin/tests/test_pilot_feedback.py -q
```

`test_complete_pilot_journey` creates the institution, first admin, academic
structure, students from the messy import, fees, payment, attendance, results,
report card PDF and receipt, and fails on any unexpected response, missing PDF
or wrong value. If it fails, the pilot is not ready.

---

## 2. Sample import files with messy data

Use these files in staging during onboarding. They are deliberately messy; the
point is to prove that a bad row is reported without corrupting good rows.

| File | Use it for | Deliberate mess | Expected result |
| --- | --- | --- | --- |
| [`core/samples/pilot_students_messy.csv`](core/samples/pilot_students_messy.csv) | The exact student import in the end-to-end scenario. | Duplicate `First Name` header, blank header column, stray notes column, one invalid email. | Preview shows **2 ready** and **1 with a problem**. `PIL-001` and `PIL-002` are created; `PIL-003` is rejected. Map `first_name` to the first `First Name`, not `First Name (2)`. |
| [`core/samples/pilot_fee_structures_messy.csv`](core/samples/pilot_fee_structures_messy.csv) | Practising fee-structure import before invoicing. | Duplicate `Fee Structure Name` header, blank header column, repeated structure code, one non-numeric amount. | Two valid lines (`Tuition 500.00`, `Levy 50.00`) join structure `PILOT-2026` for a total of `550.00`; the `Bad amount` line is rejected. An academic year named `2026` must exist first. |
| [`core/samples/messy_students.csv`](core/samples/messy_students.csv) | A smaller column-mapping exercise for new staff. | Duplicate header, blank cell in the header row, stray notes column. | Use it only to practise mapping; it does not contain student numbers or programme details for a full run. |

How to use a sample:

1. Open `/imports/new/` and choose the import kind.
2. Upload the sample. No write happens yet.
3. On the mapping screen, check every field. A duplicated header is shown with
   a suffix such as `First Name (2)`; do not choose it for the normal field.
4. Open the preview. Read the ready count, the problem count and the row report.
5. Fix the source data, not the stored values by hand. Re-upload and preview again.
6. Run only after the school owner signs off the preview. Download both result
   CSVs and keep them with the pilot record.
7. For a real first import, run a 5–10 row file, check it and undo it before the
   full file. Undo is a manual step; see `PILOT_LIMITATIONS.md`.

The file limit is 5 MB and 5,000 rows. A `.txt` renamed to `.csv`, a file over
the limit and a repeated student number are all rejected in the preview.

---

## 3. First two weeks, day by day

Every day ends with a named person recording the evidence in the pilot ticket.
A missed exit criterion stops the next step until it is fixed or formally
accepted in writing.

| Day | Owner | Work | Exit evidence |
| --- | --- | --- | --- |
| 1 | Platform admin | Confirm the deployment settings (`DEBUG=false`, real `SECRET_KEY`, `ALLOWED_HOSTS`, database, email, object storage, support contacts) and read `PILOT_LIMITATIONS.md`. Create the institution with only Core academics and Finance enabled. | Institution appears in `/platform/institutions/`; `/status/` has no *Outage*; settings decisions recorded. |
| 2 | Platform admin + school admin | Create the first admin, deliver the temporary password on a separate channel, sign in through the real login page, change the password and complete the five-step setup. Platform admin enables 2FA on their own account. | `/platform/security/` shows platform 2FA; school admin reaches `/dashboard/` with no setup redirect. |
| 3 | School admin | Save branding, contact address, current academic year, current term, numbering formats and module switches. Assign registrar, bursar and lecturer roles. | `/settings/institution/`, `/academics/years/` and `/accounts/users/` show the agreed values; two accounts see different menus. |
| 4 | School admin + lecturer | Set school mode; add grade level `Form 2`, class `Form 2`, programme `Pilot Diploma` and subject `Mathematics` with the lecturer assigned. | Registrar can find the class/programme; lecturer can see the subject. |
| 5 | Registrar | Run a 5–10 row practice import, preview and undo it. Then import `pilot_students_messy.csv`, check the mapping and row report, and run the batch. | Practice batch undone; pilot batch shows 2 imported and 1 error; rejected student does not exist. |
| 6 | Registrar | Enrol both imported students in `Mathematics` for the current term. Check the lecturer roster. | Two enrolments on the correct term and subject. |
| 7 | Bursar | Create fee structure `PILOT-2026`, add a hand-calculated `Tuition 500.00` item, preview and confirm two invoices, record one `500.00` cash payment with reference, open the receipt and receipt PDF, verify the public token. | Invoice balances and the ledger agree; receipt PDF opens; public verification confirms the receipt. |
| 8 | Lecturer | Record attendance (one present, one absent with reason) and marks (`78.50`, `64.00`) on the roster, then submit the result sheet. | `ResultSheet` status is submitted; marks and attendance match the roster. |
| 9 | Registrar | Moderate, approve and release the sheet. Open the report card and PDF and verify the document token publicly. | Statuses progress in order; report-card PDF opens; verification does not reveal marks. |
| 10 | School admin | Walk each role checklist below. Test the feedback button from a normal page and from an error page. Link `/help/`, `docs/ERROR_CODES.md` and the support contacts in staff material. | Feedback item visible in `/platform/inbox/` with page and error reference; staff can find help without assistance. |
| 11 | Platform admin | Spot-check tenant isolation with a dummy institution, review `/platform/errors/`, slow-query logs and status page, then delete the dummy. | No cross-school marker visible; repeated codes have an owner. |
| 12 | Bursar + platform admin | Check debtors/collections/trial balance, trace one payment to the ledger and audit log, run the finance backup and a restore dry-run on staging. | Hand-calculated totals match; restore dry-run report saved; rollback owner named. |
| 13 | All roles | Re-run the end-to-end scenario on staging or the training institution. Review support requests, error references and the limitations document with the school. | Issue list with owner and decision for each item; no unresolved money error. |
| 14 | Platform admin + school admin | Go/no-go review: full suite green on the deployed commit, no *Outage*, backup and restore evidence, named rollback owner, signed pilot ticket. | Both owners sign. Feature set stays frozen. |

From day 15 onward, use the post-pilot review section of
`docs/PILOT_ONBOARDING.md` until the pilot review meeting.

---

## 4. Checklist for each role

Print these and tick them with the named person. If a step cannot be done,
write down why; do not silently skip it.

### Platform administrator

- [ ] Institution slug, currency, contact details, plan and modules agreed in writing.
- [ ] First admin created and secret delivered on a separate channel.
- [ ] 2FA enabled on the platform account and `/platform/security/` checked.
- [ ] Feedback item from the school found in `/platform/inbox/` and acknowledged.
- [ ] Error reference on the feedback item matches the error page.
- [ ] Dummy-institution tenant check done and dummy removed.
- [ ] `/platform/errors/`, slow-query log and `/status/` reviewed daily in week 2.
- [ ] Backup, restore dry-run and rollback owner recorded.

### Institution administrator

- [ ] Year and term are current before any student, mark or invoice is entered.
- [ ] Registrar, bursar and lecturer roles assigned to named people.
- [ ] Only the two pilot modules are enabled.
- [ ] Numbering formats confirmed before student and receipt records exist.
- [ ] `/help/`, error code guide and support contacts shared with staff.
- [ ] Every staff member can find and use “Pilot feedback”.
- [ ] No personal data is included in feedback messages.

### Registrar

- [ ] Student sample preview checked; duplicate and invalid rows understood.
- [ ] Practice batch undone before the real import.
- [ ] Imported students match the source file minus documented rejects.
- [ ] Enrolments are on the correct subject and term.
- [ ] Result sheet moderated, approved and released in order.
- [ ] Report card PDF opens and public verification confirms the document.
- [ ] Any result correction is recorded, not edited silently after release.

### Bursar / accounts

- [ ] Currency and academic year match the school's records.
- [ ] Fee structure total hand-calculated before invoice generation.
- [ ] Invoice preview checked for duplicate or wrong students.
- [ ] Payment reference recorded from the bank/cashbook, not invented.
- [ ] Receipt, receipt PDF and public verification checked.
- [ ] Balance matches billed minus paid minus waived for each invoiced student.
- [ ] Payment traced to the ledger and audit log.
- [ ] No online payment or fiscalisation assumed to be live.

### Teacher / lecturer

- [ ] Subject roster shows only the pilot students.
- [ ] Attendance saved with a reason for absence.
- [ ] Marks match the paper mark sheet before submission.
- [ ] Result sheet submitted once; later changes go back through the registrar.
- [ ] A missing student is reported to the registrar, not added by hand.

### Student

- [ ] If the school enabled student access: profile details checked and password changed.
- [ ] Released results, fees and receipts are read-only until the school says otherwise.
- [ ] Login and one-time codes never shared.
- [ ] A wrong fee or result is reported to the school office with the on-screen reference.

### Parent / guardian

- [ ] If the school enabled guardian access: only linked children appear.
- [ ] Results and fees are visible only after release/permission.
- [ ] Access problems are reported to the school office with the error reference.
- [ ] No other learner's records are accessible, even with a student number.

---

## 5. Success measures

Collect these from the deployed pilot instance and the pilot ticket. Numbers,
not impressions, decide the first review.

### Payments recorded

- **100%** of pilot payments have a unique reference, one `Payment` row, one
  receipt number and a matching ledger entry.
- Every invoiced student's balance equals billed minus paid minus waived,
  checked by hand for at least the two pilot students.
- At least one real receipt PDF downloads with `%PDF` content and the public
  verification page confirms the receipt number, amount, student and institution.
- **Zero** unresolved duplicate postings, overpayments or negative balances at
  the day-14 review.

### Reports produced

- At least one fee structure, two invoices, one receipt, one debtors report,
  one collections report and one trial balance are opened on screen.
- At least one report-card PDF and one receipt PDF are downloaded and open.
- Public verification succeeds for the report card and receipt tokens.
- Hand-calculated totals match the fee structure (`500.00` for the pilot item),
  the invoices and the payment.
- Any report that returns a 5xx, wrong total or wrong student becomes a tracked
  issue before sign-off.

### Support requests

- Every signed-in role can see and use “Pilot feedback” from any page.
- **100%** of pilot feedback items in `/platform/inbox/` carry the sender,
  institution, page path and any `ERR-XXXXXX` reference.
- Feedback messages contain no names, student numbers, health details or other
  personal information.
- Platform staff acknowledge pilot feedback within **one business day**.
- Any error code seen twice, or any support request about money or results, is
  either fixed or has a written explanation by day 14.
- Rate limiting allows five feedback reports per user per hour; a sixth is
  refused with a clear message rather than silently dropped.

---

## 6. Verification and sign-off

Run on the deployed commit before day 14:

```bash
python -m pytest -q
python -m ruff check .
python manage.py makemigrations --check --dry-run
python manage.py check
```

Expected: all tests pass, Ruff is clean, migrations report `No changes
detected`, and `manage.py check` shows only the expected `core.email.W001`
console-backend warning in development.

If any check fails, the pilot does not go live until the failure is fixed or
the risk is accepted in writing by both owners. After sign-off, changes follow
the incident/rollback path in `PRODUCTION_READINESS.md`, not a new feature
request.
