"""Email, through the site's outgoing email account (Frappe's email queue)."""

import frappe
from frappe import _

from ophis_engage_core.connections import Connection


def mask(address: str | None) -> str:
	"""What staff see of an address a message went to: its first letter and its domain."""
	if not address or "@" not in address:
		return ""
	local, domain = address.split("@", 1)
	return f"{local[:1]}•••@{domain}"


class Email(Connection):
	code = "Email"
	title = "Email"
	provider = "Frappe email"
	description = "Email to patients and to clinics, sent by the site's default outgoing email account."
	requires = "an outgoing Email Account, set as the default."

	def send(
		self,
		recipient: str,
		subject: str,
		message: str,
		attachments: list[dict] | None = None,
		reference=None,
	) -> dict:
		def simulate():
			return {"reference": None, "message": _("not sent to {0}").format(mask(recipient))}

		def live():
			frappe.sendmail(
				recipients=[recipient],
				subject=subject,
				message=message,
				attachments=attachments or [],
				reference_doctype=reference.doctype if reference else None,
				reference_name=reference.name if reference else None,
				# A sender other than the default account, when the record names one.
				sender=self.settings.sender or None,
			)
			return {"reference": None, "message": _("queued for {0}").format(mask(recipient))}

		return self.run("Send", simulate, live, reference)
