import frappe
from frappe.tests import IntegrationTestCase
from ophis_emr_core.emr_core.testing import make_appointment, make_patient, make_practitioner

from ophis_engage_core.api.patient import booking
from ophis_engage_core.engage_core.testing import next_weekday, set_settings, signed_in, use_connections


def decide(name: str):
	"""The request, as the front desk opens it to approve or decline."""
	return frappe.get_doc("Booking Request", name)


class TestBookingRequests(IntegrationTestCase):
	def setUp(self):
		use_connections()
		set_settings(booking_requests_enabled=1, booking_horizon_days=28, max_pending_requests=2)
		self.practitioner = make_practitioner(default_appointment_duration=15)
		self.patient = make_patient()
		frappe.db.set_value(
			"Patient",
			self.patient.name,
			{"enrolment_status": "Rostered", "primary_practitioner": self.practitioner.name},
		)
		# Monday: inside the clinic's standard hours, which apply to a practitioner with no shifts.
		self.monday = next_weekday(0)

	def times_on(self, day) -> list[str]:
		days = booking.get_times(start=str(day))["days"]
		return next((row["times"] for row in days if row["date"] == str(day)), [])

	def test_open_times_leave_out_bookings_and_waiting_requests(self):
		make_appointment(
			make_patient().name, self.practitioner.name, appointment_date=self.monday, start_time="09:00:00"
		)
		with signed_in(self.patient.name):
			times = self.times_on(self.monday)
			self.assertNotIn("09:00", times)
			self.assertIn("09:15", times)
			booking.request(str(self.monday), "09:15")
			self.assertNotIn("09:15", self.times_on(self.monday))
			self.assertRaises(frappe.ValidationError, booking.request, str(self.monday), "09:15")

	def test_a_patient_not_rostered_is_asked_to_walk_in(self):
		frappe.db.set_value("Patient", self.patient.name, "enrolment_status", "Not Rostered")
		with signed_in(self.patient.name):
			options = booking.get_options()
			self.assertFalse(options["can_request"])
			self.assertIn("walk in", options["reason"])
			self.assertRaises(frappe.PermissionError, booking.request, str(self.monday), "10:00")

	def test_only_so_many_requests_wait_at_once(self):
		with signed_in(self.patient.name):
			booking.request(str(self.monday), "10:00")
			booking.request(str(self.monday), "10:30")
			self.assertRaises(frappe.ValidationError, booking.request, str(self.monday), "11:00")
			booking.withdraw(booking.get_requests()[0]["name"])
			booking.request(str(self.monday), "11:00")

	def test_approving_books_the_appointment_and_tells_the_patient(self):
		with signed_in(self.patient.name):
			name = booking.request(str(self.monday), "13:00", reason_for_visit="Sore knee")["name"]
		self.assertTrue(
			frappe.db.exists("Patient Message", {"reference_name": name, "event": "Booking Request Received"})
		)

		appointment = decide(name).approve(start_time="13:30")
		booked = frappe.get_doc("Patient Appointment", appointment)
		self.assertEqual(
			(booked.practitioner, str(booked.start_time), booked.reason_for_visit),
			(self.practitioner.name, "13:30:00", "Sore knee"),
		)
		self.assertEqual(
			frappe.db.get_value("Booking Request", name, ["status", "appointment"]), ("Approved", appointment)
		)
		self.assertTrue(
			frappe.db.exists(
				"Patient Message", {"reference_name": appointment, "event": "Appointment Booked"}
			)
		)
		self.assertRaises(frappe.ValidationError, decide(name).approve)

	def test_declining_needs_a_reason_and_tells_the_patient(self):
		with signed_in(self.patient.name):
			name = booking.request(str(self.monday), "14:00")["name"]
		self.assertRaises(frappe.ValidationError, decide(name).decline, " ")
		decide(name).decline("The doctor is away that day.")
		self.assertEqual(frappe.db.get_value("Booking Request", name, "status"), "Declined")
		message = frappe.get_doc(
			"Patient Message", {"reference_name": name, "event": "Booking Request Declined"}
		)
		self.assertIn("away that day", message.body)

	def test_a_patient_cannot_withdraw_someone_elses_request(self):
		with signed_in(self.patient.name):
			name = booking.request(str(self.monday), "15:00")["name"]
		other = make_patient()
		with signed_in(other.name):
			self.assertRaises(frappe.DoesNotExistError, booking.withdraw, name)
