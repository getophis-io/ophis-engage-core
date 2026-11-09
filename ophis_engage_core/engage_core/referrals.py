"""eReferrals: a `Patient Referral` sent on to another clinic, through OceanMD or by email.

An app's referral screen offers sending when eReferrals are switched on in Engage Settings
(`get_service`, which the core answers to the `ereferral_service` hook). There the clinic is
found by name, specialty or address, in the clinic directory (`Referral Clinic`, its address
in Frappe's `Address`) and, where OceanMD is switched on, on Ocean. A clinic on Ocean is sent
the referral there; any other by email, to the address on its Address. A clinic that cannot
be found is added with its email address and sent the referral by email.

The letter is the `Referral Letter` print format of the `Patient Referral`, as a PDF.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from ophis_engage_core import connections
from ophis_engage_core.connections.oceanmd import matches
from ophis_engage_core.engage_core import messaging
from ophis_engage_core.engage_core.clinic import clinic_name as sending_clinic_name
from ophis_engage_core.engage_core.clinic import clinic_phone

LETTER_FORMAT = "Referral Letter"
# A referral in these states is not sent anywhere.
CLOSED_STATUSES = ("Cancelled", "Entered in Error")
EREFERRAL_FIELDS = [
	"name",
	"referral",
	"clinic",
	"clinic_name",
	"method",
	"status",
	"simulated",
	"recipient_email",
	"sent_on",
	"external_reference",
	"detail",
]


def is_enabled() -> bool:
	return bool(frappe.db.get_single_value("Engage Settings", "ereferrals_enabled"))


def get_service(patient: str) -> dict:
	"""What an app's referral screen shows: whether to offer sending, and what was sent."""
	enabled = is_enabled() and frappe.has_permission("eReferral", "read")
	by_referral = {}
	if enabled:
		for row in frappe.get_list(
			"eReferral",
			filters={"patient": patient},
			fields=EREFERRAL_FIELDS,
			order_by="creation desc",
			limit_page_length=0,
		):
			by_referral.setdefault(row.referral, []).append(row)
	return {
		"enabled": enabled,
		"can_send": enabled and frappe.has_permission("eReferral", "create"),
		"oceanmd": connections.is_enabled("OceanMD"),
		"email": connections.is_enabled("Email"),
		"by_referral": by_referral,
	}


def describe_clinic(clinic) -> dict:
	return {
		"name": clinic.name,
		"clinic_name": clinic.clinic_name,
		"specialty": clinic.specialty,
		"address": clinic.address_display,
		"email": clinic.email,
		"phone": clinic.phone,
		"fax": clinic.fax,
		"ocean_reference": clinic.ocean_reference,
		"simulated": clinic.simulated,
		"source": clinic.source,
	}


def search_directory(query: str, limit: int = 20) -> list[dict]:
	"""Active directory clinics whose every query word is in their name, specialty or address."""
	clinics = frappe.get_all(
		"Referral Clinic",
		filters={"active": 1},
		fields=[
			"name",
			"clinic_name",
			"specialty",
			"address",
			"address_display",
			"email",
			"phone",
			"fax",
			"ocean_reference",
			"simulated",
			"source",
		],
		order_by="clinic_name asc",
	)
	addresses = {
		row.name: row
		for row in frappe.get_all(
			"Address",
			filters={"name": ("in", [clinic.address for clinic in clinics if clinic.address])},
			fields=["name", "address_line1", "address_line2", "city", "pincode"],
		)
	}
	found = []
	for clinic in clinics:
		address = addresses.get(clinic.address) or {}
		searchable = {
			"clinic_name": clinic.clinic_name,
			"specialty": clinic.specialty,
			"address_line1": " ".join(
				filter(None, [address.get("address_line1"), address.get("address_line2")])
			),
			"city": address.get("city"),
			"postal_code": address.get("pincode"),
		}
		if matches(searchable, query):
			found.append(describe_clinic(clinic))
		if len(found) >= limit:
			break
	return found


