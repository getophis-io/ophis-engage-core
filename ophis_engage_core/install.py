"""What the core sets up on install and migrate: its connections and its starting templates.

Staff are Ophis EMR Core's: its roles (`EMR Physician`, `EMR Nurse`, `EMR Reception`,
`EMR Clinic Admin`) decide what they may do with these DocTypes. Patients never hold a role;
they reach only the patient API, through their Portal Account.
"""

import frappe

from ophis_engage_core import connections
from ophis_engage_core.engage_core.messaging import email_template_name

# The messages the core starts with, by event. Their words are Frappe Email Templates, which the
# clinic edits freely: these are only written where a template of that name does not exist yet.
TEMPLATES = [
	{
		"template_name": "Portal invitation",
		"event": "Portal Invitation",
		"description": "Sent by staff to invite a patient to the portal.",
		"send_email": 1,
		"send_sms": 1,
		"subject": "Your {{ clinic }} patient portal",
		"body": (
			"<p>Hello {{ patient.first_name }},</p>"
			"<p>{{ clinic }} has a patient portal, where you can read the clinic's messages to you and ask for appointments.</p>"
			"<p>Use the button below to sign in for the first time. We will send a code to the mobile phone we have on file for you.</p>"
		),
		"text_message": "Hello {{ patient.first_name }}, {{ clinic }} invites you to its patient portal:",
	},
	{
		"template_name": "Booking request received",
		"event": "Booking Request Received",
		"send_email": 1,
		"send_sms": 0,
		"subject": "We have your appointment request",
		"body": (
			"<p>Hello {{ patient.first_name }},</p>"
			"<p>We have your request to see {{ request.practitioner }} on {{ request.date }} at {{ request.time }}. "
			"The front desk will confirm it, or offer another time, soon.</p>"
		),
		"text_message": "{{ clinic }}: we have your request for {{ request.date }} at {{ request.time }} and will confirm it soon.",
	},
	{
		"template_name": "Booking request declined",
		"event": "Booking Request Declined",
		"send_email": 1,
		"send_sms": 1,
		"subject": "About your appointment request",
		"body": (
			"<p>Hello {{ patient.first_name }},</p>"
			"<p>We could not book {{ request.date }} at {{ request.time }} with {{ request.practitioner }}.</p>"
			"{% if request.reason %}<p>{{ request.reason }}</p>{% endif %}"
			"<p>Please choose another time in the portal{% if clinic_phone %}, or call us at {{ clinic_phone }}{% endif %}.</p>"
		),
		"text_message": "{{ clinic }}: we could not book the time you asked for. Please see the portal for details:",
	},
	{
		"template_name": "Appointment confirmed",
		"event": "Appointment Booked",
		"send_email": 1,
		"send_sms": 1,
		"subject": "Your appointment on {{ appointment.date }}",
		"body": (
			"<p>Hello {{ patient.first_name }},</p>"
			"<p>Your appointment with {{ appointment.practitioner }} is booked for {{ appointment.date }} at {{ appointment.time }}.</p>"
			"<p>Please bring your health card.{% if clinic_phone %} If you cannot come, call us at {{ clinic_phone }}.{% endif %}</p>"
		),
		"text_message": "{{ clinic }}: your appointment with {{ appointment.practitioner }} is on {{ appointment.date }} at {{ appointment.time }}.",
	},
	{
		"template_name": "Appointment cancelled",
		"event": "Appointment Cancelled",
		"send_email": 1,
		"send_sms": 1,
		"subject": "Your appointment on {{ appointment.date }} is cancelled",
		"body": (
			"<p>Hello {{ patient.first_name }},</p>"
			"<p>Your appointment with {{ appointment.practitioner }} on {{ appointment.date }} at {{ appointment.time }} is cancelled.</p>"
			"<p>You can ask for another time in the portal{% if clinic_phone %}, or call us at {{ clinic_phone }}{% endif %}.</p>"
		),
		"text_message": "{{ clinic }}: your appointment on {{ appointment.date }} at {{ appointment.time }} is cancelled.",
	},
	{
		"template_name": "Lab requisition",
		"event": "Order Placed",
		"description": "The requisition of a new order, locked with the patient's password.",
		"send_email": 1,
		"send_sms": 1,
		"sensitive": 1,
		"has_attachment": 1,
		"password_protected": 1,
		"subject": "Your laboratory requisition",
		"body": (
			"<p>Hello {{ patient.first_name }},</p>"
			"<p>Your requisition is attached. Take it to any community laboratory.</p>"
			"{% if order.fasting %}<p>Please do not eat or drink anything but water for 10 to 12 hours before your test.</p>{% endif %}"
		),
	},
	{
		"template_name": "Referral sent",
		"event": "Referral Sent",
		"send_email": 1,
		"send_sms": 1,
		"sensitive": 1,
		"subject": "Your referral has been sent",
		"body": (
			"<p>Hello {{ patient.first_name }},</p>"
			"<p>Your referral{% if referral.specialty %} to {{ referral.specialty }}{% endif %} has been sent to {{ referral.clinic }}. "
			"They will contact you to book.</p>"
		),
	},
	{
		"template_name": "General message",
		"event": "Manual",
		"description": "A message written by staff. Read in the portal; the email and text only say there is one.",
		"send_email": 1,
		"send_sms": 1,
		"sensitive": 1,
		"subject": "A message from {{ clinic }}",
		"body": "<p>{{ clinic }} has sent you a message.</p>",
	},
	{
		"template_name": "General message with document",
		"event": "Manual",
		"description": "A message written by staff with a PDF, locked with the patient's password.",
		"send_email": 1,
		"send_sms": 1,
		"sensitive": 1,
		"has_attachment": 1,
		"password_protected": 1,
		"subject": "A document from {{ clinic }}",
		"body": "<p>{{ clinic }} has sent you a document.</p>",
	},
]

EMAIL_TEMPLATE_FIELDS = ("subject", "body")


def after_install():
	after_migrate()


def after_migrate():
	connections.ensure_records()
	ensure_templates()


def ensure_templates():
	"""Write each starting template, and its Email Template, where it does not exist yet."""
	for spec in TEMPLATES:
		# A template the clinic already has (made here or by an app before) keeps its own words.
		if frappe.db.exists("Message Template", spec["template_name"]):
			continue
		email_name = email_template_name(spec["template_name"])
		if not frappe.db.exists("Email Template", email_name):
			email_template = frappe.get_doc(
				{
					"doctype": "Email Template",
					"__newname": email_name,
					"subject": spec["subject"],
					"response": spec["body"],
				}
			)
			email_template.name = email_name
			email_template.insert(ignore_permissions=True)
		values = {key: value for key, value in spec.items() if key not in EMAIL_TEMPLATE_FIELDS}
		# An event already answered by another enabled template keeps that one.
		enabled = spec["event"] == "Manual" or not frappe.db.exists(
			"Message Template", {"event": spec["event"], "enabled": 1}
		)
		frappe.get_doc(
			{"doctype": "Message Template", **values, "enabled": int(enabled), "email_template": email_name}
		).insert(ignore_permissions=True)
