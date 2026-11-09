"""Booking requests: the times patients are offered, and asking for one.

A time is open when it falls inside the practitioner's working hours (Ophis EMR Core's
`get_working_periods`, which asks the `practitioner_working_periods` hook and falls back to
clinic hours), on the core's quarter-hour grid, and clashes with no appointment and no request
still waiting for the front desk. Requests hold their time so two patients are not offered the
same one; approving one books a `Patient Appointment`, whose own rules check again.

Booked visits are Family Clinic visits, with the patient's own practitioner, for rostered
patients (Ophis EMR Core's rule). So a patient is offered the open times of their primary
practitioner only; anyone not rostered is told they are welcome as a walk-in.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate, now_datetime, today
from ophis_emr_core.emr_core.doctype.patient_appointment.patient_appointment import RELEASED_STATUSES
from ophis_emr_core.emr_core.scheduling import SLOT_GRID_MINUTES, get_working_periods

from ophis_engage_core.engage_core import clinic

DAYS_PER_PAGE = 7


def get_duration(practitioner: str, appointment_type: str | None = None) -> int:
	minutes = 0
	if appointment_type:
		minutes = cint(frappe.db.get_value("Appointment Type", appointment_type, "default_duration"))
	if not minutes:
		minutes = cint(frappe.db.get_value("Practitioner", practitioner, "default_appointment_duration"))
	return minutes or SLOT_GRID_MINUTES


def get_busy(practitioner: str, first_day, last_day, exclude_request: str | None = None) -> list[tuple]:
	"""[(start, end), …] of the practitioner's appointments and waiting requests in these days."""
	busy = [
		(row.starts_on, row.ends_on)
		for row in frappe.get_all(
			"Patient Appointment",
			filters={
				"practitioner": practitioner,
				"status": ("not in", RELEASED_STATUSES),
				"appointment_date": ("between", [first_day, last_day]),
			},
			fields=["starts_on", "ends_on"],
		)
	]
	for row in frappe.get_all(
		"Booking Request",
		filters={
			"practitioner": practitioner,
			"status": "Pending",
			"requested_date": ("between", [first_day, last_day]),
			"name": ("!=", exclude_request or ""),
		},
		fields=["requested_date", "requested_time", "duration"],
	):
		start = get_datetime(f"{row.requested_date} {row.requested_time}")
		busy.append((start, add_to_date(start, minutes=cint(row.duration) or SLOT_GRID_MINUTES)))
	return busy


def get_days(
	practitioner: str, minutes: int, first_day, days: int, exclude_request: str | None = None
) -> list[dict]:
	"""[{date, times: ["HH:MM", …]}] for each day from `first_day`, `days` long, that has an open time."""
	first_day = getdate(first_day)
	last_day = add_days(first_day, days - 1)
	busy = get_busy(practitioner, first_day, last_day, exclude_request)
	now = now_datetime()
	result = []
	day = first_day
	while day <= last_day:
		times = []
		for opens, closes in get_working_periods(practitioner, day):
			times += open_times(opens, closes, minutes, busy, not_before=now)
		if times:
			result.append({"date": str(day), "times": times})
		day = add_days(day, 1)
	return result


def open_times(opens, closes, minutes: int, busy: list[tuple], not_before) -> list[str]:
	"""Every grid time between `opens` and `closes` at which `minutes` fit, after `not_before`."""
	grid = SLOT_GRID_MINUTES
	start = opens
	times = []
	while add_to_date(start, minutes=minutes) <= closes:
		end = add_to_date(start, minutes=minutes)
		if start >= not_before and not any(
			booked_start < end and booked_end > start for booked_start, booked_end in busy
		):
			times.append(start.strftime("%H:%M"))
		start = add_to_date(start, minutes=grid)
	return times


def is_open(practitioner: str, date, time: str, minutes: int, exclude_request: str | None = None) -> bool:
	day = getdate(date)
	wanted = get_datetime(f"{day} {time}").strftime("%H:%M")
	return any(wanted in row["times"] for row in get_days(practitioner, minutes, day, 1, exclude_request))


# A patient asking for a time. These take the patient (a Patient document) from the caller, who
# has already decided whose request it is: in the patient API, the patient signed in.


def can_request(patient) -> bool:
	return bool(clinic.get_settings().booking_requests_enabled) and clinic.is_rostered_with(patient)


def horizon():
	return getdate(add_days(today(), cint(clinic.get_settings().booking_horizon_days) or 28))


