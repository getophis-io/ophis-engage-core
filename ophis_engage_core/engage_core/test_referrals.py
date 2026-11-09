from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from ophis_emr_core.emr_core.testing import make_patient

from ophis_engage_core.engage_core import referrals
from ophis_engage_core.engage_core.testing import blank_pdf, set_settings, use_connections

# wkhtmltopdf is slow and beside the point here; the letter's print format is checked on its own.
FAKE_LETTER = blank_pdf()


def make_referral(patient: str, **values):
	return frappe.get_doc(
		{
			"doctype": "Patient Referral",
			"patient": patient,
			"referred_to": "Dr. Example",
			"specialty": "Cardiology",
			"status": "Draft",
			"referral_date": frappe.utils.today(),
			"priority": "Routine",
			"reason": "Palpitations",
			**values,
		}
	).insert(ignore_permissions=True)


class TestReferrals(IntegrationTestCase):
	def setUp(self):
		use_connections("Email", "OceanMD", "SMS")
		set_settings(ereferrals_enabled=1)
		self.patient = make_patient(mobile_phone="416-555-0177")
		self.referral = make_referral(self.patient.name)
		self.letter = patch.object(referrals, "render_letter", return_value=FAKE_LETTER)
		self.letter.start()
		self.addCleanup(self.letter.stop)

	def test_the_directory_is_searched_by_address(self):
		clinic = referrals.save_clinic(
			{
				"clinic_name": "Queen Street Clinic",
				"specialty": "Cardiology",
				"address_line1": "12 Queen Street West",
				"city": "Toronto",
				"province": "ON",
				"postal_code": "M5H 2M9",
				"email": "clinic@example.com",
			}
		)
		self.assertTrue(
			frappe.db.exists(
				"Dynamic Link",
				{"parenttype": "Address", "link_doctype": "Referral Clinic", "link_name": clinic.name},
			)
		)
		self.assertEqual(clinic.email, "clinic@example.com")
		found = referrals.search("queen st")["directory"] or referrals.search("12 queen")["directory"]
		self.assertIn(clinic.name, [row["name"] for row in found])
		self.assertIn(clinic.name, [row["name"] for row in referrals.search("M5H")["directory"]])

	def test_ocean_answers_are_simulated_and_say_so(self):
		ocean = referrals.search("cardiology")["ocean"]
		self.assertTrue(ocean)
		self.assertTrue(
			all(clinic["simulated"] and clinic["clinic_name"].startswith("Simulated") for clinic in ocean)
		)

	def test_a_clinic_not_found_is_sent_the_referral_by_email(self):
		ereferral = referrals.send(
			self.referral.name,
			new_clinic={
				"clinic_name": "Elm Heart Centre",
				"email": "heart@example.com",
				"address_line1": "5 Elm Road",
				"city": "Guelph",
				"province": "ON",
			},
		)
		self.assertEqual(
			(ereferral.method, ereferral.status, ereferral.recipient_email),
			("Email", "Sent", "heart@example.com"),
		)
		self.assertTrue(ereferral.simulated)
		self.assertTrue(ereferral.letter)
		self.referral.reload()
		self.assertEqual(
			(self.referral.status, self.referral.referred_to_clinic), ("Sent", "Elm Heart Centre")
		)
		self.assertTrue(
			frappe.db.exists("Patient Message", {"reference_name": ereferral.name, "event": "Referral Sent"})
		)

	def test_a_clinic_found_on_ocean_is_sent_the_referral_there(self):
		listing = referrals.search("dermatology")["ocean"][0]
		ereferral = referrals.send(self.referral.name, ocean_reference=listing["ocean_reference"])
		self.assertEqual((ereferral.method, ereferral.status), ("OceanMD", "Sent"))
		self.assertTrue(ereferral.external_reference.startswith("SIM-"))
		self.assertTrue(frappe.db.get_value("Referral Clinic", ereferral.clinic, "simulated"))

	def test_a_new_clinic_needs_an_email_address(self):
		self.assertRaises(
			frappe.ValidationError,
			referrals.send,
			self.referral.name,
			new_clinic={"clinic_name": "No Email Clinic"},
		)

	def test_a_closed_referral_is_not_sent(self):
		self.referral.db_set("status", "Entered in Error")
		self.assertRaises(
			frappe.ValidationError,
			referrals.send,
			self.referral.name,
			new_clinic={"clinic_name": "X", "email": "x@example.com"},
		)

	def test_nothing_is_offered_when_switched_off(self):
		set_settings(ereferrals_enabled=0)
		self.assertFalse(referrals.get_service(self.patient.name)["enabled"])
		self.assertRaises(
			frappe.ValidationError,
			referrals.send,
			self.referral.name,
			new_clinic={"clinic_name": "X", "email": "x@example.com"},
		)

	def test_the_service_lists_what_was_sent_for_each_referral(self):
		ereferral = referrals.send(
			self.referral.name,
			new_clinic={
				"clinic_name": "Elm",
				"email": "elm@example.com",
				"address_line1": "1 Elm",
				"city": "Guelph",
			},
		)
		service = referrals.get_service(self.patient.name)
		self.assertTrue(service["enabled"] and service["can_send"])
		self.assertEqual([row.name for row in service["by_referral"][self.referral.name]], [ereferral.name])

	def test_the_letter_names_the_patient_and_the_reason(self):
		self.letter.stop()
		html = frappe.get_print("Patient Referral", self.referral.name, print_format=referrals.LETTER_FORMAT)
		self.letter.start()
		self.assertIn(self.patient.patient_name, html)
		self.assertIn("Palpitations", html)
		self.assertIn(self.patient.health_card_number, html)
