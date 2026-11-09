"""The ways the clinic reaches patients and other clinics.

Each connection is an `Engage Connection` record, switched on or off and set to one of two modes.
In Simulator mode it sends nothing: it answers locally, says so in its answer and its log, and
lets the workflow around it be built, shown and tested. Production talks to the real service
with the record's credentials. A connection whose live side is not built yet (OceanMD) stops
with what it needs rather than pretend.

Every exchange is written to `Engage Connection Log`, never with a message's content.
"""

import time

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password


class ConnectionError(frappe.ValidationError):
	pass


class Connection:
	"""Base for one connection. Subclasses set the attributes and add their operations."""

	code = ""  # the Engage Connection record, e.g. "SMS"
	title = ""
	provider = ""
	description = ""
	# What the live connection needs, said when it is switched on without it.
	requires = ""
	# Whether the live side exists. Without it, Production stops instead of sending.
	live = True

	def __init__(self):
		self.settings = frappe.get_doc("Engage Connection", self.code)

	@property
	def enabled(self) -> bool:
		return bool(self.settings.enabled)

	@property
	def simulated(self) -> bool:
		return self.settings.mode == "Simulator"

	def get_secret(self) -> str | None:
		return self.settings.get_password("auth_token", raise_exception=False)

	def get_extra_settings(self) -> dict:
		return frappe.parse_json(self.settings.settings or "{}") or {}

	def run(self, action: str, simulate, live=None, reference=None) -> dict:
		"""Carry out one exchange and log its outcome. Returns the answer, with `simulated` set."""
		started = time.monotonic()
		try:
			if not self.simulated and live and self.live:
				result = live()
			elif not self.simulated:
				frappe.throw(
					_(
						"{0} is set to Production, but its live connection is not built yet. It needs: {1}"
					).format(self.title, self.requires),
					ConnectionError,
				)
			else:
				result = simulate()
			result = {**(result or {}), "simulated": self.simulated}
		except Exception as error:
			# The failed request may be rolled back, and the log entry with it, so the entry is
			# committed on its own. Tests run inside one transaction and must not commit.
			if not frappe.flags.in_test:
				frappe.db.rollback()
			self.log(action, "Failed", reference, str(error), started)
			if not frappe.flags.in_test:
				frappe.db.commit()  # nosemgrep
			raise
		message = result.get("message", "")
		self.log(
			action, "Success", reference, f"Simulator: {message}" if self.simulated else message, started
		)
		return result

	def log(self, action, status, reference, message, started):
		frappe.get_doc(
			{
				"doctype": "Engage Connection Log",
				"connection": self.code,
				"action": action,
				"status": status,
				"mode": self.settings.mode,
				"reference_doctype": reference.doctype if reference else None,
				"reference_name": reference.name if reference else None,
				"user": frappe.session.user,
				"duration_ms": int((time.monotonic() - started) * 1000),
				"message": frappe.utils.strip_html(message or "")[:500],
			}
		).insert(ignore_permissions=True)


def get_classes() -> list[type[Connection]]:
	from ophis_engage_core.connections import email, oceanmd, twilio

	return [email.Email, twilio.SMS, twilio.WhatsApp, oceanmd.OceanMD]


def get(code: str) -> Connection:
	return next(cls for cls in get_classes() if cls.code == code)()


def is_enabled(code: str) -> bool:
	return bool(frappe.db.get_value("Engage Connection", code, "enabled"))


def ensure_records():
	"""Create a switched-off, Simulator record for each connection; refresh their descriptions."""
	for cls in get_classes():
		values = {"title": cls.title, "provider": cls.provider, "description": cls.description}
		if frappe.db.exists("Engage Connection", cls.code):
			frappe.db.set_value("Engage Connection", cls.code, values)
			continue
		frappe.get_doc(
			{"doctype": "Engage Connection", "code": cls.code, "enabled": 0, "mode": "Simulator", **values}
		).insert(ignore_permissions=True)


def get_status() -> list[dict]:
	"""Each connection as the settings screen shows it, without its secret."""
	rows = {
		row.code: row
		for row in frappe.get_all(
			"Engage Connection",
			fields=[
				"code",
				"title",
				"provider",
				"description",
				"enabled",
				"mode",
				"account_sid",
				"sender",
				"endpoint",
			],
		)
	}
	result = []
	for cls in get_classes():
		row = rows.get(cls.code)
		if not row:
			continue
		row.has_secret = bool(
			get_decrypted_password("Engage Connection", cls.code, "auth_token", raise_exception=False)
		)
		row.requires = cls.requires
		row.live_built = cls.live
		result.append(row)
	return result
