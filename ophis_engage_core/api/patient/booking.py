"""Asking for an appointment, as the signed-in patient (rules in engage_core/booking.py)."""

import frappe

from ophis_engage_core.engage_core import accounts
from ophis_engage_core.engage_core import booking as rules


def get_patient():
	return frappe.get_doc("Patient", accounts.current_patient())


@frappe.whitelist()
def get_options() -> dict:
	"""Whether this patient can ask for an appointment, with whom, and for what."""
	return rules.get_options(get_patient())


@frappe.whitelist()
def get_times(appointment_type: str | None = None, start: str | None = None) -> dict:
	"""A week of open times with the patient's practitioner, from `start` (today by default)."""
	return rules.get_patient_times(get_patient(), appointment_type, start)


@frappe.whitelist(methods=["POST"])
def request(
	date: str,
	time: str,
	appointment_type: str | None = None,
	reason_for_visit: str | None = None,
	alternatives: str | None = None,
) -> dict:
	doc = rules.create_request(get_patient(), date, time, appointment_type, reason_for_visit, alternatives)
	return {"name": doc.name}


@frappe.whitelist()
def get_requests() -> list[dict]:
	return rules.get_requests(accounts.current_patient())


@frappe.whitelist(methods=["POST"])
def withdraw(name: str):
	rules.withdraw(accounts.current_patient(), name)
