# Manual testing - institution onboarding and bulk student upload

Use a staging copy with a real platform administrator account and working
`SITE_URL` / Mailjet settings (see `DEPLOY.md`). Every scenario starts from
`/platform/institutions/`. Do the scenarios in order; later ones reuse the
institution created in scenario 1.

## 1. Create an institution and its first administrator by invitation

1. Sign in as a platform main administrator, open **Institutions → New
   institution**, fill in the name, web address, type and contact details, and
   save.
2. On the institution page, confirm the onboarding checklist is visible and
   shows the first administrator as missing.
3. Open **Administrator accounts → Add administrator**, choose **Invitation**,
   enter a name and email, and save.
4. On the one-time screen, confirm the set-password link is shown with **Copy**
   and **Print**. Reload the page: it must say the details were already shown.
5. Check the inbox. The welcome email must contain the sign-in link
   `/i/<slug>/login/`, the one-time set-password link, the getting-started
   steps and both support contacts: `+263774573765` and
   `vanhuvaonejeffrey@gmail.com`. No password is emailed.
6. Open the set-password link, choose a strong password, then sign in at the
   branded `/i/<slug>/login/` page. The checklist now shows the administrator
   as signed in.

## 2. Temporary password and the guided first sign-in

1. Add a second administrator using **Temporary password** instead of an
   invitation.
2. On the one-time screen, copy the temporary password. Reload to confirm it
   cannot be shown twice.
3. Sign out and sign in with that temporary password at `/i/<slug>/login/`.
4. The next page must open the five-step guided setup: password, logo,
   institution details, academic year/term, sign-up link. It is branded with
   the institution name and never shows platform-only institution creation.
5. Complete the steps. On the last step, copy the generated sign-up link, then
   press **Finish setup**. The checklist shows setup complete, and the next
   visit to `/dashboard/` behaves normally.
6. Sign out, then sign in again: the guided setup must not reappear.

## 3. Sign-in link, QR and WhatsApp

1. On the institution detail page, find **Sign-in link**. Copy the URL and
   open it in a private window: the login page shows the institution's logo
   and only accepts that institution's accounts.
2. Scan the QR image with a phone: it opens the same URL.
3. Use **WhatsApp** to share the link and confirm the prefilled message.
4. In **Email the link to an administrator**, pick an administrator and press
   **Email sign-in link**. The message must arrive with the support contacts,
   and the page must report success. Suspending the institution changes the
   panel to **Disabled**, and the link stops working.

## 4. Platform-created sign-up link

1. Open **Sign-up links** from the institution page.
2. Create a link with a name, a kind (staff/student/any), an optional expiry in
   days and an optional maximum number of uses.
3. Copy the generated link from the success banner. It uses the configured
   `SITE_URL`, not the browser's host.
4. Open it in a private window and register: the person is created at the
   correct institution, and the use count increases. Exhausted or expired links
   are shown as closed.

## 5. Custom domain (optional)

1. Open **Custom domain** and enter a hostname such as `learn.example.org`.
   Save it. The domain is still **Not configured or not verified yet** and the
   standard `/i/<slug>/login/` link still works.
2. Follow the on-screen verification steps: add the displayed TXT record at
   `_edusphere.learn.example.org`, point the hostname at Vanpere, and add
   `ALLOWED_HOSTS=learn.example.org` and
   `CSRF_TRUSTED_ORIGINS=https://learn.example.org` to the deployment
   environment, then redeploy.
3. Open `https://learn.example.org/i/<slug>/login/` and confirm styling and
   uploads load. Tick the confirmation and press **Verify and enable domain**.
4. Confirm the page now reports the domain as enabled and the branded login
   works on the custom host. Press **Disable custom domain**: the record stays
   on screen but the host mapping stops. The `/i/<slug>/login/` link is
   unaffected throughout.

## 6. Changing the web address

1. In the **Details** form, change the slug and save.
2. Confirm both the new `/i/<new-slug>/` and the old `/i/<old-slug>/` addresses
   work; the old one redirects with its query string intact. The detail page
   lists the old address as redirecting for 90 days.
3. Try to save the slug `platform` and then the slug of another institution.
   Both are refused with a clear message and the stored slug is unchanged.
4. An expired redirect (older than 90 days) must 404 instead of redirecting.

## 7. Email delivery failures

Use staging only. Temporarily set an invalid `MAILJET_API_KEY` (or point the
email backend at a failing provider) and restart the app.

1. Press **Email sign-in link** and **Resend welcome email**. Each action must
   show a plain-English "could not send" error, not a success message.
2. Resend an invitation and reset a password from **Administrator accounts**.
   The one-time details screen must show the warning and keep the single-use
   link/password under **Show a one-time link**, so the administrator can still
   be onboarded by hand.
3. Repeat a failed welcome resend and confirm it is not counted as sent in the
   checklist. Restore the email settings afterwards.

## 8. Access control and protected data

1. Sign in as an ordinary institution administrator and request any
   `/platform/institutions/...` URL: expect **404**, never a platform screen.
