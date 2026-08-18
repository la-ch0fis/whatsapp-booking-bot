"""
sweeper.py — Serverless replacement for the local sweeper thread.
EventBridge invokes it every minute: expires conversations idle >90 s
and notifies the user. One-minute granularity over a 90-second timeout
is fine: the notice arrives between 90 s and ~150 s after the user's
last message. If you ever need precision, use one-shot EventBridge
Scheduler per conversation — today that would be over-engineering.
"""
import logging

from app import sweep_timeouts

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger("sweeper")


def lambda_handler(event, context):
    n = sweep_timeouts()
    if n:
        log.info("Expired conversations: %d", n)
    return {"expired": n}
