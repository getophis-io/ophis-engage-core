"""The signed-in patient's mailbox: their own messages, and nothing else."""

import frappe

from ophis_engage_core.engage_core import accounts
from ophis_engage_core.engage_core import inbox as mailbox


@frappe.whitelist()
def get_messages(start: int = 0) -> list[dict]:
	"""The patient's messages, newest first, 200 at a time."""
	return mailbox.list_messages(accounts.current_patient(), start=int(start or 0))


@frappe.whitelist()
def get_message(name: str) -> dict:
	"""One message, marked read. With a protected attachment, how to open it."""
	return mailbox.read_message(accounts.current_patient(), name)


@frappe.whitelist()
def download(name: str):
	"""The message's attachment, as stored: a protected one stays locked with the patient's password."""
	file_name, content = mailbox.get_attachment(accounts.current_patient(), name)
	frappe.local.response.filename = file_name
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"


@frappe.whitelist()
def get_unread_count() -> int:
	return mailbox.unread_count(accounts.current_patient())
