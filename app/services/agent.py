"""Streaming Groq LLM Agent with tool calling and prompt injection defense."""
import os
import json
import logging
from typing import AsyncGenerator, Dict, Any, List, Set, Optional
from groq import AsyncGroq

from app.brand import get_brand_facts_text
from app.schemas.order import Order
from app.prompts.agent_prompt import build_system_prompt, GREETING_INSTRUCTION
from app.tools.schemas import TOOL_SCHEMAS
from app.tools.registry import execute_tool_call, normalize_order_id
from app.services.tts import SentenceSplitter

logger = logging.getLogger("app.services.agent")


class AriaAgent:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self.client = AsyncGroq(api_key=api_key)
        self.model = model

    async def run_turn(
        self,
        history: List[Dict[str, Any]],
        orders_db: Dict[str, Order],
        looked_up_orders: Set[str],
        current_customer: Optional[str] = None,
        session_state: Optional[Any] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        system_prompt = build_system_prompt(
            get_brand_facts_text(),
            current_user_name=current_customer or "",
            orders_db=orders_db
        )
        
        # Guardrail: Delimit untrusted customer speech inputs to prevent prompt injection
        formatted_history = []
        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if (
                role == "user"
                and content
                and content != GREETING_INSTRUCTION
                and not content.startswith("<customer_utterance>")
            ):
                formatted_history.append({"role": "user", "content": f"<customer_utterance>\n{content}\n</customer_utterance>"})
            else:
                formatted_history.append(msg)

        messages = [{"role": "system", "content": system_prompt}] + formatted_history

        for _ in range(3):
            tool_calls_collector = {}
            splitter = SentenceSplitter()
            streamed_text = ""

            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                stream=True,
                max_tokens=300,
                temperature=0.1,
            )

            async for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if not delta:
                    continue
                content = getattr(delta, "content", None)
                if content:
                    streamed_text += content
                    for sentence in splitter.process_token(content):
                        yield {"type": "sentence", "text": sentence}
                tool_calls = getattr(delta, "tool_calls", None)
                if tool_calls:
                    for tc in tool_calls:
                        idx = getattr(tc, "index", 0)
                        if idx not in tool_calls_collector:
                            tool_calls_collector[idx] = {
                                "id": getattr(tc, "id", None) or f"call_{idx}",
                                "name": "",
                                "arguments": ""
                            }
                        elif getattr(tc, "id", None) and not tool_calls_collector[idx]["id"]:
                            tool_calls_collector[idx]["id"] = tc.id

                        fn = getattr(tc, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                tool_calls_collector[idx]["name"] += fn.name
                            if getattr(fn, "arguments", None):
                                tool_calls_collector[idx]["arguments"] += fn.arguments

            for sentence in splitter.flush():
                yield {"type": "sentence", "text": sentence}

            if not tool_calls_collector:
                break

            messages.append({
                "role": "assistant",
                "content": streamed_text or None,
                "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in tool_calls_collector.values()],
            })

            for call in tool_calls_collector.values():
                name = call["name"]
                try:
                    args = json.loads(call["arguments"])
                except Exception:
                    args = {}

                order_id = normalize_order_id(args.get("order_id", ""))
                if order_id:
                    looked_up_orders.add(order_id)

                logger.info("Executing tool: %s(args=%s, customer=%s)", name, args, current_customer)
                yield {"type": "tool_call", "name": name, "order_id": order_id}

                res = execute_tool_call(name, args, orders_db, current_customer, session_state)
                logger.debug("Tool %s result: %s", name, res)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(res, ensure_ascii=False)})


def get_agent() -> AriaAgent:
    return AriaAgent(
        api_key=os.getenv("GROQ_API_KEY", "").strip(),
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile"
    )


