"""
app.py — Webhook logic + conversation state machine. Runtime-agnostic:
  - Local:  Flask wraps it (bottom of file) and a thread sweeps timeouts.
  - Lambda: lambda_handler.py wraps webhook_verify/webhook_incoming and
            sweeper.py sweeps timeouts. Same code, different transport.

State lives in state.py (local memory or DynamoDB depending on TABLE_NAME).
Confirmed bookings live ONLY in Google Calendar.

Booking flow:      ASK_NAME -> ASK_DATETIME -> CONFIRM
Cancellation flow: CANCEL_PICK -> CANCEL_CONFIRM
"""
import os
import logging
import unicodedata
from datetime import datetime

import state
import messages as M
import whatsapp_api as wa
import calendar_utils as cal
import parsing_utils as par
from config import TZ, CONVERSATION_TIMEOUT_SECONDS, TIMEOUT_SWEEP_INTERVAL, MAX_JUNK_STRIKES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]

# Parse failures that count as "playing around". Real-world limits.
# (past, Sunday, holiday, lunch, outsie hours) are NOT junk.
JUNK_ERRORS = {
    par.ERR_JUNK,
    par.ERR_NO_TIME_MARKER,
    par.ERR_INVALID_DATE,
    par.ERR_MISSING_PARTS,
    par.ERR_UNPARSEABLE
}


def _norm_cmd(text: str) -> str:
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------------------
# Runtime-agnostic entry points (wrapped by Flask locally, Lambda in prod)
# ---------------------------------------------------------------------------
def webhook_verify(args: dict) -> tuple[str, int]:
    """Meta's one-time handshake. args = query string params as a dict."""
    if (args.get("hub.mode") == "subscribe"
            and args.get("hub.verify_token") == VERIFY_TOKEN):
        return args.get("hub.challenge", ""), 200
    return "forbidden", 403


def webhook_incoming(payload: dict) -> None:
    """
    Process a POST from Meta. NEVER raises upward: the transport must
    always return 200, or Meta re-delivers the message in a retry loop.
    """
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # Status webhooks (sent/delivered/read) hit this same
                # endpoint: we only iterate "messages".
                for msg in value.get("messages", []):
                    _dispatch(msg)
    except Exception:
        log.exception("Error processing webhook")


def sweep_timeouts() -> int:
    """Expire flows idle >90 s and NOTIFY the user. Returns the count."""
    expired = state.expire_stale(CONVERSATION_TIMEOUT_SECONDS)
    for wa_id in expired:
        wa.send_text(wa_id, M.TIMEOUT)
        log.info("Conversation expired: %s", wa_id)
    return len(expired)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def _dispatch(msg: dict):
    if state.already_processed(msg.get("id", "")):
        log.info("Duplicate message ignored: %s", msg.get("id"))
        return

    wa_id = msg.get("from", "")
    mtype = msg.get("type")

    if mtype == "text":
        _handle_text(wa_id, msg.get("text", {}).get("body", ""))
    elif mtype == "interactive":
        inter = msg.get("interactive", {})
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        _handle_choice(wa_id, reply.get("id", ""))
    else:
        # audio, image, sticker, location... friendly redirect
        wa.send_text(wa_id, M.REDIRECT)


BOOK_COMMANDS = {"agendar", "agenda", "cita", "reservar", "reserva", "sesion"}
CANCEL_COMMANDS = {"cancelar", "cancela", "cancelacion"}
GREETINGS = {"hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hi", "hey", "sup", "que tranza"}


def _handle_text(wa_id: str, text: str):
    cmd = _norm_cmd(text)
    conv = state.get(wa_id)

    # Global commands: work even mid-conversation (they restart the flow)
    if cmd in BOOK_COMMANDS:
        state.touch(wa_id, "ASK_NAME", {})
        wa.send_text(wa_id, M.ASK_NAME)
        return
    if cmd in CANCEL_COMMANDS:
        _start_cancel(wa_id)
        return

    if conv is None:
        # No active flow: greeting or redirect — never silence
        wa.send_text(wa_id, M.WELCOME if cmd in GREETINGS else M.REDIRECT)
        return

    if conv["state"] == "ASK_NAME":
        name = text.strip()[:60]
        state.touch(wa_id, "ASK_DATETIME", {"name": name})
        wa.send_text(wa_id, M.ASK_DATETIME.format(name=name))
    elif conv["state"] == "ASK_DATETIME":
        _handle_datetime(wa_id, conv["data"], text)
    else:
        # Was in a button state and typed free text instead
        wa.send_text(wa_id, M.REDIRECT)


