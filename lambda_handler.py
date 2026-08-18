"""
lambda_handler.py — API Gateway (HTTP API v2) -> app.py adapter.
All the brains live in app.py; this only translates the event.
"""
import json
import base64
import logging

import whatsapp_api as wa
from app import webhook_verify, webhook_incoming

logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")

    if method == "GET":
        body, code = webhook_verify(event.get("queryStringParameters") or {})
        return {"statusCode": code, "body": body}

    if method == "POST":
        # Signature check FIRST, on the raw bytes exactly as Meta sent them.
        raw = event.get("body") or ""
        raw = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()
        headers = event.get("headers") or {}  # HTTP API lowercases header names
        if not wa.verify_signature(raw, headers.get("x-hub-signature-256")):
            logging.warning("Webhook with bad/missing signature rejected")
            return {"statusCode": 403, "body": "invalid signature"}
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        webhook_incoming(payload)  # never raises; always 200 back to Meta
        return {"statusCode": 200, "body": "ok"}

    return {"statusCode": 405, "body": "method not allowed"}
