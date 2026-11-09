import frappe
from frappe.tests import IntegrationTestCase
from ophis_emr_core.emr_core.testing import make_patient

from ophis_engage_core import connections
from ophis_engage_core.connections import twilio
from ophis_engage_core.engage_core import accounts, messaging
from ophis_engage_core.engage_core.testing import use_connections


class TestStaffMessages(IntegrationTestCase):
	def setUp(self):
		use_connections("Email", "SMS")
		self.patient = make_patient(mobile_phone="416-555-0190", email="staff.messages@example.com")

	def test_staff_write_on_manual_templates_only(self):
		message = messaging.send_manual(
			self.patient.name, "General message", subject="Hello", body="Call us."
		)
		self.assertEqual(frappe.get_doc("Patient Message", message.name).body, "Call us.")
		self.assertRaises(
			frappe.ValidationError,
			messaging.send_manual,
			self.patient.name,
			"Appointment confirmed",
			body="Hi",
		)
		self.assertRaises(
			frappe.ValidationError, messaging.send_manual, self.patient.name, "General message", body=" "
		)

	def test_an_invitation_makes_the_account_and_carries_a_link(self):
		invitation = messaging.invite(self.patient.name)
		self.assertEqual(invitation.event, "Portal Invitation")
		self.assertEqual(frappe.db.get_value("Portal Account", self.patient.name, "status"), "Invited")
		self.assertTrue(
			frappe.db.exists("Portal Link", {"patient": self.patient.name, "message": invitation.name})
		)

	def test_a_disabled_account_is_not_invited(self):
		account = accounts.ensure_account(self.patient.name)
		account.status = "Disabled"
		account.save()
		self.addCleanup(frappe.db.set_value, "Portal Account", self.patient.name, "status", "Invited")
		self.assertRaises(frappe.ValidationError, messaging.invite, self.patient.name)

	def test_a_template_is_saved_with_its_email_template(self):
		template = messaging.save_template(
			{
				"template_name": f"Flu clinic {frappe.generate_hash(length=6)}",
				"event": "Manual",
				"subject": "Flu shots",
				"body": "<p>Flu shots are in.</p>",
				"send_email": 1,
				"sensitive": 0,
			}
		)
		self.assertEqual(
			frappe.db.get_value("Email Template", template.email_template, "subject"), "Flu shots"
		)
		self.assertEqual(messaging.describe_template(template.name)["body"], "<p>Flu shots are in.</p>")
		self.assertIn("Flu shots are in", messaging.create(self.patient.name, template.name).body)

	def test_links_point_at_the_portal_address(self):
		self.assertTrue(
			accounts.make_link(self.patient.name, "Invitation").split("?")[0].endswith("/portal/welcome")
		)


class TestConnections(IntegrationTestCase):
	def test_every_connection_has_a_switched_off_simulator_record(self):
		connections.ensure_records()
		for cls in connections.get_classes():
			self.assertTrue(frappe.db.exists("Engage Connection", cls.code))

	def test_production_without_a_live_side_stops_rather_than_pretends(self):
		use_connections("OceanMD", mode="Production")
		self.addCleanup(use_connections)
		self.assertRaises(
			connections.ConnectionError, connections.get("OceanMD").search_clinics, "cardiology"
		)

	def test_the_status_never_carries_the_secret(self):
		doc = frappe.get_doc("Engage Connection", "OceanMD")
		doc.auth_token = "s3cret"
		doc.save(ignore_permissions=True)
		row = next(row for row in connections.get_status() if row.code == "OceanMD")
		self.assertTrue(row.has_secret)
		self.assertNotIn("auth_token", row)

	def test_twilio_fills_frappes_sms_settings(self):
		twilio.configure_sms_settings("AC123", "token", "416 555 0100")
		sms = frappe.get_doc("SMS Settings")
		self.assertIn("/Accounts/AC123/Messages.json", sms.sms_gateway_url)
		self.assertEqual(
			{row.parameter: row.value for row in sms.parameters if not row.header}, {"From": "+14165550100"}
		)

	def test_phone_numbers(self):
		self.assertEqual(twilio.to_e164("(416) 555-0100"), "+14165550100")
		self.assertEqual(twilio.to_e164("1 416 555 0100"), "+14165550100")
		self.assertIsNone(twilio.to_e164("555-0100"))
		self.assertEqual(twilio.mask("416-555-0100"), "••• 0100")
