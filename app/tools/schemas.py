"""OpenAI / Groq function calling tool schemas."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_order_details",
            "description": "Look up order information, current status, and deterministic policy flags (returnable, cancellable, shipping fee, COD) by order ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Canonical or spoken order ID, e.g. 'ORD-101', '102'"}
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_order_cancellation",
            "description": "Step 1 of 2-step cancellation. Verifies cancellation eligibility in database and prepares confirmation. Call when customer first asks to cancel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order ID to cancel, e.g. 'ORD-103'"}
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_order_cancellation",
            "description": "Step 2 of 2-step cancellation. Commits cancellation to database ONLY after customer explicitly says yes/confirms. Fails if Step 1 was not called first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order ID being confirmed"},
                    "confirmed": {"type": "boolean", "description": "True if customer explicitly said yes/confirmed; False if customer declined"}
                },
                "required": ["order_id", "confirmed"],
            },
        },
    },
]
