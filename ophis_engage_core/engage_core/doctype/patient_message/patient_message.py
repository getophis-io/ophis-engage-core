from frappe.model.document import Document
from ophis_emr_core.emr_core.phi import EncryptedFieldsMixin


class PatientMessage(EncryptedFieldsMixin, Document):
	"""Its subject, body and text are encrypted (the `phi_encrypted_fields` hook in hooks.py)."""
