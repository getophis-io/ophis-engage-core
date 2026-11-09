import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, format_date, format_time, now_datetime

from ophis_engage_core.engage_core import booking, messaging


class BookingRequest(Document):
	def validate(self):
		if not cint(self.duration):
			self.duration = booking.get_duration(self.practitioner, self.appointment_type)

	def decide(self, status: str):
		self.check_permission("write")
		if self.status != "Pending":
			frappe.throw(_("This request has already been {0}.").format(_(self.status).lower()))
		self.status = status
		self.decided_by = frappe.session.user
		self.decided_on = now_datetime()

	@frappe.whitelist()
	def approve(self, appointment_date: str | None = None, start_time: str | None = None) -> str:
		"""Book the visit as a `Patient Appointment`, at the time asked for or one the front desk chose instead.

		Ophis EMR Core's own rules apply to the booking (rostered with this practitioner, no overlap),
		and booking it sends the patient the Appointment Booked message. Returns the appointment.
		"""
		self.decide("Approved")
		appointment = frappe.get_doc(
			{
				"doctype": "Patient Appointment",
				"patient": self.patient,
				"practitioner": self.practitioner,
				"appointment_type": self.appointment_type,
				"appointment_date": appointment_date or self.requested_date,
				"start_time": start_time or self.requested_time,
				"duration": self.duration,
				"reason_for_visit": self.reason_for_visit,
				"notes": _("Requested in the patient portal ({0}).").format(self.name),
			}
		).insert()
		self.appointment = appointment.name
		self.save()
		return appointment.name

	@frappe.whitelist()
	def decline(self, reason: str):
		if not (reason or "").strip():
			frappe.throw(_("Say why: the patient is told."))
		self.decide("Declined")
		self.decline_reason = reason.strip()
		self.save()
		template = messaging.get_template("Booking Request Declined")
		if template:
			messaging.create(self.patient, template, context={"request": self.describe()}, reference=self)

	def withdraw(self):
		"""The patient no longer wants it. Only from the portal, for their own request."""
		if self.status != "Pending":
			frappe.throw(_("This request has already been {0}.").format(_(self.status).lower()))
		self.status = "Withdrawn"
		self.save(ignore_permissions=True)

	def describe(self) -> dict:
		"""The request as templates use it: {date, time, practitioner, type, reason}."""
		return frappe._dict(
			name=self.name,
			date=format_date(self.requested_date, "EEEE, MMMM d"),
			time=format_time(self.requested_time, "h:mm a"),
			practitioner=self.practitioner_name,
			type=self.appointment_type or _("Appointment"),
			reason=self.decline_reason or "",
		)
