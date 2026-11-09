"""Patients' sign-ins to the portal.

A patient has one `Portal Account`, made the first time the clinic writes to them or invites
them. Signing in always ends with a one-time code sent to the mobile phone (or, without one,
the email) on their chart, so knowing a phone number or holding a link is never
enough. There are two ways to start:

- **A personal link** (`Portal Link`), in an invitation or in any message the clinic sends.
  It names the patient; the code goes to their phone. This is how a patient who was never
  invited still gets in from the very first message.
- **Their phone number**, typed on the sign-in screen, for a patient who has an account. The
  code goes to that number only if a patient with an account has it on their chart; the
  answer is the same either way, so the screen cannot be used to find out who is a patient.

Each account has a Frappe Website User, named with an address that cannot receive mail
(`<account>@patients.invalid`), so no patient's real email or phone lands in the user table.
"""

import hashlib
import hmac
import secrets

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, get_url, now_datetime
from ophis_emr_core.emr_core import phi

from ophis_engage_core.engage_core import clinic

LOGIN_DOMAIN = "patients.invalid"
CODE_DIGITS = 6
CODE_ATTEMPTS = 5
CHALLENGE_PREFIX = "engage:sign-in:"


class SignInError(frappe.ValidationError):
	pass


get_settings = clinic.get_settings


def portal_url(path: str = "") -> str:
	"""An address in the patient portal: Engage Settings' portal address, or this site's /portal.

	The portal is the app built on the core; it serves `/welcome?token=…` (a personal link) and
	`/messages/<name>` (a message, after signing in).
	"""
	base = (get_settings().portal_url or f"{get_url()}/portal").rstrip("/")
	return f"{base}{path}"


def ensure_account(patient: str):
	"""The patient's portal account, made (Invited) if they have none."""
	if frappe.db.exists("Portal Account", patient):
		return frappe.get_doc("Portal Account", patient)
	return frappe.get_doc(
		{"doctype": "Portal Account", "patient": patient, "status": "Invited", "invited_on": now_datetime()}
	).insert(ignore_permissions=True)


def hash_token(token: str) -> str:
	return hashlib.sha256(token.encode()).hexdigest()


def make_link(patient: str, purpose: str, message: str | None = None) -> str:
	"""A personal sign-in link for the patient, opening `message` if given. Only its hash is kept."""
	token = secrets.token_urlsafe(32)
	frappe.get_doc(
		{
			"doctype": "Portal Link",
			"patient": patient,
			"token_hash": hash_token(token),
			"purpose": purpose,
			"message": message,
			"expires_on": add_days(now_datetime(), cint(get_settings().link_expiry_days) or 14),
		}
	).insert(ignore_permissions=True)
	return portal_url(f"/welcome?token={token}")


def resolve_link(token: str):
	"""The unexpired Portal Link for a token, or None."""
	if not token:
		return None
	name = frappe.db.get_value("Portal Link", {"token_hash": hash_token(token)}, "name")
	if not name:
		return None
	link = frappe.get_doc("Portal Link", name)
	if get_datetime(link.expires_on) < now_datetime():
		return None
	return link


def get_contact(patient) -> tuple[str, str] | None:
	"""(channel, address) a sign-in code goes to: the mobile phone on the chart, else the email."""
	if patient.mobile_phone:
		return "SMS", patient.mobile_phone
	if patient.email:
		return "Email", patient.email
	return None


def find_by_phone(phone: str) -> list[str]:
	"""Patients with this mobile number on their chart and a portal account that is not disabled."""
	indexes = phi.blind_indexes("mobile_phone", phone)
	if not indexes:
		return []
	patients = frappe.get_all(
		"Patient", filters={"mobile_phone_index": ("in", indexes), "status": ("!=", "Deceased")}, pluck="name"
	)
	if not patients:
		return []
	return frappe.get_all(
		"Portal Account",
		filters={"patient": ("in", patients), "status": ("!=", "Disabled")},
		pluck="patient",
		order_by="creation asc",
	)


# Sign-in challenges live in the cache only, for the minutes a code is good for.


