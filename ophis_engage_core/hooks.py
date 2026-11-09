app_name = "ophis_engage_core"
app_title = "Ophis Engage Core"
app_publisher = "STRMLNE"
app_description = "Patient engagement for Canadian family practice, without a user interface"
app_email = "dev@getophis.io"
app_license = "agpl-3.0"

# Patients, practitioners, appointments, orders and referrals are Ophis EMR Core's records.
required_apps = ["frappe/erpnext", "ophis_emr_core"]

after_install = "ophis_engage_core.install.after_install"
after_migrate = "ophis_engage_core.install.after_migrate"

# Messages to patients as the clinical record changes (engage_core/events.py).
doc_events = {
	"Patient Order": {"after_insert": "ophis_engage_core.engage_core.events.order_placed"},
	"Patient Appointment": {
		"after_insert": "ophis_engage_core.engage_core.events.appointment_booked",
		"on_update": "ophis_engage_core.engage_core.events.appointment_changed",
	},
}

# Hooks of Ophis EMR Core this app answers:
#
# Patient messages are encrypted with the core's key, and rotated and redacted with its fields.
phi_encrypted_fields = {"Patient Message": ["subject", "body", "text_message"]}

# Hooks this app answers for apps built on the core (see the README):
#
# ereferral_service    (patient) -> {enabled, can_send, oceanmd, email, by_referral}
ereferral_service = ["ophis_engage_core.engage_core.referrals.get_service"]
