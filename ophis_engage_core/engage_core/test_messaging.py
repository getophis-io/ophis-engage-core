import io

import frappe
from frappe.tests import IntegrationTestCase
from ophis_emr_core.emr_core.testing import make_appointment, make_patient, make_practitioner
from pypdf import PdfReader

from ophis_engage_core.engage_core import messaging
from ophis_engage_core.engage_core.testing import blank_pdf, use_connections


class TestMessaging(IntegrationTestCase):
	def setUp(self):
		use_connections("Email", "SMS")
		self.patient = make_patient(mobile_phone="416-555-0101", email="patient.messaging@example.com")

	def message(self, template="General message", **kwargs):
		return messaging.create(self.patient.name, template, **kwargs)

	def test_content_is_encrypted_at_rest(self):
		message = self.message(subject="About your results", body="Your results are normal.")
		stored = frappe.db.get_value("Patient Message", message.name, ["subject", "body"], as_dict=True)
		self.assertTrue(stored.subject.startswith("phi1:"))
		self.assertTrue(stored.body.startswith("phi1:"))
		reloaded = frappe.get_doc("Patient Message", message.name)
		self.assertEqual(reloaded.subject, "About your results")
		self.assertEqual(reloaded.body, "Your results are normal.")

	def test_staff_words_are_not_run_as_a_template(self):
		message = self.message(body="{{ 7 * 7 }} <b>bold</b>")
		self.assertNotIn("49", message.body)
		self.assertIn("&lt;b&gt;", message.body)

	def test_announcements_are_simulated_and_masked(self):
		message = frappe.get_doc("Patient Message", self.message(body="Hello").name)
		self.assertEqual(message.status, "Sent")
		by_channel = {row.channel: row for row in message.deliveries}
		self.assertEqual(set(by_channel), {"Email", "SMS"})
		self.assertEqual({row.status for row in message.deliveries}, {"Simulated"})
		self.assertEqual(by_channel["SMS"].recipient, "••• 0101")
		self.assertEqual(by_channel["Email"].recipient, "p•••@example.com")
		logs = frappe.get_all(
			"Engage Connection Log", filters={"reference_name": message.name}, pluck="message"
		)
		self.assertTrue(logs and all(log.startswith("Simulator:") for log in logs))

	def test_sensitive_texts_say_only_that_there_is_a_message(self):
		message = self.message(body="Your biopsy result is in.")
		text = messaging.text_for(message, "https://example.com/link", "Test Clinic")
		self.assertNotIn("biopsy", text)
		self.assertIn("https://example.com/link", text)

	def test_a_channel_without_an_address_is_skipped(self):
		patient = make_patient(email="no.phone@example.com")
		message = frappe.get_doc(
			"Patient Message", messaging.create(patient.name, "General message", body="Hi").name
		)
		statuses = {row.channel: row.status for row in message.deliveries}
		self.assertEqual(statuses, {"Email": "Simulated", "SMS": "Skipped"})

	def test_switched_off_connections_announce_nothing(self):
		use_connections()
		message = frappe.get_doc("Patient Message", self.message(body="Hi").name)
		self.assertEqual(message.deliveries, [])
		self.assertEqual(message.status, "Sent")

	def test_protected_attachment_opens_with_health_card_and_birth_date(self):
		message = self.message(
			"General message with document", body="Your form", attachment=("Form.pdf", blank_pdf())
		)
		self.assertTrue(message.password_protected)
		file = frappe.get_doc(
			"File", {"attached_to_doctype": "Patient Message", "attached_to_name": message.name}
		)
		self.assertTrue(file.is_private)
		reader = PdfReader(io.BytesIO(file.get_content(encodings=[])))
		self.assertTrue(reader.is_encrypted)
		self.assertFalse(reader.decrypt("wrong"))
		self.assertTrue(reader.decrypt(f"{self.patient.health_card_number}19800115"))

	def test_unprotected_attachments_are_not_emailed(self):
		frappe.db.set_value("Message Template", "General message with document", "password_protected", 0)
		self.addCleanup(
			frappe.db.set_value, "Message Template", "General message with document", "password_protected", 1
		)
		message = self.message(
			"General message with document", body="Form", attachment=("Form.pdf", blank_pdf())
		)
		self.assertFalse(message.password_protected)
		self.assertEqual(frappe.get_all("Email Queue", filters={"reference_name": message.name}), [])

	def test_an_order_sends_the_patient_a_message(self):
		order = frappe.get_doc(
			{
				"doctype": "Patient Order",
				"patient": self.patient.name,
				"order_type": "Blood Work",
				"status": "Ordered",
				"order_date": frappe.utils.today(),
				"priority": "Routine",
				"tests": "CBC\nTSH",
			}
		).insert(ignore_permissions=True)
		message = frappe.get_doc(
			"Patient Message", {"reference_doctype": "Patient Order", "reference_name": order.name}
		)
		self.assertEqual(message.event, "Order Placed")
		self.assertTrue(message.sensitive)

	def test_booking_and_cancelling_an_appointment_tell_the_patient(self):
		practitioner = make_practitioner()
		appointment = make_appointment(self.patient.name, practitioner.name)
		booked = frappe.get_doc(
			"Patient Message", {"reference_name": appointment.name, "event": "Appointment Booked"}
		)
		self.assertIn(practitioner.practitioner_name, booked.body)
		appointment.cancel_appointment("Practitioner away")
		self.assertTrue(
			frappe.db.exists(
				"Patient Message", {"reference_name": appointment.name, "event": "Appointment Cancelled"}
			)
		)

	def test_a_switched_off_template_sends_nothing(self):
		frappe.db.set_value("Message Template", "Appointment confirmed", "enabled", 0)
		self.addCleanup(frappe.db.set_value, "Message Template", "Appointment confirmed", "enabled", 1)
		appointment = make_appointment(self.patient.name, make_practitioner().name)
		self.assertFalse(frappe.db.exists("Patient Message", {"reference_name": appointment.name}))

	def test_one_enabled_template_per_event(self):
		template = frappe.copy_doc(frappe.get_doc("Message Template", "Appointment confirmed"))
		template.template_name = "Second confirmation"
		self.assertRaises(frappe.ValidationError, template.insert)
