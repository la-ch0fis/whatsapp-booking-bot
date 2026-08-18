"""
whatsapp_api.py — The ONLY file that talks to Meta's Cloud API.
Porting to another platform someday = replace this file, done.

Note there is no "WhatsApp library": these are plain HTTPS POSTs with
`requests` against the Graph API. Every outbound call is wrapped in
try/except: the user never sees a crash, the real error goes to the logs.
"""
import os
import hmac
import hashlib
import logging

import requests

from config import GRAPH_API_VERSION, REMINDER_TEMPLATE_NAME

log = logging.getLogger(__name__)

_secret_cache: dict = {}


def _secret(name: str) -> str:
    """
    Resolve a secret by name:
      - Local dev: plain env var (e.g. WHATSAPP_TOKEN).
      - Lambda: env var <NAME>_SSM holds an SSM SecureString parameter NAME;
        the value is fetched once per container and cached. Secrets never
        sit in Lambda env vars or CloudFormation parameters this way.
    """
    if name in _secret_cache:
        return _secret_cache[name]
    val = os.environ.get(name, "")
    if not val:
        ssm_param = os.environ.get(f"{name}_SSM", "")
        if ssm_param:
            import boto3
            resp = boto3.client("ssm").get_parameter(Name=ssm_param, WithDecryption=True)
            val = resp["Parameter"]["Value"]
    _secret_cache[name] = val
    return val


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """
    Verify Meta's X-Hub-Signature-256 header: HMAC-SHA256 of the RAW request
    body keyed with the App Secret. This is the ONLY thing standing between
    your webhook and anyone on the internet forging customer messages.
    If APP_SECRET is not configured (pure local REPL testing), we allow the
    request but log a loud warning — never run production like that.
    """
    secret = _secret("APP_SECRET")
    if not secret:
        log.warning("APP_SECRET not set — webhook signature NOT verified (dev only!)")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def _url() -> str:
    return (f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
            f"{os.environ['PHONE_NUMBER_ID']}/messages")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_secret('WHATSAPP_TOKEN')}",
            "Content-Type": "application/json"}


def _post(payload: dict) -> bool:
    try:
        resp = requests.post(_url(), headers=_headers(), json=payload, timeout=15)
        if resp.status_code >= 400:
            log.error("WhatsApp API %s: %s", resp.status_code, resp.text)
            return False
        return True
    except requests.RequestException as exc:
        log.error("WhatsApp API request failed: %s", exc)
        return False


def send_text(to: str, body: str) -> bool:
    return _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    })


def send_buttons(to: str, body: str, buttons: list[tuple[str, str]]) -> bool:
    """
    Quick-reply buttons. WHATSAPP HARD LIMITS: max 3 buttons,
    titles max 20 chars. buttons = [(id, title), ...]
    """
    return _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid, "title": title[:20]}}
                    for bid, title in buttons[:3]
                ]
            },
        },
    })


def send_list(to: str, body: str, button_label: str,
              rows: list[tuple[str, str]]) -> bool:
    """
    List message (up to 10 rows) — used when there are more than 3 options,
    e.g. picking which booking to cancel. rows = [(id, title), ...]
    """
    return _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_label[:20],
                "sections": [{
                    "title": "Opciones",
                    "rows": [{"id": rid, "title": title[:24]} for rid, title in rows[:10]],
                }],
            },
        },
    })


def send_reminder_template(to: str, when_text: str) -> bool:
    """
    Reminders fire ~24h after the customer's last message, i.e. OUTSIDE
    WhatsApp's 24-hour customer-service window: Meta requires a
    PRE-APPROVED template here. Create one named 'recordatorio_cita' in
    Business Manager with a {{1}} body parameter for the date/time.
    Free-form text at this point would simply fail to deliver.
    """
    return _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": REMINDER_TEMPLATE_NAME,
            "language": {"code": "es_MX"},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": when_text}],
            }],
        },
    })