def search(query: str) -> dict:
	"""{directory, ocean}: matches in the clinic directory, and on Ocean where it is switched on.

	An Ocean clinic already in the directory is listed once, from the directory.
	"""
	query = (query or "").strip()
	if len(query) < 2:
		return {"directory": [], "ocean": []}
	directory = search_directory(query)
	ocean = []
	if connections.is_enabled("OceanMD"):
		known = {row["ocean_reference"] for row in directory if row["ocean_reference"]}
		ocean = [
			clinic
			for clinic in connections.get("OceanMD").search_clinics(query)
			if clinic["ocean_reference"] not in known
		]
	return {"directory": directory, "ocean": ocean}


def save_clinic(values: dict, name: str | None = None):
	"""Add or change a directory clinic and its Frappe Address. Returns the Referral Clinic."""
	clinic = frappe.get_doc("Referral Clinic", name) if name else frappe.new_doc("Referral Clinic")
	for field in ("clinic_name", "specialty", "active"):
		if field in values:
			clinic.set(field, values[field])
	if not name:
		clinic.update(
			{
				"source": values.get("source") or "Manual",
				"ocean_reference": values.get("ocean_reference"),
				"simulated": values.get("simulated") or 0,
			}
		)
	if not clinic.clinic_name:
		frappe.throw(_("Enter the clinic's name."))
	if name:
		clinic.save()
	else:
		clinic.insert()

	address = frappe.get_doc("Address", clinic.address) if clinic.address else frappe.new_doc("Address")
	address.update(
		{
			"address_title": clinic.clinic_name,
			"address_type": address.address_type or "Office",
			"address_line1": values.get("address_line1", address.address_line1),
			"address_line2": values.get("address_line2", address.address_line2),
			"city": values.get("city", address.city),
			"state": values.get("province", address.state),
			"pincode": values.get("postal_code", address.pincode),
			"country": "Canada",
			"email_id": values.get("email", address.email_id),
			"phone": values.get("phone", address.phone),
			"fax": values.get("fax", address.fax),
		}
	)
	if not any(
		link.link_doctype == "Referral Clinic" and link.link_name == clinic.name for link in address.links
	):
		address.append("links", {"link_doctype": "Referral Clinic", "link_name": clinic.name})
	if address.is_new():
		address.insert(ignore_permissions=True)
	else:
		address.save(ignore_permissions=True)
	if clinic.address != address.name:
		clinic.address = address.name
	# Saved again so the fields it takes from its Address are current.
	clinic.save()
	return clinic


def get_ocean_clinic(ocean_reference: str):
	"""The directory clinic for an Ocean listing, added from Ocean's answer the first time it is used."""
	name = frappe.db.get_value("Referral Clinic", {"ocean_reference": ocean_reference}, "name")
	if name:
		return frappe.get_doc("Referral Clinic", name)
	listing = connections.get("OceanMD").get_clinic(ocean_reference)
	if not listing:
		frappe.throw(_("That clinic is no longer listed on OceanMD."))
	return save_clinic({**listing, "source": "OceanMD"})


def render_letter(referral) -> bytes:
	return frappe.get_print(
		"Patient Referral", referral.name, print_format=LETTER_FORMAT, as_pdf=True, no_letterhead=1
	)


