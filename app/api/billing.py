from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._helpers import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import PaymentTransaction, User
from app.services.billing_service import (
    create_checkout_invoice,
    fulfill_payment_transaction,
    verify_cryptomus_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["billing"])


class CheckoutRequest(BaseModel):
    package_tier: str  # "starter" or "pro"


@router.get("/billing/balance")
async def get_billing_balance(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the current credit balance, purchase history, and active pricing plans."""
    res = await session.execute(
        select(PaymentTransaction)
        .where(PaymentTransaction.user_id == current_user.id)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(20)
    )
    transactions = res.scalars().all()

    tx_history = [
        {
            "id": tx.id,
            "amount_usd": tx.amount_usd,
            "credits_added": tx.credits_added,
            "status": tx.status,
            "provider": tx.provider,
            "payment_url": tx.payment_url,
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
        }
        for tx in transactions
    ]

    return {
        "credits": current_user.credits,
        "total_credits_purchased": current_user.total_credits_purchased,
        "plans": {
            "starter": {
                "name": "Starter",
                "price_usd": settings.billing_starter_price_usd,
                "credits": settings.billing_starter_credits,
            },
            "pro": {
                "name": "Pro",
                "price_usd": settings.billing_pro_price_usd,
                "credits": settings.billing_pro_credits,
            },
        },
        "history": tx_history,
    }


@router.post("/billing/checkout")
async def checkout(
    req: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new payment transaction and return payment checkout URL."""
    tier = req.package_tier.lower()
    if tier not in ("starter", "pro"):
        raise HTTPException(status_code=400, detail="Invalid package tier. Must be 'starter' or 'pro'.")

    tx, payment_url = await create_checkout_invoice(current_user, tier, session)

    return {
        "transaction_id": tx.id,
        "amount_usd": tx.amount_usd,
        "credits_added": tx.credits_added,
        "payment_url": payment_url,
        "status": tx.status,
    }


@router.post("/webhooks/crypto")
async def crypto_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Verified webhook listener for Cryptomus crypto payment notifications."""
    raw_body = await request.body()
    sign_header = request.headers.get("sign", "")

    # Live verification
    if settings.cryptomus_payment_key:
        if not verify_cryptomus_signature(raw_body, sign_header):
            logger.warning("Crypto webhook signature verification failed")
            raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    order_id = payload.get("order_id")
    payment_status = payload.get("status")
    provider_tx_id = payload.get("uuid")

    if not order_id:
        raise HTTPException(status_code=400, detail="Missing order_id")

    if payment_status in ("paid", "paid_over"):
        success = await fulfill_payment_transaction(order_id, provider_tx_id, session)
        if success:
            return {"status": "ok", "message": "Transaction fulfilled"}
        raise HTTPException(status_code=404, detail="Transaction not found")

    return {"status": "ignored", "message": f"Payment status is {payment_status}"}


@router.post("/billing/test-fulfill/{tx_id}")
async def test_fulfill_checkout(
    tx_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Sandbox endpoint to complete a test transaction during development."""
    res = await session.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.id == tx_id,
            PaymentTransaction.user_id == current_user.id,
        )
    )
    tx = res.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    success = await fulfill_payment_transaction(tx.id, "sandbox_test_tx", session)
    if not success:
        raise HTTPException(status_code=400, detail="Could not fulfill transaction")

    await session.refresh(current_user)
    return {
        "status": "paid",
        "credits": current_user.credits,
        "message": f"Successfully added {tx.credits_added} credits!",
    }
