# Ophis Engage Core

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Open-source patient engagement for Canadian family practice. Ontario comes first.

- **Platform:** Frappe v16, ERPNext v16 and [Ophis EMR Core](https://github.com/getophis-io/ophis-emr-core), Python 3.14
- **License:** GNU AGPL v3.0 or later
- **Contact:** [dev@getophis.io](mailto:dev@getophis.io)

Messages to patients by email, SMS and WhatsApp, patients' mailboxes and how they sign in to them,
appointment requests, and eReferrals to other clinics, as a Frappe app on Ophis EMR Core. It has no
user interface of its own; an app on top of it (Harbour, for one) supplies the staff screens and
the patient portal.

This is a proof of concept. Its connections run in Simulator mode until switched on, OceanMD's live
connection is not built, and it must not be used with real patient information.

## What is in it

One module, `Engage Core`. From Frappe it uses `Email Template`, `Email Account` and the email
queue, `SMS Settings`, `Address`, `File`, `User` and `Print Format`; from Ophis EMR Core,
`Patient`, `Practitioner`, `Appointment Type`, `Patient Appointment`, `Patient Order` and
`Patient Referral`.

| Area | DocTypes |
|---|---|
| Patient messages | `Message Template`, `Patient Message`, `Patient Message Delivery` |
| Patient sign-in | `Portal Account`, `Portal Link` |
| Booking requests | `Booking Request` |
| eReferrals | `eReferral`, `Referral Clinic`, print format `Referral Letter` |
| Set-up | `Engage Settings`, `Engage Connection`, `Engage Connection Log` |

Rules worth knowing:

- **Templates decide handling.** Every message comes from a `Message Template`, whose words are a
  Frappe `Email Template`. The template decides the channels, whether the content is sensitive,
  whether there is an attachment and whether it is password protected. One enabled template answers
  each event (an appointment booked or cancelled, an order placed, a booking request received or
  declined, a referral sent, a portal invitation); Manual templates are chosen by staff.
- **Sensitive stays in the portal.** A sensitive message's email and texts say only that there is a
  message, with a personal link. SMS and WhatsApp never carry sensitive content or attachments.
- **Protected attachments** are PDFs locked (AES-256) with the patient's health card number followed
  by their date of birth as YYYYMMDD. Only a protected attachment is emailed. An order's
  requisition is sent this way, on the clinic's provincial form.
- **Signing in** always ends with a one-time code sent to the mobile phone (or, without one, the
  email) on the patient's chart. A patient starts from a personal link in any message, or by
  typing their mobile number; the answer never says whether a number belongs to a patient. Several
  patients on one phone choose who is signing in. Each account has a Website `User` at an address
  that cannot receive mail, so no patient's email or phone lands in the user table.
- **Booking requests** are for rostered patients, with their own practitioner, at an open time
  (inside their working hours, clear of appointments and other waiting requests). The front desk
  approves one into a `Patient Appointment` (`Booking Request.approve`) or declines it with a reason
  (`decline`); the patient is told either way.
- **eReferrals** send a `Patient Referral` to a clinic found in the directory or on OceanMD, through
  OceanMD or by email, with the `Referral Letter` as a PDF.
- **Encrypted.** Message subjects, bodies and texts are encrypted with Ophis EMR Core's key. Logs and
  delivery records hold masked addresses and no content.

## Connections

Each is an `Engage Connection` record, off and in Simulator mode until a clinic admin changes it.
In Simulator mode nothing leaves the clinic, and every answer and log entry says so.

| Connection | Through |
|---|---|
| Email | The site's default outgoing `Email Account` |
| SMS | Frappe's `SMS Settings`, set up for Twilio by `connections.twilio.configure_sms_settings` |
| WhatsApp | The same Twilio account, with a WhatsApp sender on the connection |
| OceanMD | Simulator only: the live side needs an OceanMD partner agreement |

## The patient API

Whitelisted methods for patient apps, under `ophis_engage_core.api.patient`. Every method but
signing in answers only for the patient signed in.

| Module | Methods |
|---|---|
| `auth` (open to guests, rate limited) | `get_session`, `request_code(phone)`, `start_from_link(token)`, `verify(challenge, code)`, `choose(challenge, index)` |
| `inbox` | `get_messages(start)`, `get_message(name)`, `download(name)`, `get_unread_count` |
| `booking` | `get_options`, `get_times(appointment_type, start)`, `request(date, time, …)`, `get_requests`, `withdraw(name)` |

Sign-in links point at `<portal address>/welcome?token=…`, and after signing in a link to a message
opens `/messages/<name>`; the portal app serves both. The portal address is set in Engage Settings,
and defaults to this site's `/portal`.

## Services for apps built on it

Staff screens call these as plain Python, after their own permission checks:

- `engage_core/messaging.py` — `create`, `deliver`, `send_manual`, `invite`, `save_template`, `describe_template`.
- `engage_core/booking.py` — open times (`get_days`, `is_open`) and a patient's requests.
- `engage_core/referrals.py` — `search`, `search_directory`, `save_clinic`, `send`.
- `engage_core/accounts.py` — portal accounts, links and sign-in.
- `connections` — `get_status` (never with secrets), `get(code)`.

## Hooks

| Hook | Direction | For |
|---|---|---|
| `phi_encrypted_fields` | answered for Ophis EMR Core | `Patient Message` subject, body and text, encrypted and rotated with the core's fields |
| `ereferral_service` | answered for apps built on the core | `(patient)` → `{enabled, can_send, oceanmd, email, by_referral}`, for a referral screen's eReferral panel |

Document events on `Patient Order` (after insert) and `Patient Appointment` (after insert, on
update) send the patient their messages. `ophis_engage_core.engage_core.testing` has helpers for
the tests of apps built on the core: `use_connections`, `set_settings`, `signed_in`, `blank_pdf`.

## Installing

```
bench get-app https://github.com/getophis-io/ophis-engage-core
bench --site <site> install-app ophis_engage_core
```

Ophis EMR Core must be installed on the site first. Tests run on a site with `allow_tests` set:

```
bench --site <test site> run-tests --app ophis_engage_core
```

## Contributing

Issues and pull requests are welcome. Use synthetic data only: never put real patient information
in an issue, a test, a fixture or a log. Add a test with any change in behaviour.

If you work with an AI coding agent, the project's rules for it are in `AGENTS.md`. Claude Code's
own settings, skills and subagents go in `.claude/`.

## Contact

Questions about development, contributions or security: [dev@getophis.io](mailto:dev@getophis.io).
Report security issues by email rather than in a public issue.

## License

Copyright (C) 2026 STRMLNE

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program
(`LICENSE`). If not, see <https://www.gnu.org/licenses/>.

If you run a modified version of this software as a network service, the AGPL requires you to
offer its users the source code of your modified version.
