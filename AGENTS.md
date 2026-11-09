# AGENTS.md

Guidance for AI coding agents working in this repo (Claude Code, Codex, Cursor, Copilot and others).

## What this is

`ophis_engage_core` ("Ophis Engage Core") — an open-source Frappe v16 app for patient engagement in
Canadian (Ontario-first) family practice, built on Ophis EMR Core (`ophis_emr_core`, in
`../ophis-emr-core.git`): messages to patients by email, SMS and WhatsApp, patients' mailboxes and
their sign-in, booking requests, and eReferrals. No user interface. POC stage. Licensed
AGPL-3.0-or-later.

Harbour (`harbour`, in `../../engage-app.git`) is the app built on it: the staff screens, the patient
portal SPA, the staff API, the time-clock gate. Harbour imports this app; this app never imports
Harbour, CanChart or any other app built on it.

- `ophis_engage_core/hooks.py` — app manifest; document events on Ophis EMR Core's records; the hooks it answers.
- `ophis_engage_core/install.py` — connection records and the starting message templates, written only where missing.
- `ophis_engage_core/engage_core/` — the only module (`Engage Core`). DocTypes, and:
  `messaging.py` (patient messages, templates, protected attachments, delivery),
  `accounts.py` (portal accounts, personal links, one-time codes, `current_patient`),
  `inbox.py` (a patient's mailbox), `booking.py` (open times and booking requests),
  `referrals.py` (eReferrals and the clinic directory), `events.py` (messages as orders and
  appointments change), `clinic.py` (the clinic as patients know it), `testing.py` (helpers
  shared by tests and by apps built on the core).
- `ophis_engage_core/connections/` — Email, SMS and WhatsApp (Twilio through Frappe's SMS Settings), OceanMD.
- `ophis_engage_core/api/patient/` — the patient API: sign-in, mailbox, booking requests.
- `ophis_engage_core/templates/emails/` — the email that announces a patient message.

## Ground rules

- **No UI.** No pages, SPA, desk form scripts or assets. Business rules live here as plain
  functions; staff endpoints and screen shaping belong to the app on top.
- **The patient API is the one exception.** `api/patient/` holds the whitelisted methods patient
  apps call, so every portal gets the same sign-in and the same "only your own messages" rules.
  Every method there but signing in reads the patient from `accounts.current_patient()`, never
  from its arguments. Keep them thin: the rules go in `engage_core/`.
- **Never import an app built on the core.** Ask it through a hook instead. Document every hook the
  core asks or answers in the README.
- **Ophis EMR Core owns the clinical record.** Patients, practitioners, appointments, orders and
  referrals are its records; import from `ophis_emr_core` freely, but never change its DocTypes.
  A booking request becomes a `Patient Appointment` only when the front desk approves it, under
  the core's rules (rostered patients, with their own practitioner).
- **Built on Frappe.** Message words are `Email Template`, email goes through the email queue, SMS
  through `SMS Settings`, clinic addresses are `Address`, attachments are private `File`s, patients'
  logins are Website `User`s. Frappe's `Notification` is not used: contact details are encrypted and
  attachments must be locked. Do not add custom fields to Frappe, ERPNext or Ophis EMR Core DocTypes.
- **Canada only.** Phone numbers are Canadian (`twilio.to_e164`); addresses are in Canada.
- **DocType names are public.** Apps and existing sites depend on them; renaming one needs a patch
  and a note in the README.
- **Templates decide handling.** Every patient message comes from a `Message Template`: its event,
  channels, whether it is sensitive, whether it has an attachment, and whether that is password
  protected. One enabled template per event; Manual ones are chosen by staff. Words written by
  staff are escaped, never rendered as Jinja.
- **Sensitive stays in the portal.** A sensitive message's email, SMS and WhatsApp say only that
  there is a message, with the link. SMS and WhatsApp never carry sensitive content or
  attachments; only a password-protected attachment is emailed.
- **Protected attachments.** A PDF locked (AES-256) with the patient's health card number followed
  by their date of birth as YYYYMMDD, no spaces, no version code; date of birth alone without a
  card. Only the locked file is kept.
- **Signing in.** Always a one-time code to the mobile phone (or, without one, the email) on the
  patient's chart. Answers never reveal whether a number is a patient's. Simulator codes are
  returned only in developer mode.
- **Patient content is encrypted.** Message subjects, bodies and texts are encrypted with Ophis EMR
  Core's key through its `phi_encrypted_fields` hook (`Patient Message` takes
  `EncryptedFieldsMixin`). Read them with `frappe.get_doc` or `phi.decrypt`. Add new patient content
  to that hook. Logs and delivery rows hold masked addresses and no content.
- **Simulator output is never passed off as real.** Every connection runs in Simulator mode until
  switched to Production, and its answers and logs say they are simulated. OceanMD's live side is
  not built; in Production it stops rather than pretend.
- **Synthetic data only.** No real patient information in fixtures, tests or logs.
- **Tests.** Add a test with any behaviour change. Records are not rolled back between tests, so
  tests use their own phone numbers and restore any template they change.

## Local dev

Run it through the `cemr-bench` stack (`../../cemr-bench`), mounted beside Ophis EMR Core and linked
into the bench as `apps/ophis_engage_core`. From there:

```
docker compose exec -w /home/frappe/frappe-bench frappe bench --site emr.localhost migrate
docker compose exec -w /home/frappe/frappe-bench frappe bench --site test-emr.localhost run-tests --app ophis_engage_core
```

- Tests run on `test-emr.localhost`, never on the dev site.
- Don't push or open PRs unless asked.
