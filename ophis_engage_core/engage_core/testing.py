"""Helpers for tests, here and in apps built on the core. Patients, practitioners and appointments
come from Ophis EMR Core's factories (`ophis_emr_core.emr_core.testing`)."""

import io
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import add_days, getdate, today

from ophis_engage_core.engage_core import accounts


def use_connections(*codes: str, mode: str = "Simulator"):
	"""Switch these connections on, in `mode`, and every other one off."""
	for code in frappe.get_all("Engage Connection", pluck="name"):
		frappe.db.set_value("Engage Connection", code, {"enabled": int(code in codes), "mode": mode})


def set_settings(**values):
	settings = frappe.get_single("Engage Settings")
	settings.update(values)
	settings.save(ignore_permissions=True)
	frappe.clear_document_cache("Engage Settings", "Engage Settings")


def next_weekday(weekday: int = 0):
	"""The next date, after today, that falls on `weekday` (Monday is 0)."""
	day = add_days(today(), 1)
	while getdate(day).weekday() != weekday:
		day = add_days(day, 1)
	return getdate(day)


def blank_pdf() -> bytes:
	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=200, height=200)
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()


@contextmanager
def login_manager():
	"""A stand-in for the request's login manager, which tests do not have. Yields the mock."""
	manager = MagicMock()
	manager.login_as.side_effect = frappe.set_user
	with patch.object(frappe.local, "login_manager", manager, create=True):
		try:
			yield manager
		finally:
			frappe.set_user("Administrator")


@contextmanager
def signed_in(patient: str):
	"""Run as the patient's portal login."""
	with login_manager():
		accounts.sign_in(patient)
		yield frappe.session.user
