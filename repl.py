"""
repl.py — Test the state machine with NO WhatsApp, NO AWS, NO tunnel.
Outbound messages print to the console; you type what a customer would.
Buttons: their IDs are shown and you reply with the ID
(e.g. confirm_yes, alt_0, cancel_yes).

  python3 repl.py            -> REAL calendar (uses token.json; creates/deletes real events)
  python3 repl.py --fake-cal -> simulated calendar (everything free; never touches Google)

Requires: export VERIFY_TOKEN=x WHATSAPP_TOKEN=x PHONE_NUMBER_ID=x
(dummy values are fine: Meta is never called from here).
"""
import sys
from datetime import timedelta

# --- Patch the WhatsApp layer BEFORE importing app -----------------------
import whatsapp_api as wa


def _print_text(to, body):
    print(f"\n🤖 BOT -> {to}:\n{body}\n")
    return True


def _print_buttons(to, body, buttons):
    print(f"\n🤖 BOT -> {to}:\n{body}")
    for bid, title in buttons:
        print(f"   [{bid}]  {title}")
    print()
    return True


def _print_list(to, body, label, rows):
    return _print_buttons(to, body, rows)


wa.send_text = _print_text
wa.send_buttons = _print_buttons
wa.send_list = _print_list

if "--fake-cal" in sys.argv:
    import calendar_utils as cal

    _fake_events = {}

    cal.is_slot_free = lambda start: True
    cal.find_alternatives = lambda requested, max_alts=3: [
        requested + timedelta(minutes=30 * (i + 1)) for i in range(max_alts)
    ]

    def _fake_create(name, user_id, start):
        eid = f"fake_{len(_fake_events)}"
        _fake_events[eid] = {"id": eid, "start": {"dateTime": start.isoformat()},
                             "extendedProperties": {"private": {"user_id": str(user_id)}}}
        print(f"   (📅 fake event created: {name} @ {start})")
        return _fake_events[eid]

    cal.create_booking = _fake_create
    cal.get_user_bookings = lambda user_id: [
        e for e in _fake_events.values()
        if e["extendedProperties"]["private"]["user_id"] == str(user_id)
    ]

    def _fake_delete(event_id, user_id):
        return _fake_events.pop(event_id, None) is not None

    cal.delete_booking = _fake_delete

from app import _handle_text, _handle_choice, sweep_timeouts  # noqa: E402

WA_ID = "5213312345678"
print("Bot REPL — type as the customer. Use button IDs to pick them.")
print("REPL commands: /sweep (force timeout check) · /quit\n")

while True:
    try:
        raw = input("🧑 YOU: ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if raw == "/quit":
        break
    if raw == "/sweep":
        print(f"   (expired: {sweep_timeouts()})")
        continue
    if not raw:
        continue
    # Heuristic: looks like a button ID -> choice; otherwise -> text
    if raw.split("_")[0] in {"confirm", "alt", "cancel", "cx"}:
        _handle_choice(WA_ID, raw)
    else:
        _handle_text(WA_ID, raw)
