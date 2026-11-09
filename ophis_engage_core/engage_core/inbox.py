"""A patient's mailbox: their own messages, and nothing else.

Each function takes the patient whose mailbox it is from the caller. The patient API passes the
patient signed in (`accounts.current_patient()`), never one named in a request.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime
from ophis_emr_core.emr_core import phi

from ophis_engage_core.engage_core import messaging

PAGE_LENGTH = 200


def get_own(patient: str, name: str):
	"""The patient's own message. Someone else's is answered exactly as one that does not exist."""
	message = frappe.get_doc("Patient Message", name) if frappe.db.exists("Patient Message", name) else None
	if not message or message.patient != patient:
		frappe.throw(_("Message not found."), frappe.DoesNotExistError)
	return message


def list_messages(patient: str, start: int = 0, limit: int = PAGE_LENGTH) -> list[dict]:
	"""The patient's messages, newest first: subject, date, read or not, whether there is a document."""
	rows = frappe.get_all(
		"Patient Message",
		filters={"patient": patient},
		fields=["name", "subject", "creation", "read_on", "attachment"],
		order_by="creation desc",
		start=start,
		limit=min(limit, PAGE_LENGTH),
	)
	return [
		{
			"name": row.name,
			"subject": phi.decrypt(row.subject),
			"date": row.creation,
			"unread": not row.read_on,
			"has_attachment": bool(row.attachment),
		}
		for row in rows
	]


def read_message(patient: str, name: str) -> dict:
	"""One of the patient's messages, marked read the first time it is opened."""
	message = get_own(patient, name)
	if not message.read_on:
		message.db_set("read_on", now_datetime(), update_modified=False)
	return {
		"name": message.name,
		"subject": message.subject,
		"body": message.body,
		"date": message.creation,
		"read_on": message.read_on,
		"attachment": message.attachment_name if message.attachment else None,
		"password_hint": messaging.password_hint(frappe.get_doc("Patient", patient))
		if message.attachment and message.password_protected
		else None,
	}


def get_attachment(patient: str, name: str) -> tuple[str, bytes]:
	"""(file name, content) of the message's attachment, as stored: a protected one stays locked."""
	message = get_own(patient, name)
	if not message.attachment:
		frappe.throw(_("This message has no attachment."), frappe.DoesNotExistError)
	file = frappe.get_doc("File", {"file_url": message.attachment, "attached_to_name": message.name})
	return message.attachment_name or file.file_name, file.get_content(encodings=[])


def unread_count(patient: str) -> int:
	return frappe.db.count("Patient Message", {"patient": patient, "read_on": ("is", "not set")})
