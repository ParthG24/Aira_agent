"""Aura Skincare - Brand Facts, Policies, and Mock Database.

Single source of truth for prompt, tools, and UI test panel.
"""
from typing import Dict, Any, Optional
from app.schemas.order import Order

BRAND_NAME = "Aura Skincare"
SHIPPING_FREE_THRESHOLD = 499  # Free ONLY strictly above 499 (499 pays 50)
SHIPPING_FEE = 50
DELIVERY_TIMELINE = "3-5 business days"
RETURN_WINDOW_DAYS = 7
DAMAGE_REPORT_WINDOW_HOURS = 48
COD_MAX_LIMIT = 2500

CANCELLABLE_STATUSES = {"Processing"}
NON_CANCELLABLE_STATUSES = {"Shipped", "Out for Delivery", "Delivered"}


INITIAL_ORDERS: Dict[str, Order] = {
    "ORD-101": Order(
        order_id="ORD-101",
        customer="Priya Sharma",
        product="Vitamin C Serum (30ml)",
        value=699,
        status="Out for Delivery",
        carrier_notes="BlueDart BD-982103, expected by 6 PM today",
    ),
    "ORD-102": Order(
        order_id="ORD-102",
        customer="Rahul Verma",
        product="Hydrating Sunscreen SPF 50",
        value=499,
        status="Delivered",
        carrier_notes="Delhivery DL-441029, delivered 14 days ago",
        delivered_days_ago=14,
    ),
    "ORD-103": Order(
        order_id="ORD-103",
        customer="Ananya Patel",
        product="Green Tea Face Wash + Toner",
        value=850,
        status="Processing",
        carrier_notes="Ordered 3 hours ago, eligible for cancellation",
    ),
}


def compute_order_policy(order: Order) -> Dict[str, Any]:
    """Computes authoritative policy flags deterministically from order data."""
    is_cancellable = order.status in CANCELLABLE_STATUSES
    cancellation_notes = (
        "Eligible for cancellation." if is_cancellable else
        f"Cannot be cancelled (status: '{order.status}'). Advise customer to refuse at doorstep."
    )

    if order.status != "Delivered":
        is_returnable = False
        return_notes = "Not delivered yet. Returns apply only after delivery."
    elif order.delivered_days_ago is not None and order.delivered_days_ago > RETURN_WINDOW_DAYS:
        is_returnable = False
        return_notes = f"Delivered {order.delivered_days_ago} days ago (exceeds {RETURN_WINDOW_DAYS}-day return window)."
    else:
        is_returnable = True
        return_notes = f"Within {RETURN_WINDOW_DAYS}-day window (unopened original packaging only)."

    return {
        "is_cancellable": is_cancellable,
        "cancellation_notes": cancellation_notes,
        "is_returnable": is_returnable,
        "return_notes": return_notes,
        "shipping_fee": 0 if order.value > SHIPPING_FREE_THRESHOLD else SHIPPING_FEE,
        "cod_eligible": order.value <= COD_MAX_LIMIT,
    }


def get_brand_facts_text() -> str:
    return f"""BRAND & POLICIES ({BRAND_NAME}):
- Shipping: Free for orders strictly above ₹{SHIPPING_FREE_THRESHOLD}. Orders of ₹{SHIPPING_FREE_THRESHOLD} or below pay ₹{SHIPPING_FEE}. Delivery: {DELIVERY_TIMELINE}.
- Returns: Allowed within {RETURN_WINDOW_DAYS} days of delivery for unopened items in original packaging. Opened items or requests past {RETURN_WINDOW_DAYS} days cannot be returned. Damaged items must be reported within {DAMAGE_REPORT_WINDOW_HOURS}h for replacement.
- Cancellations: Only 'Processing' orders can be cancelled. Shipped/Out for Delivery orders cannot be cancelled; advise customer to refuse at doorstep.
- COD: Available up to ₹{COD_MAX_LIMIT} only. Above ₹{COD_MAX_LIMIT} must be prepaid."""
