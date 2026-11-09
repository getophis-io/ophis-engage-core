"""Patient messages: written from a template, read in the portal inbox, announced by email,
SMS and WhatsApp.

A message's words are a Frappe `Email Template` (subject and message, Jinja), named by
the `Message Template`, which decides everything else about how it is handled:

- **Channels**: which of email, SMS and WhatsApp announce it, each only where its connection is
  switched on and the patient's chart has the address or number.
- **Sensitive content**: the announcement then says only that there is a message, and gives the
  link; the content is read in the portal, after signing in.
- **Attachment** and **password protection**: a protected attachment is a PDF locked with the
  patient's health card number followed by their date of birth (YYYYMMDD), no spaces and no
  version code. Only a protected attachment goes out by email; any other is in the portal only.

SMS and WhatsApp never carry sensitive content or attachments. Every announcement carries the
patient's own sign-in link (accounts.make_link): it opens the message once a code sent to the
phone or email on their chart is entered. The first message a patient gets is therefore also
their way into the portal, invited or not.

Subjects, bodies and texts are stored encrypted (Ophis EMR Core's `phi`, through the
`phi_encrypted_fields` hook), so read them through `frappe.get_doc` or `phi.decrypt`.
"""

import io
import secrets

import frappe
from frappe import _
from frappe.utils import escape_html, format_date, format_time, getdate, now_datetime

from ophis_engage_core import connections
from ophis_engage_core.connections import email, twilio
from ophis_engage_core.engage_core import accounts, clinic

CHANNELS = (
	# (channel, template flag, connection, patient field)
	("Email", "send_email", "Email", "email"),
	("SMS", "send_sms", "SMS", "mobile_phone"),
	("WhatsApp", "send_whatsapp", "WhatsApp", "mobile_phone"),
)

# What a sensitive message's announcement says in place of its content.
SENSITIVE_SUBJECT = "New message from {clinic}"
SENSITIVE_TEXT = "{clinic}: you have a new message. Read it securely here: {link}"


def get_template(event: str):
	"""The enabled template sent for an event, or None: an event without one sends nothing."""
	name = frappe.db.get_value(
		"Message Template", {"event": event, "enabled": 1}, "name", order_by="creation asc"
	)
	return frappe.get_doc("Message Template", name) if name else None


def get_context(patient, **extra) -> dict:
	"""What every template can use, plus what the event adds (`appointment`, `order`, …)."""
	return {
		"patient": frappe._dict(
			first_name=patient.preferred_name or patient.first_name,
			last_name=patient.last_name,
			full_name=patient.patient_name,
		),
		"clinic": clinic.clinic_name(),
		"clinic_phone": clinic.clinic_phone(),
		**extra,
	}


def render(text: str | None, context: dict) -> str:
	return frappe.render_template(text or "", context).strip() if text else ""


def attachment_password(patient) -> str:
	"""The password a protected attachment opens with: health card digits, then YYYYMMDD."""
	if not patient.date_of_birth:
		frappe.throw(
			_(
				"{0} has no date of birth on their chart, so an attachment cannot be protected for them."
			).format(patient.patient_name)
		)
	number = "".join(character for character in (patient.health_card_number or "") if character.isdigit())
	return f"{number}{getdate(patient.date_of_birth).strftime('%Y%m%d')}"


def password_hint(patient) -> str:
	if patient.health_card_number:
		return _(
			"The attachment is protected. Its password is your health card number followed by your date of birth "
			"as YYYYMMDD, with no spaces and without the version code. For example, 1234567890 and 15 January 1980 "
			"make 123456789019800115."
		)
	return _(
		"The attachment is protected. Its password is your date of birth as YYYYMMDD, for example 19800115."
	)


def protect_pdf(content: bytes, password: str) -> bytes:
	"""The PDF locked with `password` (AES-256). Its owner password is random and kept nowhere."""
	from pypdf import PdfReader, PdfWriter

	writer = PdfWriter()
	writer.append(PdfReader(io.BytesIO(content)))
	writer.encrypt(user_password=password, owner_password=secrets.token_urlsafe(24), algorithm="AES-256")
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()


