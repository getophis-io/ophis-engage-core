"""Signing in: a phone number or a personal link, then a one-time code.

These are open to guests, so every answer says no more than it must: asking for a code by
phone number answers the same whether or not the number is a patient's. All are rate limited
by address.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime

from ophis_engage_core import connections
from ophis_engage_core.connections import email, twilio
from ophis_engage_core.engage_core import accounts, clinic


def check_enabled():
	if not accounts.get_settings().portal_enabled:
		frappe.throw(_("The patient portal is closed at the moment."), accounts.SignInError)


@frappe.whitelist(allow_guest=True)
def get_session() -> dict:
	"""Who is signed in, for the portal to decide what to show."""
	patient = None
	if frappe.session.user != "Guest":
		patient = frappe.db.get_value(
			"Portal Account", {"user": frappe.session.user, "status": "Active"}, "patient"
		)
	first_name = None
	if patient:
		first_name = frappe.db.get_value("Patient", patient, ["preferred_name", "first_name"], as_dict=True)
		first_name = first_name.preferred_name or first_name.first_name
	return {
		"signed_in": bool(patient),
		"first_name": first_name,
		"clinic": clinic.clinic_name(),
		"clinic_phone": accounts.get_settings().clinic_phone,
		"portal_enabled": bool(accounts.get_settings().portal_enabled),
		# A member of staff who opens the portal is told it is for patients, not signed out.
		"staff": frappe.session.user != "Guest"
		and not patient
		and frappe.get_cached_value("User", frappe.session.user, "user_type") == "System User",
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=5, seconds=15 * 60)
def request_code(phone: str) -> dict:
	"""Send a sign-in code to a mobile number, if a patient with a portal account has it on their chart."""
	check_enabled()
	if not twilio.to_e164(phone):
		frappe.throw(_("Enter your 10-digit mobile number."), accounts.SignInError)
	# Checked before the number is looked up, so the answer is the same for every number.
	if not connections.is_enabled("SMS"):
		frappe.throw(
			_("Sign-in codes cannot be sent at the moment. Please call the clinic."), accounts.SignInError
		)
	patients = accounts.find_by_phone(phone)
	result = accounts.start_challenge(patients, "SMS", phone)
	return {**result, "sent_to": twilio.mask(phone)}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=15 * 60)
def start_from_link(token: str) -> dict:
	"""Begin signing in from a personal link: send the code to the patient's phone (or email) on file."""
	check_enabled()
	link = accounts.resolve_link(token)
	if not link:
		frappe.throw(
			_("This link has expired. Sign in with your mobile number instead."), accounts.SignInError
		)
	patient = frappe.get_doc("Patient", link.patient)
	contact = accounts.get_contact(patient)
	if not contact:
		frappe.throw(
			_("We have no mobile number or email on file to send you a code. Please call the clinic."),
			accounts.SignInError,
		)
	if not link.first_used_on:
		link.db_set("first_used_on", now_datetime())
	channel, address = contact
	next_path = f"/messages/{link.message}" if link.message else None
	result = accounts.start_challenge([link.patient], channel, address, next_path)
	return {
		**result,
		"channel": channel,
		"sent_to": twilio.mask(address) if channel == "SMS" else email.mask(address),
		"first_name": patient.preferred_name or patient.first_name,
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=15 * 60)
def verify(challenge: str, code: str) -> dict:
	"""Check the code. One patient: signed in. Several on one phone: {choose: [...]} to pick from."""
	state = accounts.check_code(challenge, code)
	if len(state["patients"]) == 1:
		accounts.sign_in(state["patients"][0])
		accounts.end_challenge(challenge)
		return {"signed_in": True, "next": state.get("next")}
	names = {
		row.name: row
		for row in frappe.get_all(
			"Patient",
			filters={"name": ("in", state["patients"])},
			fields=["name", "first_name", "preferred_name", "last_name"],
		)
	}
	# Only first names and initials: whoever holds the phone already knows who shares it.
	return {
		"choose": [
			{
				"index": index,
				"name": f"{(names[patient].preferred_name or names[patient].first_name)} {names[patient].last_name[:1]}.",
			}
			for index, patient in enumerate(state["patients"])
			if patient in names
		]
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=15 * 60)
def choose(challenge: str, index: int) -> dict:
	"""After a verified code shared by several patients, sign in as the one chosen."""
	state = accounts.get_challenge(challenge)
	if not state.get("verified"):
		frappe.throw(_("Enter your code first."), accounts.SignInError)
	patients = state["patients"]
	index = int(index)
	if not 0 <= index < len(patients):
		frappe.throw(_("Choose who is signing in."), accounts.SignInError)
	accounts.sign_in(patients[index])
	accounts.end_challenge(challenge)
	return {"signed_in": True, "next": state.get("next")}