def start_challenge(
	patients: list[str], channel: str | None, address: str | None, next_path: str | None = None
) -> dict:
	"""Send a code to `address` for these patients. Returns {challenge, simulated_code?}.

	With no patients (or nowhere to send), nothing is sent but a challenge is still made, so the
	caller answers exactly as it would for a patient.
	"""
	challenge = secrets.token_urlsafe(24)
	code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
	minutes = cint(get_settings().code_minutes) or 10
	frappe.cache.set_value(
		f"{CHALLENGE_PREFIX}{challenge}",
		{
			"patients": patients,
			"code": hash_code(challenge, code),
			"attempts": 0,
			"verified": False,
			"next": next_path,
		},
		expires_in_sec=minutes * 60,
	)
	result = {"challenge": challenge}
	if patients and address:
		simulated = send_code(channel, address, code, minutes)
		# On a development site, a Simulator code is shown on the sign-in screen, labelled as such.
		if simulated and frappe.conf.developer_mode:
			result["simulated_code"] = code
	return result


def hash_code(challenge: str, code: str) -> str:
	return hmac.new(challenge.encode(), code.encode(), hashlib.sha256).hexdigest()


def send_code(channel: str, address: str, code: str, minutes: int) -> bool:
	"""Send the code. Returns whether the connection only simulated it."""
	from ophis_engage_core import connections

	clinic_name = clinic.clinic_name()
	text = _("{0} is your {1} sign-in code. It works for {2} minutes. Never share it.").format(
		code, clinic_name, minutes
	)
	connection = connections.get(channel)
	if not connection.enabled:
		frappe.throw(_("Sign-in codes cannot be sent at the moment. Please call the clinic."), SignInError)
	if channel == "Email":
		result = connection.send(address, _("Your {0} sign-in code").format(clinic_name), f"<p>{text}</p>")
	else:
		result = connection.send(address, text)
	return result["simulated"]


def get_challenge(challenge: str) -> dict:
	state = frappe.cache.get_value(f"{CHALLENGE_PREFIX}{challenge}") if challenge else None
	if not state:
		frappe.throw(_("This code has expired. Ask for a new one."), SignInError)
	return state


def check_code(challenge: str, code: str) -> dict:
	"""Mark the challenge verified if the code is right. Returns its state."""
	state = get_challenge(challenge)
	key = f"{CHALLENGE_PREFIX}{challenge}"
	if state["attempts"] >= CODE_ATTEMPTS:
		frappe.cache.delete_value(key)
		frappe.throw(_("Too many tries. Ask for a new code."), SignInError)
	state["attempts"] += 1
	right = bool(state["patients"]) and hmac.compare_digest(
		state["code"], hash_code(challenge, (code or "").strip())
	)
	if not right:
		frappe.cache.set_value(key, state, expires_in_sec=(cint(get_settings().code_minutes) or 10) * 60)
		frappe.throw(_("That code is not right. Check it and try again."), SignInError)
	state["verified"] = True
	frappe.cache.set_value(key, state, expires_in_sec=5 * 60)
	return state


def end_challenge(challenge: str):
	frappe.cache.delete_value(f"{CHALLENGE_PREFIX}{challenge}")


def get_user(account) -> str:
	"""The account's Website User, made the first time they sign in."""
	if account.user and frappe.db.exists("User", account.user):
		return account.user
	patient = frappe.get_doc("Patient", account.patient)
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"{frappe.generate_hash(length=16)}@{LOGIN_DOMAIN}",
			"first_name": patient.preferred_name or patient.first_name,
			"last_name": patient.last_name,
			"user_type": "Website User",
			"send_welcome_email": 0,
			"enabled": 1,
		}
	)
	user.flags.ignore_permissions = True
	user.insert()
	account.db_set("user", user.name)
	return user.name


def sign_in(patient: str):
	"""Start a portal session for the patient. Only after a verified code."""
	account = ensure_account(patient)
	if account.status == "Disabled":
		frappe.throw(_("This portal account is switched off. Please call the clinic."), SignInError)
	user = get_user(account)
	now = now_datetime()
	account.db_set({"status": "Active", "activated_on": account.activated_on or now, "last_sign_in": now})
	frappe.local.login_manager.login_as(user)


def current_patient() -> str:
	"""The patient whose portal session this is. Refuses anyone else, staff included."""
	user = frappe.session.user
	patient = (
		frappe.db.get_value("Portal Account", {"user": user, "status": "Active"}, "patient")
		if user != "Guest"
		else None
	)
	if not patient:
		frappe.throw(_("Please sign in to the patient portal."), frappe.AuthenticationError)
	return patient


def code_expires_at() -> str:
	return str(add_to_date(now_datetime(), minutes=cint(get_settings().code_minutes) or 10))
