from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import PaymentTransaction, User

logger = logging.getLogger(__name__)

CRYPTOMUS_API_URL = "https://api.cryptomus.com/v1/payment"


def _generate_signature(data_json: str, payment_key: str) -> str:
    """Generate MD5 signature required by Cryptomus API."""
    encoded_json = base64.b64encode(data_json.encode("utf-8")).decode("utf-8")
    return hashlib.md5((encoded_json + payment_key).encode("utf-8")).hexdigest()


async def create_checkout_invoice(
    user: User, package_tier: str, session: AsyncSession
) -> tuple[PaymentTransaction, str]:
    """Create a new payment transaction and request payment URL from Cryptomus."""
    if package_tier.lower() == "pro":
        amount_usd = settings.billing_pro_price_usd
        credits_added = settings.billing_pro_credits
    else: # starter
        amount_usd = settings.billing_starter_price_usd
        credits_added = settings.billing_starter_credits

    tx_id = str(uuid.uuid4())
    tx = PaymentTransaction(
        id=tx_id,
        user_id=user.id,
        amount_usd=amount_usd,
        credits_added=credits_added,
        status="pending",
        provider="cryptomus",
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)

    payment_url = ""

    # Live Cryptomus Integration
    if settings.cryptomus_merchant_id and settings.cryptomus_payment_key:
        payload_data = {
            "amount": f"{amount_usd:.2f}",
            "currency": "USD",
            "order_id": tx_id,
            "url_callback": "https://pipka.net/api/webhooks/crypto",
            "url_return": "https://pipka.net/?payment=success",
            "is_payment_multiple": False,
            "lifetime": 3600,
        }
        json_str = json.dumps(payload_data)
        signature = _generate_signature(json_str, settings.cryptomus_payment_key)
        headers = {
            "merchant": settings.cryptomus_merchant_id,
            "sign": signature,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(CRYPTOMUS_API_URL, headers=headers, content=json_str)
                resp.raise_for_status()
                res_data = resp.json()
                payment_url = res_data.get("result", {}).get("url", "")
                provider_tx = res_data.get("result", {}).get("uuid", "")
                if provider_tx:
                    tx.provider_tx_id = provider_tx
        except Exception as e:
            logger.error("Cryptomus invoice creation error: %s", e)

    # Sandbox / Test mode fallback
    if not payment_url:
        payment_url = f"https://pipka.net/?checkout_test_id={tx_id}"

    tx.payment_url = payment_url
    await session.commit()
    return tx, payment_url


def verify_cryptomus_signature(raw_body: bytes, header_sign: str) -> bool:
    """Verify Cryptomus webhook MD5 signature."""
    if not settings.cryptomus_payment_key:
        return False
    try:
        encoded = base64.b64encode(raw_body).decode("utf-8")
        computed_sign = hashlib.md5((encoded + settings.cryptomus_payment_key).encode("utf-8")).hexdigest()
        return computed_sign == header_sign
    except Exception as e:
        logger.error("Signature verification error: %s", e)
        return False


async def fulfill_payment_transaction(
    tx_id: str, provider_tx_id: str | None, session: AsyncSession
) -> bool:
    """Fulfill a successful transaction by adding credits to the user account."""
    result = await session.execute(
        select(PaymentTransaction).where(PaymentTransaction.id == tx_id)
    )
    tx = result.scalar_one_or_none()
    if not tx:
        logger.warning("Payment transaction %s not found for fulfillment", tx_id)
        return False

    if tx.status == "paid":
        return True  # Already fulfilled (idempotent)

    tx.status = "paid"
    if provider_tx_id:
        tx.provider_tx_id = provider_tx_id

    # Fetch user and add credits
    user_res = await session.execute(select(User).where(User.id == tx.user_id))
    user = user_res.scalar_one_or_none()
    if user:
        user.credits += tx.credits_added
        user.total_credits_purchased += tx.credits_added
        logger.info(
            "Fulfilled transaction %s: added %d credits to user %s (new total: %d)",
            tx_id, tx.credits_added, user.id, user.credits,
        )

    await session.commit()
    return True


async def deduct_user_credits(user: User, count: int, session: AsyncSession) -> bool:
    """Deduct N credits from a user account if sufficient balance exists."""
    if user.credits < count:
        logger.warning("User %s has insufficient credits (%d < %d)", user.id, user.credits, count)
        return False
    user.credits -= count
    await session.commit()
    return True
