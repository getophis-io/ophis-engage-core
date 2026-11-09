"""SMS and WhatsApp, through Frappe's SMS Settings set up for Twilio.

Frappe's SMS Settings is a generic HTTP gateway; Twilio's Messages API
(https://www.twilio.com/docs/messaging/api/message-resource) fits it as a POST of To, From and
Body with the account as basic authentication. `configure_sms_settings` fills it in from an
account SID, auth token and number. SMS goes out through Frappe's own sender. WhatsApp goes
through the same gateway and account, with both numbers prefixed `whatsapp:` and the
WhatsApp sender kept on its `Engage Connection` record.

Neither ever carries sensitive content or attachments: a text says there is a message, and
gives the link to read it in the portal (engage_core/messaging.py decides the wording).
"""

import base64
import re

import frappe
from frappe import _

from ophis_engage_core.connections import Connection, ConnectionError

API = "https://api.twilio.com/2010-04-01/Accounts/{account}/Messages.json"


def to_e164(phone: str | None) -> str | None:
	"""A Canadian number as +1XXXXXXXXXX, or None if it is not ten digits (with or without the 1)."""
	digits = re.sub(r"\D", "", phone or "")
	if len(digits) == 11 and digits.startswith("1"):
		digits = digits[1:]
	return f"+1{digits}" if len(digits) == 10 else None


def mask(phone: str | None) -> str:
	"""What staff see of a number a message went to: its last four digits."""
	digits = re.sub(r"\D", "", phone or "")
	return f"••• {digits[-4:]}" if digits else ""


def configure_sms_settings(account_sid: str, auth_token: str, number: str):
	"""Set Frappe's SMS Settings up to send through this Twilio account and number."""
	sender = to_e164(number)
	if not (account_sid and auth_token and sender):
		frappe.throw(_("Enter the Twilio account SID, its auth token and a Canadian sending number."))
	credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
	settings = frappe.get_doc("SMS Settings")
	settings.update(
		{
			"sms_gateway_url": API.format(account=account_sid),
			"message_parameter": "Body",
			"receiver_parameter": "To",
			"use_post": 1,
		}
	)
	settings.set(
		"parameters",
		[
			{"parameter": "From", "value": sender, "header": 0},
			{"parameter": "Authorization", "value": f"Basic {credentials}", "header": 1},
		],
	)
	settings.save(ignore_permissions=True)


def is_configured() -> bool:
	return bool(frappe.db.get_single_value("SMS Settings", "sms_gateway_url"))


class Texts(Connection):
	provider = "Twilio, through Frappe's SMS Settings"
	requires = "Frappe's SMS Settings set up for a Twilio account (`configure_sms_settings` does this)."
	prefix = ""

	def send(self, phone: str, body: str, reference=None) -> dict:
		to = to_e164(phone)
		if not to:
			frappe.throw(_("{0} is not a Canadian phone number.").format(mask(phone)), ConnectionError)

		def simulate():
			return {
				"reference": f"SIM-{frappe.generate_hash(length=10)}",
				"message": _("not sent to {0}").format(mask(to)),
			}

		def live():
			if not is_configured():
				frappe.throw(_("{0} needs {1}").format(self.title, self.requires), ConnectionError)
			self.send_live(to, body)
			return {"reference": None, "message": _("sent to {0}").format(mask(to))}

		return self.run("Send", simulate, live, reference)

	def send_live(self, to: str, body: str):
		raise NotImplementedError


class SMS(Texts):
	code = "SMS"
	title = "SMS"
	description = "Text messages to patients' mobile phones, through Frappe's SMS Settings (Twilio). Also carries sign-in codes."

	def send_live(self, to: str, body: str):
		from frappe.core.doctype.sms_settings.sms_settings import _send_sms

		# Sent on the clinic's behalf, to patients who have no login of their own, so without
		# the SMS Settings role check that `send_sms` makes of whoever is signed in.
		_send_sms([to], body, success_msg=False)


class WhatsApp(Texts):
	code = "WhatsApp"
	title = "WhatsApp"
	prefix = "whatsapp:"
	description = (
		"WhatsApp messages to patients' mobile phones, through the Twilio account in SMS Settings and a "
		"WhatsApp sender. Messages the clinic starts need templates approved by WhatsApp."
	)
	requires = "Frappe's SMS Settings set up for a Twilio account, and a WhatsApp sender on this record."

	def send_live(self, to: str, body: str):
		from frappe.core.doctype.sms_settings.sms_settings import get_headers, send_request

		sender = to_e164(self.settings.sender)
		if not sender:
			frappe.throw(_("Set the WhatsApp sender's number on the WhatsApp connection."), ConnectionError)
		gateway = frappe.get_doc("SMS Settings")
		params = {
			gateway.message_parameter: body,
			gateway.receiver_parameter: f"{self.prefix}{to}",
			"From": f"{self.prefix}{sender}",
		}
		send_request(gateway.sms_gateway_url, params, get_headers(gateway), use_post=True)