def create(
	patient: str,
	template,
	context: dict | None = None,
	attachment: tuple[str, bytes] | None = None,
	reference=None,
	subject: str | None = None,
	body: str | None = None,
	deliver: bool = True,
):
	"""Write a message to a patient from a template and, unless `deliver` is false, announce it.

	`template` is a Message Template or its name. `subject` and `body` (plain text) replace its
	Email Template's, for a message written by staff. `attachment` is (file name, content); it must be a PDF when
	the template protects attachments. Returns the Patient Message.
	"""
	if isinstance(template, str):
		template = frappe.get_doc("Message Template", template)
	email_template = frappe.get_doc("Email Template", template.email_template)
	patient_doc = frappe.get_doc("Patient", patient)
	context = get_context(patient_doc, **(context or {}))
	accounts.ensure_account(patient)
	# Words written by staff are shown as written, never run as a template.
	if body is not None:
		body = escape_html(body).replace("\n", "<br>")
	else:
		body = render(
			email_template.response_html if email_template.use_html else email_template.response, context
		)

	message = frappe.get_doc(
		{
			"doctype": "Patient Message",
			"patient": patient,
			"template": template.name,
			"event": template.event,
			"subject": (subject or "").strip() or render(email_template.subject, context),
			"body": body,
			"text_message": render(template.text_message, context),
			"sensitive": template.sensitive,
			"password_protected": bool(attachment)
			and template.has_attachment
			and template.password_protected,
			"reference_doctype": reference.doctype if reference else None,
			"reference_name": reference.name if reference else None,
		}
	).insert(ignore_permissions=True)

	if attachment and template.has_attachment:
		file_name, content = attachment
		if message.password_protected:
			content = protect_pdf(content, attachment_password(patient_doc))
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"content": content,
				"is_private": 1,
				"attached_to_doctype": "Patient Message",
				"attached_to_name": message.name,
				"attached_to_field": "attachment",
			}
		).insert(ignore_permissions=True)
		message.db_set({"attachment": file.file_url, "attachment_name": file_name})

	if deliver:
		frappe.enqueue(
			"ophis_engage_core.engage_core.messaging.deliver",
			message=message.name,
			enqueue_after_commit=True,
			now=frappe.flags.in_test,
		)
	return message


def deliver(message: str):
	"""Announce a message on each of its template's channels, and record how each went."""
	doc = frappe.get_doc("Patient Message", message)
	template = frappe.get_doc("Message Template", doc.template)
	patient = frappe.get_doc("Patient", doc.patient)
	link = accounts.make_link(doc.patient, "Message", message=doc.name)
	clinic_name = clinic.clinic_name()

	doc.set("deliveries", [])
	for channel, flag, code, field in CHANNELS:
		if not template.get(flag) or not connections.is_enabled(code):
			continue
		recipient = patient.get(field)
		row = {"channel": channel, "sent_on": now_datetime()}
		if not recipient:
			doc.append(
				"deliveries",
				{
					**row,
					"status": "Skipped",
					"detail": _("No {0} on the chart.").format(_(field.replace("_", " "))),
				},
			)
			continue
		masked = email.mask(recipient) if channel == "Email" else twilio.mask(recipient)
		try:
			if channel == "Email":
				result = send_email(doc, patient, recipient, link, clinic_name)
			else:
				result = connections.get(code).send(
					recipient, text_for(doc, link, clinic_name), reference=doc
				)
		except Exception as error:
			doc.append(
				"deliveries",
				{
					**row,
					"recipient": masked,
					"status": "Failed",
					"detail": frappe.utils.strip_html(str(error))[:300],
				},
			)
			continue
		doc.append(
			"deliveries",
			{
				**row,
				"recipient": masked,
				"status": "Simulated" if result.get("simulated") else "Sent",
				"provider_reference": result.get("reference"),
				"detail": _("Simulator: nothing was sent.") if result.get("simulated") else None,
			},
		)

	statuses = {row.status for row in doc.deliveries}
	# Always in the inbox; Failed only when every announcement that was tried failed.
	doc.status = "Failed" if "Failed" in statuses and not statuses & {"Sent", "Simulated"} else "Sent"
	doc.sent_on = now_datetime()
	doc.save(ignore_permissions=True)
	return doc


def text_for(message, link: str, clinic_name: str) -> str:
	"""The SMS or WhatsApp text: the template's own, or for sensitive content only a notice."""
	if message.sensitive or not message.text_message:
		return SENSITIVE_TEXT.format(clinic=clinic_name, link=link)
	return f"{message.text_message} {link}"