def send(
	referral: str,
	clinic: str | None = None,
	ocean_reference: str | None = None,
	new_clinic: dict | None = None,
):
	"""Send a referral to a clinic: a directory clinic, an Ocean listing, or a new one by email.

	Returns the eReferral. A failure to send is recorded on it (status Failed) as well as raised.
	"""
	if not is_enabled():
		frappe.throw(_("eReferrals are not switched on for this clinic."))
	frappe.has_permission("eReferral", "create", throw=True)
	referral_doc = frappe.get_doc("Patient Referral", referral)
	referral_doc.check_permission("write")
	if referral_doc.status in CLOSED_STATUSES:
		frappe.throw(_("A referral that is {0} is not sent.").format(referral_doc.status))

	if new_clinic:
		if not (new_clinic.get("email") or "").strip():
			frappe.throw(_("Enter the clinic's email address to send the referral by email."))
		clinic_doc = save_clinic(new_clinic)
	elif ocean_reference:
		clinic_doc = get_ocean_clinic(ocean_reference)
	elif clinic:
		clinic_doc = frappe.get_doc("Referral Clinic", clinic)
	else:
		frappe.throw(_("Choose the clinic to send the referral to."))

	method = choose_method(clinic_doc)
	ereferral = frappe.get_doc(
		{
			"doctype": "eReferral",
			"referral": referral_doc.name,
			"clinic": clinic_doc.name,
			"method": method,
			"status": "Sending",
			"recipient_email": clinic_doc.email if method == "Email" else None,
			"sent_by": frappe.session.user,
		}
	).insert()

	letter = render_letter(referral_doc)
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"Referral {referral_doc.name}.pdf",
			"content": letter,
			"is_private": 1,
			"attached_to_doctype": "eReferral",
			"attached_to_name": ereferral.name,
			"attached_to_field": "letter",
		}
	).insert(ignore_permissions=True)
	ereferral.letter = file.file_url

	try:
		if method == "OceanMD":
			result = connections.get("OceanMD").send_referral(ereferral, letter)
		else:
			result = send_email(ereferral, referral_doc, clinic_doc, file)
	except Exception as error:
		ereferral.update({"status": "Failed", "detail": frappe.utils.strip_html(str(error))[:300]})
		ereferral.save(ignore_permissions=True)
		raise

	ereferral.update(
		{
			"status": "Sent",
			"sent_on": now_datetime(),
			"simulated": 1 if result.get("simulated") else 0,
			"external_reference": result.get("reference"),
			"detail": _("Simulator: nothing left the clinic.") if result.get("simulated") else None,
		}
	)
	ereferral.save(ignore_permissions=True)
	mark_sent(referral_doc, clinic_doc)

	template = messaging.get_template("Referral Sent")
	if template:
		messaging.create(
			referral_doc.patient,
			template,
			context={
				"referral": frappe._dict(
					clinic=clinic_doc.clinic_name, specialty=referral_doc.specialty or clinic_doc.specialty
				)
			},
			reference=ereferral,
		)
	return ereferral


def choose_method(clinic) -> str:
	if clinic.simulated and not connections.get("OceanMD").simulated:
		frappe.throw(
			_("{0} is a made-up clinic from the OceanMD Simulator. A real referral cannot go to it.").format(
				clinic.clinic_name
			)
		)
	if clinic.ocean_reference and connections.is_enabled("OceanMD"):
		return "OceanMD"
	if clinic.email:
		if not connections.is_enabled("Email"):
			frappe.throw(_("Email is switched off in Engage Settings, so this referral cannot be emailed."))
		return "Email"
	frappe.throw(
		_(
			"{0} is not on OceanMD and has no email address. Add its email to the directory, or fax the letter."
		).format(clinic.clinic_name)
	)


def send_email(ereferral, referral, clinic, file) -> dict:
	"""The letter to the clinic's email address. The patient is named only in the attached letter."""
	sender_clinic = sending_clinic_name()
	phone = clinic_phone()
	subject = _("Referral from {0} ({1})").format(sender_clinic, ereferral.name)
	message = "".join(
		[
			f"<p>{_('Please find attached a {0} referral from {1}.').format((referral.priority or 'routine').lower(), sender_clinic)}</p>",
			f"<p>{_('Specialty')}: {frappe.utils.escape_html(referral.specialty or clinic.specialty or '-')}</p>",
			f"<p>{_('Questions about it: {0}.').format(phone)}</p>" if phone else "",
			f"<p>{_('Reference')}: {ereferral.name}</p>",
		]
	)
	return connections.get("Email").send(
		clinic.email,
		subject,
		message,
		attachments=[{"fname": file.file_name, "fcontent": file.get_content(encodings=[])}],
		reference=ereferral,
	)


def mark_sent(referral, clinic):
	"""The referral is now Sent, and names the clinic where it did not already."""
	if referral.status == "Draft":
		referral.status = "Sent"
	for field, value in (
		("referred_to_clinic", clinic.clinic_name),
		("referred_to_phone", clinic.phone),
		("referred_to_fax", clinic.fax),
	):
		if not referral.get(field) and value:
			referral.set(field, value)
	referral.save()
