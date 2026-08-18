"""
state.py — Conversation state + message dedup, with two backends:

  - Local (Flask on your laptop): in-memory dict, business as usual.
  - Lambda: DynamoDB. Activated simply by setting env var TABLE_NAME.

The rest of the code calls get/touch/clear/already_processed without
knowing which backend is underneath. This table is NOT a bookings
database: it holds in-progress flows with a ~90-second useful life.
Appointments live exclusively in Google Calendar.

DynamoDB schema (single table, on-demand):
  pk = "conv#<wa_id>"  -> state, data(json), last(epoch), ttl
  pk = "msg#<msg_id>"  -> ttl                (dedup via conditional write)
"""
import os
import json
import time
import logging
from collections import deque

log = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("TABLE_NAME", "")
USE_DYNAMO = bool(TABLE_NAME)

CONV_TTL_SECONDS = 600      # safety net; the sweeper cleans at 90 s, TTL is backup
DEDUP_TTL_SECONDS = 3600

if USE_DYNAMO:
    import boto3
    from botocore.exceptions import ClientError

    _table = boto3.resource("dynamodb").Table(TABLE_NAME)
else:
    import threading

    _mem: dict[str, dict] = {}
    _mem_lock = threading.Lock()
    _seen_ids: deque = deque(maxlen=500)
    _seen_set: set = set()


# --------------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------------
def get(wa_id: str) -> dict | None:
    """Returns {"state": str, "data": dict, "last": float} or None."""
    if not USE_DYNAMO:
        with _mem_lock:
            conv = _mem.get(wa_id)
            return dict(conv) if conv else None
    try:
        item = _table.get_item(Key={"pk": f"conv#{wa_id}"}).get("Item")
    except ClientError as exc:
        log.error("Dynamo get failed: %s", exc)
        return None
    if not item:
        return None
    return {
        "state": item["state"],
        "data": json.loads(item["data"]),
        "last": float(item["last"]),
    }


def touch(wa_id: str, state: str, data: dict | None = None):
    """Create/update the conversation and reset the inactivity clock."""
    now = time.time()
    if not USE_DYNAMO:
        with _mem_lock:
            if data is None:
                data = _mem.get(wa_id, {}).get("data", {})
            _mem[wa_id] = {"state": state, "data": data, "last": now}
        return
    if data is None:
        existing = get(wa_id)
        data = existing["data"] if existing else {}
    try:
        _table.put_item(Item={
            "pk": f"conv#{wa_id}",
            "state": state,
            "data": json.dumps(data),
            "last": int(now),
            "ttl": int(now) + CONV_TTL_SECONDS,
        })
    except ClientError as exc:
        log.error("Dynamo put failed: %s", exc)


def clear(wa_id: str):
    if not USE_DYNAMO:
        with _mem_lock:
            _mem.pop(wa_id, None)
        return
    try:
        _table.delete_item(Key={"pk": f"conv#{wa_id}"})
    except ClientError as exc:
        log.error("Dynamo delete failed: %s", exc)


def expire_stale(max_idle_seconds: int) -> list[str]:
    """
    Deletes conversations idle > max_idle and returns their wa_ids so the
    caller can notify them. A Scan is fine here: the table only ever holds
    a handful of live items. (Memory backend: the local sweeper thread
    uses this same signature.)
    """
    cutoff = time.time() - max_idle_seconds
    expired: list[str] = []
    if not USE_DYNAMO:
        with _mem_lock:
            for wa_id, conv in list(_mem.items()):
                if conv["last"] < cutoff:
                    expired.append(wa_id)
                    del _mem[wa_id]
        return expired
    try:
        resp = _table.scan(
            FilterExpression="begins_with(pk, :p) AND #l < :c",
            ExpressionAttributeNames={"#l": "last"},
            ExpressionAttributeValues={":p": "conv#", ":c": int(cutoff)},
        )
        for item in resp.get("Items", []):
            wa_id = item["pk"].removeprefix("conv#")
            _table.delete_item(Key={"pk": item["pk"]})
            expired.append(wa_id)
    except ClientError as exc:
        log.error("Dynamo scan/expire failed: %s", exc)
    return expired


# --------------------------------------------------------------------------
# Webhook dedup (Meta retries if it doesn't see a fast 200)
# --------------------------------------------------------------------------
def already_processed(msg_id: str) -> bool:
    if not msg_id:
        return False
    if not USE_DYNAMO:
        if msg_id in _seen_set:
            return True
        _seen_set.add(msg_id)
        _seen_ids.append(msg_id)
        if len(_seen_set) > 600:
            _seen_set.intersection_update(_seen_ids)
        return False
    try:
        _table.put_item(
            Item={"pk": f"msg#{msg_id}", "ttl": int(time.time()) + DEDUP_TTL_SECONDS},
            ConditionExpression="attribute_not_exists(pk)",
        )
        return False
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return True  # another invocation already handled it
        log.error("Dynamo dedup failed: %s", exc)
        return False  # when in doubt, process: a duplicate beats silence