def check_can_request(patient):
	if not can_request(patient):
		frappe.throw(_("Online booking is not open to you."), frappe.PermissionError)


def get_options(patient) -> dict:
	"""Whether this patient can ask for an appointment, with whom, and for what."""
	settings = clinic.get_settings()
	allowed = can_request(patient)
	reason = None
	if not settings.booking_requests_enabled:
		reason = _("Appointments are not booked online at the moment. Please call the clinic.")
	elif not allowed:
		reason = _(
			"Online booking is for patients rostered with one of the clinic's practitioners. You are welcome to walk in."
		)
	return {
		"can_request": allowed,
		"reason": reason,
		"practitioner": patient.primary_practitioner if allowed else None,
		"practitioner_name": frappe.db.get_value(
			"Practitioner", patient.primary_practitioner, "practitioner_name"
		)
		if allowed
		else None,
		"appointment_types": frappe.get_all(
			"Appointment Type",
			filters={"is_active": 1},
			fields=["name", "default_duration"],
			order_by="name asc",
		),
		"horizon_days": cint(settings.booking_horizon_days) or 28,
		"requests": get_requests(patient.name),
	}


def get_patient_times(patient, appointment_type: str | None = None, start=None) -> dict:
	"""A week of open times with the patient's practitioner, from `start` (today by default)."""
	check_can_request(patient)
	last = horizon()
	first = max(getdate(start or today()), getdate(today()))
	days = max(0, min(DAYS_PER_PAGE, (last - first).days + 1))
	minutes = get_duration(patient.primary_practitioner, appointment_type)
	return {
		"start": str(first),
		"next": str(add_days(first, DAYS_PER_PAGE)) if add_days(first, DAYS_PER_PAGE) <= last else None,
		"duration": minutes,
		"days": get_days(patient.primary_practitioner, minutes, first, days) if days else [],
	}


def create_request(
	patient,
	date: str,
	time: str,
	appointment_type: str | None = None,
	reason_for_visit: str | None = None,
	alternatives: str | None = None,
	source: str = "Portal",
):
	"""A request for an open time with the patient's own practitioner. Returns the Booking Request."""
	from ophis_engage_core.engage_core import messaging

	check_can_request(patient)
	settings = clinic.get_settings()
	waiting = frappe.db.count("Booking Request", {"patient": patient.name, "status": "Pending"})
	if waiting >= (cint(settings.max_pending_requests) or 2):
		frappe.throw(
			_(
				"You already have {0} requests waiting for the clinic. Withdraw one to ask for another."
			).format(waiting)
		)
	if appointment_type and not frappe.db.get_value("Appointment Type", appointment_type, "is_active"):
		frappe.throw(_("Choose a type of appointment from the list."))
	if getdate(date) > horizon():
		frappe.throw(_("That date is too far ahead to ask for."))
	minutes = get_duration(patient.primary_practitioner, appointment_type)
	if not is_open(patient.primary_practitioner, date, time, minutes):
		frappe.throw(_("That time has just been taken. Please choose another."))

	doc = frappe.get_doc(
		{
			"doctype": "Booking Request",
			"patient": patient.name,
			"practitioner": patient.primary_practitioner,
			"appointment_type": appointment_type,
			"duration": minutes,
			"requested_date": date,
			"requested_time": time,
			"reason_for_visit": (reason_for_visit or "").strip()[:1000] or None,
			"alternatives": (alternatives or "").strip()[:500] or None,
			"source": source,
		}
	).insert(ignore_permissions=True)
	template = messaging.get_template("Booking Request Received")
	if template:
		messaging.create(patient.name, template, context={"request": doc.describe()}, reference=doc)
	return doc


def get_requests(patient: str) -> list[dict]:
	"""The patient's requests from the last 30 days on."""
	return frappe.get_all(
		"Booking Request",
		filters={"patient": patient, "requested_date": (">=", add_days(today(), -30))},
		fields=[
			"name",
			"status",
			"requested_date",
			"requested_time",
			"appointment_type",
			"practitioner_name",
			"decline_reason",
			"appointment",
		],
		order_by="requested_date desc, requested_time desc",
	)


def withdraw(patient: str, name: str):
	"""Withdraw the patient's own waiting request. Someone else's is answered as not found."""
	doc = frappe.get_doc("Booking Request", name) if frappe.db.exists("Booking Request", name) else None
	if not doc or doc.patient != patient:
		frappe.throw(_("Request not found."), frappe.DoesNotExistError)
	doc.withdraw()
