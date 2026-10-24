"""Deterministic business tools with authorization (IDOR protection) and 2-step consent."""
import re
import logging
from typing import Dict, Any, Optional
from app.schemas.order import Order
from app.brand import compute_order_policy

logger = logging.getLogger("app.tools")


def normalize_order_id(raw_id: str) -> str:
    """Extracts numeric digits to format canonical 'ORD-<number>'."""
    if not raw_id:
        return ""
    digits = re.findall(r"\d+", str(raw_id))
    return f"ORD-{digits[0]}" if digits else raw_id.strip().upper()


def execute_get_order_details(
    order_id: str,
    orders_db: Dict[str, Order],
    current_customer: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieves order details with caller authorization check."""
    clean_id = normalize_order_id(order_id)
    order = orders_db.get(clean_id)
    if not order:
        return {
            "found": False,
            "authorized": True,
            "order_id": order_id,
            "message": f"No order found for '{order_id}'. Please ask the customer to verify the order ID."
        }

    # Authorization Check (IDOR prevention):
    if current_customer and order.customer and order.customer.lower() != current_customer.lower():
        logger.warning(
            "Authorization denied: Order %s belongs to '%s', caller is '%s'",
            clean_id, order.customer, current_customer
        )
        return {
            "found": True,
            "authorized": False,
            "order_id": clean_id,
            "message": f"Order {clean_id} belongs to another customer and is not associated with {current_customer}'s account. Tell the customer warmly: 'Your account doesn't have an order with ID {clean_id}. For privacy, I can only look up orders placed under your own account.'"
        }

    return {
        "found": True,
        "authorized": True,
        "order_id": order.order_id,
        "customer": order.customer,
        "product": order.product,
        "value": order.value,
        "status": order.status,
        "carrier_notes": order.carrier_notes,
        "delivered_days_ago": order.delivered_days_ago,
        "policy": compute_order_policy(order),
    }


def execute_request_cancellation(
    order_id: str,
    orders_db: Dict[str, Order],
    current_customer: Optional[str] = None,
    session_state: Optional[Any] = None
) -> Dict[str, Any]:
    """Step 1 of 2-step consent: validates policy and registers pending cancellation."""
    clean_id = normalize_order_id(order_id)
    order = orders_db.get(clean_id)
    if not order:
        return {"success": False, "message": f"Order '{order_id}' was not found."}

    # Authorization Check:
    if current_customer and order.customer and order.customer.lower() != current_customer.lower():
        return {
            "success": False,
            "authorized": False,
            "order_id": clean_id,
            "message": f"Unauthorized: You cannot cancel order {clean_id} because it belongs to another customer."
        }

    policy = compute_order_policy(order)
    if not policy["is_cancellable"]:
        return {
            "success": False,
            "order_id": clean_id,
            "status": order.status,
            "message": f"Order {clean_id} cannot be cancelled (status: '{order.status}'). Advise customer to refuse delivery at the doorstep.",
        }

    # Register deterministic pending consent in session:
    if session_state:
        session_state.pending_cancellation_order_id = clean_id

    return {
        "success": True,
        "status": "CONFIRMATION_REQUIRED",
        "order_id": clean_id,
        "message": f"Order {clean_id} is eligible for cancellation. Ask customer explicitly: 'Are you sure you want to cancel order {clean_id}?'"
    }


def execute_confirm_cancellation(
    order_id: str,
    confirmed: bool,
    orders_db: Dict[str, Order],
    current_customer: Optional[str] = None,
    session_state: Optional[Any] = None
) -> Dict[str, Any]:
    """Step 2 of 2-step consent: commits cancellation only after explicit confirmation."""
    clean_id = normalize_order_id(order_id)
    order = orders_db.get(clean_id)
    if not order:
        return {"success": False, "message": f"Order '{order_id}' was not found."}

    # Verify Step 1 occurred:
    pending_id = getattr(session_state, "pending_cancellation_order_id", None) if session_state else None
    if pending_id != clean_id:
        return {
            "success": False,
            "message": f"Cannot confirm cancellation: No pending cancellation request was initiated for {clean_id}. Please request cancellation first."
        }

    if not confirmed:
        if session_state:
            session_state.pending_cancellation_order_id = None
        return {
            "success": True,
            "status": "ABORTED",
            "message": f"Cancellation of order {clean_id} was aborted by customer."
        }

    # Commit cancellation:
    order.status = "Cancelled"
    order.carrier_notes = "Cancelled by customer via Aria (2-step consent verified)"
    if session_state:
        session_state.pending_cancellation_order_id = None

    logger.info("Order %s successfully cancelled with 2-step consent", clean_id)
    return {
        "success": True,
        "status": "Cancelled",
        "order_id": clean_id,
        "message": f"Order {clean_id} has been cancelled successfully in the database."
    }


def execute_tool_call(
    name: str,
    args: Dict[str, Any],
    orders_db: Dict[str, Order],
    current_customer: Optional[str] = None,
    session_state: Optional[Any] = None
) -> Dict[str, Any]:
    """Central tool execution dispatcher."""
    order_id = args.get("order_id", "")
    if name == "get_order_details":
        return execute_get_order_details(order_id, orders_db, current_customer)
    elif name == "request_order_cancellation":
        return execute_request_cancellation(order_id, orders_db, current_customer, session_state)
    elif name == "confirm_order_cancellation":
        confirmed = args.get("confirmed", True)
        return execute_confirm_cancellation(order_id, confirmed, orders_db, current_customer, session_state)
    elif name == "cancel_order":
        # Backward compatibility fallback
        res1 = execute_request_cancellation(order_id, orders_db, current_customer, session_state)
        if res1.get("status") == "CONFIRMATION_REQUIRED":
            return execute_confirm_cancellation(order_id, True, orders_db, current_customer, session_state)
        return res1
    else:
        return {"error": f"Unknown tool: '{name}'"}