def _handle_datetime(wa_id: str, data: dict, text: str):
    dt, err = par.parse_and_validate(text)
    if err:
        if err and JUNK_ERRORS:
            strikes = int(data.get("junk_strikes", 0)) + 1
        else:
            strikes = 0 # a sensible request (e.g. a Sunday) is not junk
        if strikes >= MAX_JUNK_STRIKES:
            state.clear(wa_id)
            wa.send_text(wa_id, M.TOO_MANY_JUNK)
            log.info("Conversation closed after %d junk inputs: %s", strikes, wa_id)
            return
        data["junk_strikes"] = strikes
        # SPECIFIC message per failure reason; user stays in ASK_DATETIME
        state.touch(wa_id, "ASK_DATETIME", data)
        wa.send_text(wa_id, M.PARSE_ERRORS[err])
        return
    
    data.pop("junk_strikes", None)

    try:
        if cal.is_slot_free(dt):
            data["slot_iso"] = dt.isoformat()
            state.touch(wa_id, "CONFIRM", data)
            wa.send_buttons(
                wa_id,
                M.CONFIRM_SLOT.format(when=M.fmt_dt(dt)),
                [("confirm_yes", "Confirmar ✅"), ("confirm_no", "Cambiar fecha")],
            )
            return
        alts = cal.find_alternatives(dt, max_alts=3)
    except cal.CalendarError:
        wa.send_text(wa_id, M.EXTERNAL_ERROR)
        state.touch(wa_id, "ASK_DATETIME", data)
        return

    if not alts:
        state.touch(wa_id, "ASK_DATETIME", data)
        wa.send_text(wa_id, M.SLOT_TAKEN_NO_ALTS)
        return

    data["alts"] = {f"alt_{i}": a.isoformat() for i, a in enumerate(alts)}
    state.touch(wa_id, "CONFIRM", data)
    wa.send_buttons(
        wa_id, M.SLOT_TAKEN_WITH_ALTS,
        [(f"alt_{i}", M.fmt_time(a)) for i, a in enumerate(alts)],
    )


def _handle_choice(wa_id: str, choice_id: str):
    conv = state.get(wa_id)
    if conv is None:
        wa.send_text(wa_id, M.REDIRECT)
        return

    st, data = conv["state"], conv["data"]

    if st == "CONFIRM":
        if choice_id == "confirm_yes":
            _finalize_booking(wa_id, data, data.get("slot_iso"))
        elif choice_id == "confirm_no":
            state.touch(wa_id, "ASK_DATETIME", data)
            wa.send_text(wa_id, M.ASK_DATETIME.format(name=data.get("name", "")))
        elif choice_id in data.get("alts", {}):
            _finalize_booking(wa_id, data, data["alts"][choice_id])
        else:
            wa.send_text(wa_id, M.REDIRECT)
    elif st == "CANCEL_PICK":
        if choice_id in data.get("bookings", {}):
            data["target"] = choice_id
            state.touch(wa_id, "CANCEL_CONFIRM", data)
            when = data["bookings"][choice_id]["when"]
            wa.send_buttons(
                wa_id, M.CANCEL_CONFIRM.format(when=when),
                [("cancel_yes", "Sí, cancelar"), ("cancel_no", "No, conservar")],
            )
        else:
            wa.send_text(wa_id, M.REDIRECT)
    elif st == "CANCEL_CONFIRM":
        _finalize_cancel(wa_id, data, choice_id)
    else:
        wa.send_text(wa_id, M.REDIRECT)


