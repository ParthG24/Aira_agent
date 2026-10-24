"""Pydantic schemas for Aura Skincare orders and deterministic policies."""
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class Order(BaseModel):
    order_id: str
    customer: str = "Valued Customer"
    product: str
    value: int
    status: str
    carrier_notes: str
    delivered_days_ago: Optional[int] = None


class PolicyFlags(BaseModel):
    is_cancellable: bool
    cancellation_notes: str
    is_returnable: bool
    return_notes: str
    shipping_fee: int
    cod_eligible: bool


class OrderLookupResult(BaseModel):
    found: bool
    authorized: bool = True
    order_id: str
    customer: Optional[str] = None
    product: Optional[str] = None
    value: Optional[int] = None
    status: Optional[str] = None
    carrier_notes: Optional[str] = None
    delivered_days_ago: Optional[int] = None
    policy: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
