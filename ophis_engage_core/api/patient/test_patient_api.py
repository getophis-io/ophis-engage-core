import random

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime
from ophis_emr_core.emr_core.testing import make_patient

from ophis_engage_core.api.patient import auth, inbox
from ophis_engage_core.engage_core import accounts, messaging
from ophis_engage_core.engage_core.testing import login_manager, signed_in, use_connections


def make_phone() -> str:
	"""A 555 number of its own for each test: records are not rolled back between tests."""
	return f"647-555-{random.randint(0, 9999):04d}"


def token_of(url: str) -> str:
	return url.split("token=", 1)[1]


class TestPortalSignIn(IntegrationTestCase):
	def setUp(self):
		use_connections("SMS", "Email")
		frappe.conf.developer_mode = 1
		self.phone = make_phone()
		self.patient = make_patient(first_name="Robin", mobile_phone=self.phone)

	def test_a_link_in_the_first_message_signs_a_patient_in_with_a_code(self):
		link = accounts.make_link(self.patient.name, "Message")
		started = auth.start_from_link(token_of(link))
		self.assertEqual(started["sent_to"], f"••• {self.phone[-4:]}")
		self.assertEqual(started["first_name"], "Robin")
		with login_manager() as manager:
			result = auth.verify(started["challenge"], started["simulated_code"])
			self.assertTrue(result["signed_in"])
			manager.login_as.assert_called_once()
			user = manager.login_as.call_args.args[0]
		self.assertTrue(user.endswith("@patients.invalid"))
		self.assertEqual(frappe.db.get_value("User", user, "user_type"), "Website User")
		self.assertEqual(frappe.db.get_value("Portal Account", self.patient.name, "status"), "Active")

	def test_a_link_alone_is_not_enough(self):
		started = auth.start_from_link(token_of(accounts.make_link(self.patient.name, "Invitation")))
		self.assertRaises(
			accounts.SignInError,
			auth.verify,
			started["challenge"],
			"000000" if started["simulated_code"] != "000000" else "111111",
		)

	def test_an_expired_link_is_refused(self):
		link = accounts.make_link(self.patient.name, "Invitation")
		frappe.db.set_value(
			"Portal Link", {"patient": self.patient.name}, "expires_on", add_days(now_datetime(), -1)
		)
		self.assertRaises(accounts.SignInError, auth.start_from_link, token_of(link))

	def test_a_phone_number_signs_in_a_patient_who_has_an_account(self):
		accounts.ensure_account(self.patient.name)
		started = auth.request_code(f"(647) 555 {self.phone[-4:]}")
		with login_manager() as manager:
			self.assertTrue(auth.verify(started["challenge"], started["simulated_code"])["signed_in"])
			manager.login_as.assert_called_once()

	def test_an_unknown_phone_number_gets_the_same_answer_and_no_code(self):
		started = auth.request_code(make_phone().replace("647", "416"))
		self.assertEqual(set(started), {"challenge", "sent_to"})
		self.assertRaises(accounts.SignInError, auth.verify, started["challenge"], "123456")

	def test_a_phone_shared_by_two_patients_asks_who_is_signing_in(self):
		sibling = make_patient(first_name="Sam", mobile_phone=self.phone)
		accounts.ensure_account(self.patient.name)
		accounts.ensure_account(sibling.name)
		started = auth.request_code(self.phone.replace("-", ""))
		choices = auth.verify(started["challenge"], started["simulated_code"])["choose"]
		self.assertEqual(len(choices), 2)
		chosen = next(choice for choice in choices if choice["name"].startswith("Sam"))
		with login_manager():
			auth.choose(started["challenge"], chosen["index"])
			self.assertEqual(accounts.current_patient(), sibling.name)

	def test_too_many_wrong_codes_end_the_challenge(self):
		accounts.ensure_account(self.patient.name)
		started = auth.request_code(self.phone.replace("-", ""))
		wrong = "000000" if started["simulated_code"] != "000000" else "111111"
		for _attempt in range(accounts.CODE_ATTEMPTS):
			self.assertRaises(accounts.SignInError, auth.verify, started["challenge"], wrong)
		self.assertRaises(accounts.SignInError, auth.verify, started["challenge"], started["simulated_code"])

	def test_a_disabled_account_cannot_sign_in(self):
		account = accounts.ensure_account(self.patient.name)
		account.status = "Disabled"
		account.save()
		started = auth.request_code(self.phone.replace("-", ""))
		# A disabled account is not looked up at all, so no code is sent.
		self.assertNotIn("simulated_code", started)

	def test_with_sms_switched_off_every_number_gets_the_same_refusal(self):
		use_connections("Email")
		accounts.ensure_account(self.patient.name)
		self.assertRaises(accounts.SignInError, auth.request_code, self.phone)
		self.assertRaises(accounts.SignInError, auth.request_code, make_phone())


class TestInbox(IntegrationTestCase):
	def setUp(self):
		use_connections()
		self.patient = make_patient(mobile_phone=make_phone())
		self.other = make_patient(mobile_phone=make_phone())

	def test_a_patient_reads_only_their_own_messages(self):
		mine = messaging.create(self.patient.name, "General message", body="For you")
		theirs = messaging.create(self.other.name, "General message", body="Not for you")
		with signed_in(self.patient.name):
			self.assertEqual([row["name"] for row in inbox.get_messages()], [mine.name])
			self.assertEqual(inbox.get_unread_count(), 1)
			self.assertEqual(inbox.get_message(mine.name)["body"], "For you")
			self.assertEqual(inbox.get_unread_count(), 0)
			self.assertRaises(frappe.DoesNotExistError, inbox.get_message, theirs.name)
			self.assertRaises(frappe.DoesNotExistError, inbox.download, theirs.name)

	def test_staff_are_not_portal_patients(self):
		self.assertRaises(frappe.AuthenticationError, accounts.current_patient)
