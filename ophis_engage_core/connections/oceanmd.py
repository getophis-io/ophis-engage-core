"""OceanMD eReferral: finding clinics on Ocean, and sending them referrals.

TODO: the live connection. Ocean's partner API (the eReferral network's directory search and
referral submission) needs a partner agreement with OceanMD, a client ID and secret, and the
clinic's Ocean site number. Until then only the Simulator works, with the made-up directory
below: every clinic in it is named as simulated, and so is every referral "sent" to one.
"""

import frappe

from ophis_engage_core.connections import Connection

# Invented clinics, so the referral screen can be tried. Not real places, numbers or addresses.
SIMULATED_DIRECTORY = [
	{
		"clinic_name": "Simulated Lakeshore Cardiology",
		"specialty": "Cardiology",
		"address_line1": "100 Example Street, Suite 200",
		"city": "Toronto",
		"province": "ON",
		"postal_code": "M5V 0A1",
		"phone": "416-555-0141",
		"fax": "416-555-0142",
		"ocean_reference": "SIM-CARD-1001",
	},
	{
		"clinic_name": "Simulated Waterfront Dermatology",
		"specialty": "Dermatology",
		"address_line1": "200 Sample Avenue",
		"city": "Toronto",
		"province": "ON",
		"postal_code": "M5J 0B2",
		"phone": "416-555-0151",
		"fax": "416-555-0152",
		"ocean_reference": "SIM-DERM-1002",
	},
	{
		"clinic_name": "Simulated Riverside Orthopaedics",
		"specialty": "Orthopaedic Surgery",
		"address_line1": "300 Placeholder Road",
		"city": "Mississauga",
		"province": "ON",
		"postal_code": "L5B 0C3",
		"phone": "905-555-0161",
		"fax": "905-555-0162",
		"ocean_reference": "SIM-ORTH-1003",
	},
	{
		"clinic_name": "Simulated Northgate Endocrinology",
		"specialty": "Endocrinology",
		"address_line1": "400 Fictional Boulevard",
		"city": "Ottawa",
		"province": "ON",
		"postal_code": "K1P 0D4",
		"phone": "613-555-0171",
		"fax": "613-555-0172",
		"ocean_reference": "SIM-ENDO-1004",
	},
	{
		"clinic_name": "Simulated Bayview Mental Health Centre",
		"specialty": "Psychiatry",
		"address_line1": "500 Demo Crescent",
		"city": "Hamilton",
		"province": "ON",
		"postal_code": "L8P 0E5",
		"phone": "905-555-0181",
		"fax": "905-555-0182",
		"ocean_reference": "SIM-PSYC-1005",
	},
]

SEARCHED_FIELDS = ("clinic_name", "specialty", "address_line1", "city", "postal_code")


def matches(clinic: dict, query: str) -> bool:
	"""Whether every word of the query is somewhere in the clinic's name, specialty or address."""
	text = " ".join(str(clinic.get(field) or "") for field in SEARCHED_FIELDS).lower().replace(" ", "")
	return all(word.replace(" ", "") in text for word in query.lower().split())


class OceanMD(Connection):
	code = "OceanMD"
	title = "OceanMD eReferral"
	provider = "OceanMD"
	description = "Finding clinics on the Ocean eReferral network and sending them referrals."
	requires = "an OceanMD partner agreement, its client ID and secret, and the clinic's Ocean site number."
	live = False

	def search_clinics(self, query: str) -> list[dict]:
		"""Clinics on Ocean whose name, specialty or address matches the query."""

		def simulate():
			found = [dict(clinic, simulated=1) for clinic in SIMULATED_DIRECTORY if matches(clinic, query)]
			return {"clinics": found, "message": f"{len(found)} made-up clinics"}

		return self.run("Search clinics", simulate)["clinics"]

	def get_clinic(self, ocean_reference: str) -> dict | None:
		"""One clinic's listing on Ocean, by its reference, or None if it is not listed."""

		def simulate():
			found = next(
				(clinic for clinic in SIMULATED_DIRECTORY if clinic["ocean_reference"] == ocean_reference),
				None,
			)
			return {
				"clinic": dict(found, simulated=1) if found else None,
				"message": "a made-up clinic" if found else "not listed",
			}

		return self.run("Get clinic", simulate)["clinic"]

	def send_referral(self, ereferral, letter: bytes) -> dict:
		"""Submit a referral to the clinic's Ocean service. Returns {reference, message, simulated}."""

		def simulate():
			return {
				"reference": f"SIM-{frappe.generate_hash(length=8).upper()}",
				"message": f"not sent to {ereferral.clinic_name}",
			}

		return self.run("Send referral", simulate, reference=ereferral)
