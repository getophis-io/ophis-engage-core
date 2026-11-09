"""The patient API: the whitelisted methods a patient app (a portal, a mobile app) calls.

This is the one place the core has whitelisted methods, because every patient app speaks the same
contract, and the rules it carries (signing in, whose messages are whose) must not be rebuilt
by each. Every method but signing in reads the patient from `accounts.current_patient()`, the
portal session's own patient, never from its arguments.

- `auth`: signing in with a phone number or a personal link, then a one-time code.
- `inbox`: the patient's mailbox.
- `booking`: asking for an appointment.
"""