2. Sign in as active platform staff without the relevant permission: expect
   **403** with a plain explanation.
3. Browse every `/platform/` screen with a main administrator and confirm there
   are no student lists, health, counselling, finance or results records. The
   health and usage pages show aggregate counts only.
4. Confirm an anonymous visitor to a protected platform URL is sent to the
   sign-in page.

## 9. Existing institutions after upgrade

Sign in (through the real login form, not a test helper) as an administrator of
an institution that existed before onboarding. There is no onboarding row for
them, so they land on `/dashboard/` normally and are not sent to the guided
setup. Similarly, programmatic/forced logins never open the setup.

## 10. Bulk upload students

Use a registrar who holds `students.bulk_import` in a test institution. First
create the programme `DIT`, the term `Term 1` and the courses `DIT101` and
`DIT102` (or tick "Create missing" in the preview). Save this as `students.csv`
in UTF-8:

```csv
Registration number,First name,Surname,Date of birth,Gender,Email,Phone,Programme,Year or grade,Stream or cohort,Intake or admission year,Guardian name,Guardian phone,Course codes or names,Term,Status,Username
STU-1001,Tariro,Dube,2008-03-04,Female,tariro.dube@example.com,0771234567,DIT,1,Blue,2026,Mrs Dube,0773333333,DIT101;DIT102,Term 1,active,
,Chipo,Nyoni,2009-07-12,Female,chipo.nyoni@example.com,0772345678,DIT,1,Blue,2026,Mr Nyoni,0774444444,DIT101,Term 1,active,
```

1. **Download the template and upload.** Open `/imports/students/new/` and
   download the Excel template. Confirm it has a `Students` sheet with a header
   row and example row, and an `Instructions` sheet. Then choose the CSV above
   (a real `.xlsx` works too) and upload it. Check the column mapping, then open
   the preview. Both rows should be **create**, with `STU-1001` kept and
   `(generated)` shown for Chipo, two enrolment additions each and no errors.
   Use the filters and download the preview report. Re-upload a `.txt` renamed
   to `.csv` and a file over 5 MB/5,000 rows to see the real file-type and size
   checks.
2. **Run the batch.** Choose **No login** first, leave "Create missing" unticked
   and leave "Apply registration rules" ticked. Confirm the run. The result
   page must show two writes, one `ImportBatch`, the enrolments, and no
   temporary password. Download both result CSVs. Reload the result page: a
   temporary password, when used, cannot be shown a second time.
3. **Re-upload and update.** Upload the same file again. The preview should
   show two **no change** rows and the run should write nothing. Now change
   Tariro's phone to `0779999999` and clear the guardian cells, then run. Only
   the phone changes; `STU-1001`, the guardian name/phone, enrolments and marks
   stay as they were. Set the year to `4` in the same row: it is warned about
   and kept until "Update year or grade" is ticked.
4. **Review and duplicates.** Add a row with a different first name but the
   same surname, date of birth and programme and no registration number/email.
   It must be **skip**, flagged for review, with nothing changed. Put the same
   registration number on two rows: both rows must be errors. Add a blank row
   and a bad email to confirm they are ignored/reported without stopping good
   rows.
5. **Missing objects.** Use a row with programme `Form 5`, stream `Blue`,
   intake year `2029` and course `SCIENCE101`. With "Create missing" off it is
   an error naming the programme/course. Tick all three creation options,
   preview the new objects, run, and confirm they are marked "Created by
   import, review needed" and that the existing `DIT` programme was not
   renamed.
6. **Enrolment rules and removal.** Remove `DIT102` from Tariro's row, keep
   `DIT101` and run without confirming removal: a warning appears and the
   enrolment stays. Tick removal and confirm: `DIT102` becomes `dropped` and
   its row (and any mark/attendance) is still present. With rules skipped, a
   full course is added anyway with a warning; with rules on it is blocked.
7. **Invitations and credentials.** Run a new file in **Invitation** mode and
   check one queued invitation per new account in the email log; no password
   is emailed or written to the audit trail. With
   `BULK_UPLOAD_EMAIL_CHUNK=1`, one delivery call sends one message and the
   rest stay queued for the next call. Use **Temporary password** in another
   batch: the password is shown once, is downloadable once and forces a change
   at first login. **No login** creates no usable password.
8. **Undo and safety.** Undo a fresh batch: the created student is removed,
   the user is deactivated and queued invitations are cancelled. Repeat with a
   batch that raised a first invoice: undo skips that student and leaves the
   invoice, user and student in place. Give a created student a mark or
   attendance and confirm undo also skips them.
9. **Permissions and tenants.** Sign in as a user without
   `students.bulk_import`: the upload URL is refused. With another institution
   open, try to view or run the first institution's batch URL: it is not
   visible and no student is matched across institutions.

## Cleanup

Delete the test institution, its test users and any custom DNS records. Remove
the temporary provider key change from scenario 7. Delete the sample students
and imported objects created by scenario 10, or undo only the batches that have
no financial or academic history.
