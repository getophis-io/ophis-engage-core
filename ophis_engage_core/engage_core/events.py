"""Messages to patients, written as Ophis EMR Core records change.

Wired through `doc_events` in hooks.py; the core does not know about them. Each sends only
when the clinic has an enabled template for its event, so switching a template off stops it.
"""

import frappe
from ophis_emr_core.emr_core import requisitions

from ophis_engage_core.engage_core import messaging


def requisition_pdf(order) -> bytes | None:
	"""An order on the clinic's provincial requisition form, or None for one written as free text."""
	if not order.get("requisition_form"):
		return None
	return requisitions.render_pdf(order)


def order_placed(doc, method=None):
	"""A new order: its requisition goes to the patient, as a protected PDF where the template asks."""
	if doc.status != "Ordered":
		return
	template = messaging.get_template("Order Placed")
	if not template:
		return
	attachment = None
	if template.has_attachment:
		content = requisition_pdf(doc)
		if content:
			attachment = (f"Requisition {doc.name}.pdf", content)
	messaging.create(
		doc.patient,
		template,
		context={
			"order": frappe._dict(
				name=doc.name,
				type=doc.order_type,
				tests=", ".join(line.strip() for line in (doc.tests or "").splitlines() if line.strip()),
				fasting=bool(doc.fasting),
			)
		},
		attachment=attachment,
		reference=doc,
	)


def appointment_booked(doc, method=None):
	"""A new appointment, booked at the desk or approved from a booking request."""
	if doc.status != "Scheduled":
		return
	send_for_appointment(doc, "Appointment Booked")


def appointment_changed(doc, method=None):
	if doc.status == "Cancelled" and doc.has_value_changed("status"):
		send_for_appointment(doc, "Appointment Cancelled")


def send_for_appointment(doc, event: str):
	template = messaging.get_template(event)
	if template:
		messaging.create(
			doc.patient, template, context={"appointment": messaging.describe_appointment(doc)}, reference=doc
		)
