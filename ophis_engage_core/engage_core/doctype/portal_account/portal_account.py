import frappe
from frappe.model.document import Document


class PortalAccount(Document):
	def on_update(self):
		# A switched-off account cannot sign in, and its open sessions end.
		if self.user and self.has_value_changed("status"):
			enabled = 0 if self.status == "Disabled" else 1
			if frappe.db.get_value("User", self.user, "enabled") != enabled:
				frappe.db.set_value("User", self.user, "enabled", enabled)
			if not enabled:
				from frappe.sessions import clear_sessions

				clear_sessions(self.user, force=True)
