"""The clinic as patients know it, and the few facts about a patient that engagement depends on."""

import frappe


def get_settings():
	return frappe.get_cached_doc("Engage Settings")


def clinic_name() -> str:
	"""The clinic, as patients know it: the site's default company."""
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
	return company or frappe.get_website_settings("app_name") or "the clinic"


def clinic_phone() -> str:
	return get_settings().clinic_phone or ""


def is_rostered_with(patient, practitioner: str | None = None) -> bool:
	"""Whether the patient can book a Family Clinic appointment (with `practitioner`, if given).

	Ophis EMR Core books appointments only for rostered patients with their own practitioner.
	"""
	if patient.enrolment_status != "Rostered" or not patient.primary_practitioner:
		return False
	return practitioner is None or practitioner == patient.primary_practitioner
