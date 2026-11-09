from frappe.contacts.doctype.address.address import get_address_display
from frappe.model.document import Document


class ReferralClinic(Document):
	def validate(self):
		# The address itself is a Frappe Address; this keeps it readable in lists and searches.
		self.address_display = get_address_display(self.address) if self.address else None
