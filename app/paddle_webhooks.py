import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app.database import SessionLocal
from app.models import Subscription

logger = logging.getLogger(__name__)
router = APIRouter()

WEBHOOK_SECRET = os.getenv("PADDLE_WEBHOOK_SECRET", "")


def verify_paddle_signature(signature_header: str, raw_body: bytes) -> bool:
    """Verify Paddle webhook signature using HMAC-SHA256."""
    if not WEBHOOK_SECRET:
        logger.warning("PADDLE_WEBHOOK_SECRET not set — skipping verification")
        return True  # Allow in dev when secret isn't configured

    parts = {}
    for part in signature_header.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            parts[key] = value

    ts = parts.get("ts", "")
    h1 = parts.get("h1", "")
    if not ts or not h1:
        return False

    payload = f"{ts}:{raw_body.decode()}"
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(h1, expected)


def _upsert_subscription(db: DBSession, sub_data: dict, customer_id: str | None = None) -> None:
    """Create or update a subscription record."""
    sub_id = sub_data.get("id", "")
    status = sub_data.get("status", "active")
    user_id = sub_data.get("custom_data", {}).get("user_id") if sub_data.get("custom_data") else None
    price_id = None
    current_period_end = None

    items = sub_data.get("items", [])
    if items:
        price_id = items[0].get("price", {}).get("id") or items[0].get("price_id")

    billing_period = sub_data.get("current_billing_period")
    if billing_period and billing_period.get("ends_at"):
        try:
            current_period_end = datetime.fromisoformat(billing_period["ends_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    existing = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if existing:
        existing.status = status
        if price_id:
            existing.price_id = price_id
        if current_period_end:
            existing.current_period_end = current_period_end
        if customer_id:
            existing.paddle_customer_id = customer_id
        existing.updated_at = datetime.now(timezone.utc)
    else:
        sub = Subscription(
            id=sub_id,
            user_id=user_id,
            paddle_customer_id=customer_id,
            status=status,
            price_id=price_id,
            current_period_end=current_period_end,
        )
        db.add(sub)

    db.commit()


@router.post("/webhooks")
async def paddle_webhook(request: Request):
    """Handle incoming Paddle webhook events."""
    raw_body = await request.body()
    signature = request.headers.get("Paddle-Signature", "")

    if not verify_paddle_signature(signature, raw_body):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    event = await request.json()
    event_type = event.get("event_type", "")
    data = event.get("data", {})

    logger.info("Paddle webhook: %s (sub_id=%s, custom_data=%s)", event_type, data.get("id"), data.get("custom_data"))

    db = SessionLocal()
    try:
        if event_type in ("subscription.created", "subscription.activated", "subscription.updated"):
            _upsert_subscription(db, data, data.get("customer_id"))
            logger.info("Upserted subscription %s", data.get("id"))

        elif event_type == "subscription.canceled":
            sub = db.query(Subscription).filter(Subscription.id == data.get("id")).first()
            if sub:
                sub.status = "canceled"
                sub.updated_at = datetime.now(timezone.utc)
                db.commit()

        elif event_type == "subscription.paused":
            sub = db.query(Subscription).filter(Subscription.id == data.get("id")).first()
            if sub:
                sub.status = "paused"
                sub.updated_at = datetime.now(timezone.utc)
                db.commit()

        elif event_type == "transaction.completed":
            logger.info("Transaction completed: %s", data.get("id"))

        else:
            logger.info("Unhandled event type: %s", event_type)
    except Exception:
        logger.exception("Webhook handler failed for %s", event_type)
        db.rollback()
    finally:
        db.close()

    return {"ok": True}


@router.get("/debug/subscriptions")
def debug_subscriptions():
    """Temporary debug endpoint — list all subscriptions."""
    db = SessionLocal()
    try:
        subs = db.query(Subscription).all()
        return [
            {
                "id": s.id,
                "user_id": str(s.user_id) if s.user_id else None,
                "status": s.status,
                "price_id": s.price_id,
                "paddle_customer_id": s.paddle_customer_id,
            }
            for s in subs
        ]
    finally:
        db.close()