def _finalize_booking(wa_id: str, data: dict, slot_iso: str | None):
    if not slot_iso:
        wa.send_text(wa_id, M.REDIRECT)
        return
    dt = datetime.fromisoformat(slot_iso)
    try:
        # Re-check right before creating: someone may have grabbed the slot
        # between the freebusy check and the "Confirmar" tap.
        if not cal.is_slot_free(dt):
            alts = cal.find_alternatives(dt, max_alts=3)
            if alts:
                data["alts"] = {f"alt_{i}": a.isoformat() for i, a in enumerate(alts)}
                data.pop("slot_iso", None)
                state.touch(wa_id, "CONFIRM", data)
                wa.send_buttons(
                    wa_id, M.SLOT_TAKEN_WITH_ALTS,
                    [(f"alt_{i}", M.fmt_time(a)) for i, a in enumerate(alts)],
                )
            else:
                state.touch(wa_id, "ASK_DATETIME", data)
                wa.send_text(wa_id, M.SLOT_TAKEN_NO_ALTS)
            return
        cal.create_booking(data.get("name", "Cliente"), wa_id, dt)
    except cal.CalendarError:
        wa.send_text(wa_id, M.EXTERNAL_ERROR)
        return
    state.clear(wa_id)
    wa.send_text(wa_id, M.BOOKED.format(when=M.fmt_dt(dt)))


def _start_cancel(wa_id: str):
    try:
        bookings = cal.get_user_bookings(wa_id)  # ONLY this wa_id's bookings
    except cal.CalendarError:
        wa.send_text(wa_id, M.EXTERNAL_ERROR)
        return
    if not bookings:
        state.clear(wa_id)
        wa.send_text(wa_id, M.CANCEL_NONE)
        return

    data = {"bookings": {}}
    rows = []
    for ev in bookings:
        start = datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(TZ)
        cid = f"cx_{ev['id']}"[:200]
        data["bookings"][cid] = {"event_id": ev["id"], "when": M.fmt_dt(start)}
        rows.append((cid, M.fmt_dt(start)))
    state.touch(wa_id, "CANCEL_PICK", data)
    if len(rows) <= 3:
        wa.send_buttons(wa_id, M.CANCEL_PICK, rows)
    else:
        wa.send_list(wa_id, M.CANCEL_PICK, "Ver mis citas", rows)


def _finalize_cancel(wa_id: str, data: dict, choice_id: str):
    if choice_id == "cancel_no":
        state.clear(wa_id)
        wa.send_text(wa_id, M.CANCEL_KEPT)
        return
    if choice_id != "cancel_yes":
        wa.send_text(wa_id, M.REDIRECT)
        return
    target = data.get("bookings", {}).get(data.get("target"), {})
    try:
        ok = cal.delete_booking(target.get("event_id", ""), wa_id)
    except cal.CalendarError:
        wa.send_text(wa_id, M.EXTERNAL_ERROR)
        return
    state.clear(wa_id)
    wa.send_text(wa_id, M.CANCELLED.format(when=target.get("when", "")) if ok
                 else M.CANCEL_FAILED)


# ---------------------------------------------------------------------------
# Local transport: Flask + sweeper thread (only outside Lambda)
# ---------------------------------------------------------------------------
if not state.USE_DYNAMO:
    import time
    import threading
    from flask import Flask, request

    app = Flask(__name__)

    @app.get("/webhook")
    def _verify_route():
        body, code = webhook_verify(request.args.to_dict())
        return body, code

    @app.post("/webhook")
    def _incoming_route():
        # Same signature gate as prod. With APP_SECRET unset (pure REPL/dev)
        # verify_signature allows and warns; with it set, forgeries get 403.
        if not wa.verify_signature(request.get_data(),
                                   request.headers.get("X-Hub-Signature-256")):
            return "invalid signature", 403
        webhook_incoming(request.get_json(silent=True) or {})
        return "ok", 200

    def _sweeper_loop():
        while True:
            time.sleep(TIMEOUT_SWEEP_INTERVAL)
            sweep_timeouts()

    threading.Thread(target=_sweeper_loop, daemon=True).start()


if __name__ == "__main__":
    # Local run: gunicorn --workers 1 app:app
    app.run(port=8000, debug=False)
