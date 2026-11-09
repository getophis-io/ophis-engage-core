import frappe
from frappe import _
from frappe.model.document import Document


class MessageTemplate(Document):
	def validate(self):
		if not self.has_attachment:
			self.password_protected = 0
		self.validate_one_per_event()

	def validate_one_per_event(self):
		"""An event sends one message, so only one enabled template answers it. Manual ones are chosen by staff."""
		if self.event == "Manual" or not self.enabled:
			return
		other = frappe.db.get_value(
			"Message Template", {"event": self.event, "enabled": 1, "name": ("!=", self.name)}, "name"
		)
		if other:
			frappe.throw(
				_("{0} is already sent when: {1}. Switch it off first.").format(other, _(self.event))
			)