def send_email(message, patient, recipient: str, link: str, clinic_name: str) -> dict:
	sensitive = bool(message.sensitive)
	attachments = []
	if message.attachment and message.password_protected:
		file = frappe.get_doc("File", {"file_url": message.attachment, "attached_to_name": message.name})
		attachments.append(
			{"fname": message.attachment_name or file.file_name, "fcontent": file.get_content(encodings=[])}
		)

	html = frappe.render_template(
		"ophis_engage_core/templates/emails/patient_message.html",
		{
			"clinic": clinic_name,
			"sensitive": sensitive,
			"body": "" if sensitive else message.body or "",
			"link": link,
			"has_attachment": bool(message.attachment),
			"attached": bool(attachments),
			"password_hint": password_hint(patient) if attachments else "",
		},
	)
	subject = SENSITIVE_SUBJECT.format(clinic=clinic_name) if sensitive else message.subject
	return connections.get("Email").send(recipient, subject, html, attachments=attachments, reference=message)


def describe_appointment(appointment) -> dict:
	"""An appointment as templates use it: {date, time, practitioner, type}."""
	return frappe._dict(
		name=appointment.name,
		date=format_date(appointment.appointment_date, "EEEE, MMMM d"),
		time=format_time(appointment.start_time, "h:mm a"),
		practitioner=appointment.practitioner_name,
		type=appointment.appointment_type or _("Appointment"),
	)


# Messages started by staff. The app on top checks who may call these; they check the records.


def send_manual(patient: str, template: str, subject: str | None = None, body: str | None = None):
	"""A message written by staff, on a Manual template that decides how it is handled."""
	if frappe.db.get_value("Message Template", template, "event") != "Manual":
		frappe.throw(_("Choose a template for messages written by staff."))
	if body is not None and not body.strip():
		frappe.throw(_("Write the message."))
	return create(patient, template, subject=subject, body=body)


def invite(patient: str):
	"""Send a patient their invitation to the portal: a personal link, then a code to their phone."""
	account = accounts.ensure_account(patient)
	if account.status == "Disabled":
		frappe.throw(_("This patient's portal account is switched off."))
	template = get_template("Portal Invitation")
	if not template:
		frappe.throw(_("There is no enabled Portal Invitation template."))
	return create(patient, template, reference=account)


# Templates. A Message Template's words are a Frappe Email Template, saved with it.

TEMPLATE_FIELDS = (
	"event",
	"enabled",
	"description",
	"send_email",
	"send_sms",
	"send_whatsapp",
	"sensitive",
	"has_attachment",
	"password_protected",
	"text_message",
)


def email_template_name(template_name: str) -> str:
	return f"Patient Message - {template_name}"


def describe_template(name: str) -> dict:
	"""A Message Template with the subject and body of its Email Template."""
	template = frappe.get_doc("Message Template", name)
	email_template = frappe.get_doc("Email Template", template.email_template)
	return {
		"name": template.name,
		**{field: template.get(field) for field in TEMPLATE_FIELDS},
		"email_template": email_template.name,
		"subject": email_template.subject,
		"use_html": email_template.use_html,
		"body": email_template.response_html if email_template.use_html else email_template.response,
	}


def save_template(values: dict, name: str | None = None):
	"""Save a Message Template and the words of its Email Template together. Returns the template."""
	template = frappe.get_doc("Message Template", name) if name else frappe.new_doc("Message Template")
	if not name:
		template.template_name = (values.get("template_name") or "").strip()
		if not template.template_name:
			frappe.throw(_("Name the template."))
	template.update({field: values[field] for field in TEMPLATE_FIELDS if field in values})

	email_name = template.email_template or email_template_name(template.template_name)
	email_template = (
		frappe.get_doc("Email Template", email_name)
		if frappe.db.exists("Email Template", email_name)
		else frappe.new_doc("Email Template")
	)
	if email_template.is_new():
		email_template.name = email_name
	email_template.subject = values.get("subject", email_template.subject)
	body = values.get("body")
	if body is not None:
		if email_template.use_html:
			email_template.response_html = body
		else:
			email_template.response = body
	if email_template.is_new():
		email_template.insert(ignore_permissions=True)
	else:
		email_template.save(ignore_permissions=True)
	template.email_template = email_template.name
	template.save(ignore_permissions=True)
	return template
