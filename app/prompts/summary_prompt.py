"""Prompt for post-call structured summary generation."""

SUMMARY_SYSTEM_PROMPT = """Analyze this support conversation transcript and generate a structured JSON object matching this schema:
{
  "customer_intent": "ORDER_TRACKING" | "CANCELLATION" | "RETURN_REFUND" | "POLICY_QUESTION" | "OUT_OF_SCOPE" | "OTHER",
  "order_id": "ORD-XXX" or null,
  "resolution_status": "RESOLVED" | "UNRESOLVED" | "ESCALATION_NEEDED",
  "call_summary": "1 to 2 sentences summarizing the caller query and resolution."
}

RULES:
1. Output valid JSON only, without markdown backticks or commentary.
2. If no conversation occurred, intent is 'OTHER', resolution is 'UNRESOLVED'.
3. Only report an order_id if one was legitimately discussed or looked up.
"""
