"""Per-call voice session, turn loop, authorization context, and barge-in handling."""
import os
import json
import asyncio
import logging
from typing import Dict, Set, Optional
from fastapi import WebSocket
from groq import AsyncGroq

from app.schemas.order import Order
from app.schemas.summary import CallSummary, CustomerIntent, ResolutionStatus
from app.brand import INITIAL_ORDERS
from app.prompts.agent_prompt import GREETING_INSTRUCTION, get_customer_salutation
from app.prompts.summary_prompt import SUMMARY_SYSTEM_PROMPT
from app.services.stt import get_stt
from app.services.tts import synthesize_sentence
from app.services.agent import get_agent

logger = logging.getLogger("app.session")


class CallSession:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.history: list[dict] = []
        self.orders_db: Dict[str, Order] = {k: Order(**v.model_dump()) for k, v in INITIAL_ORDERS.items()}
        self.current_customer: str = "Priya Sharma"  # Default active customer (ORD-101 owner)
        self.pending_cancellation_order_id: Optional[str] = None
        self.looked_up_orders: Set[str] = set()
        self.current_turn_task: asyncio.Task | None = None
        self.silence_task: asyncio.Task | None = None
        self.silence_phase: int = 0
        self.stt = get_stt()
        self.agent = get_agent()
        self.is_active = True
        self.is_greeting = False

    def set_active_customer(self, customer_name: str):
        """Sets authenticated caller context for authorization and IDOR protection."""
        self.current_customer = customer_name.strip()
        logger.info("Active customer set to: '%s'", self.current_customer)

    def cancel_active_turn(self):
        if self.current_turn_task and not self.current_turn_task.done():
            self.current_turn_task.cancel()
        self.current_turn_task = None

    def reset_silence_timer(self):
        if self.silence_task and not self.silence_task.done():
            self.silence_task.cancel()
        self.silence_task = None
        self.silence_phase = 0

    def start_silence_watcher(self):
        if self.silence_task and not self.silence_task.done():
            self.silence_task.cancel()
        self.silence_task = None
        # Strict guard: never start silence timer while agent is greeting or processing/speaking a turn
        if not self.is_active or self.is_greeting or (self.current_turn_task and not self.current_turn_task.done()):
            return
        self.silence_task = asyncio.create_task(self._silence_flow())

    async def _silence_flow(self):
        try:
            # Count exactly 10 seconds of silence strictly after playback has completed
            await asyncio.sleep(10.0)
            if not self.is_active or (self.current_turn_task and not self.current_turn_task.done()):
                return

            salutation = get_customer_salutation(self.current_customer)

            if self.silence_phase == 0:
                # Phase 0: 10s silence after agent completed speaking -> Prompt caller
                self.silence_phase = 1
                prompt_text = f"Take your time, {salutation}! Let me know if you're still there or if you need any help."
                logger.info("Silence detected (10s after agent completed speaking) -> Prompting caller: '%s'", prompt_text)
                self.history.append({"role": "assistant", "content": prompt_text})
                await self.send_json({"type": "transcript", "role": "assistant", "text": prompt_text})
                await self.send_json({"type": "state", "state": "speaking"})
                audio = await synthesize_sentence(prompt_text)
                if audio:
                    await self.send_audio(audio, sentence_id=990)
                await self.send_json({"type": "state", "state": "listening"})
                # When this reminder finishes playing on the client, client will send 'playback_done'
                # which invokes start_silence_watcher() for phase 1.

            elif self.silence_phase == 1:
                # Phase 1: 10s silence continued after reminder finished playing -> Disconnect gracefully
                self.silence_phase = 2
                disconnect_text = f"It seems we might have gotten disconnected, {salutation}. Thank you for calling Aura Skincare, have a wonderful day!"
                logger.info("Silence detected (10s after reminder playback) -> Disconnecting caller: '%s'", disconnect_text)
                self.history.append({"role": "assistant", "content": disconnect_text})
                await self.send_json({"type": "transcript", "role": "assistant", "text": disconnect_text})
                await self.send_json({"type": "state", "state": "speaking"})
                audio = await synthesize_sentence(disconnect_text)
                if audio:
                    await self.send_audio(audio, sentence_id=991)
                # Allow a short grace period for the audio to reach the client before closing
                await asyncio.sleep(3.5)
                await self.end_call()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Silence watcher error: %s", e)

    async def send_json(self, data: dict):
        if self.ws:
            try:
                await self.ws.send_json(data)
            except Exception as e:
                logger.debug("send_json error: %s", e)

    async def send_audio(self, audio_bytes: bytes, sentence_id: int):
        if self.is_active and audio_bytes:
            try:
                await self.send_json({"type": "audio_start", "sentence_id": sentence_id})
                await self.ws.send_bytes(audio_bytes)
                await self.send_json({"type": "audio_end", "sentence_id": sentence_id})
            except Exception as e:
                logger.warning("Audio send error: %s", e)

    async def start_call(self):
        """Kicks off natural customer service greeting turn immediately on every call start."""
        self.is_greeting = True
        salutation = get_customer_salutation(self.current_customer)
        greeting_text = f"Hi {salutation}! Thanks for calling Aura Skincare, my name is Aria. How can I help you today?"
        self.history.append({"role": "assistant", "content": greeting_text})
        await self.send_json({"type": "transcript", "role": "assistant", "text": greeting_text})
        await self.send_json({"type": "state", "state": "speaking"})
        audio = await synthesize_sentence(greeting_text)
        if audio:
            await self.send_audio(audio, sentence_id=1)
        self.is_greeting = False
        await self.send_json({"type": "turn_complete"})
        await self.send_json({"type": "state", "state": "listening"})

    async def handle_user_text(self, text: str):
        if not self.is_active:
            return
        clean = text.strip()
        if not clean:
            return
        self.reset_silence_timer()
        logger.info("Customer message: '%s' (Caller: %s)", clean, self.current_customer)
        self.cancel_active_turn()
        self.history.append({"role": "user", "content": clean})
        await self.send_json({"type": "transcript", "role": "user", "text": clean})
        self.current_turn_task = asyncio.create_task(self._run_agent_turn())

    async def handle_audio_bytes(self, pcm_bytes: bytes):
        if not self.is_active or len(pcm_bytes) > 1024 * 1024 or self.is_greeting:
            return
        is_speech, text = await self.stt.transcribe(pcm_bytes)
        if not self.is_active:
            return
        if is_speech and text:
            self.reset_silence_timer()
            await self.handle_user_text(text)
        else:
            if self.is_active:
                await self.send_json({"type": "state", "state": "listening"})

    async def handle_barge_in(self):
        if not self.is_active:
            return
        logger.info("Barge-in triggered; cancelling current speech turn")
        self.reset_silence_timer()
        self.cancel_active_turn()
        await self.send_json({"type": "state", "state": "listening"})

    async def end_call(self):
        if not self.is_active and not self.history:
            return
        self.is_active = False
        self.reset_silence_timer()
        self.cancel_active_turn()
        logger.info("Ending call; generating post-call summary")
        try:
            summary = await self._generate_summary()
            await self.send_json({"type": "summary", "data": summary.model_dump(), "transcript": self.history})
            logger.info("Summary successfully sent to client: intent=%s", summary.customer_intent)
        except Exception as e:
            logger.error("Error generating or sending summary: %s", e)
        await self.send_json({"type": "state", "state": "idle"})
        await self.send_json({"type": "call_ended"})

    async def _run_agent_turn(self, is_greeting: bool = False):
        await self.send_json({"type": "state", "state": "thinking"})
        spoken = []
        sentence_id = 0

        try:
            async for ev in self.agent.run_turn(
                self.history,
                self.orders_db,
                self.looked_up_orders,
                current_customer=self.current_customer,
                session_state=self
            ):
                if ev["type"] == "tool_call":
                    await self.send_json(ev)
                elif ev["type"] == "sentence":
                    sent = ev["text"]
                    sentence_id += 1
                    spoken.append(sent)
                    await self.send_json({"type": "transcript", "role": "assistant", "text": sent})
                    await self.send_json({"type": "state", "state": "speaking"})
                    audio = await synthesize_sentence(sent)
                    if audio:
                        await self.send_audio(audio, sentence_id=sentence_id)
                    else:
                        await self.send_json({"type": "tts_fallback", "text": sent})

            full = " ".join(spoken).strip()
            if full:
                if is_greeting and self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                self.history.append({"role": "assistant", "content": full})
                logger.info("Aria replied: '%s'", full)

        except asyncio.CancelledError:
            if spoken:
                self.history.append({"role": "assistant", "content": " ".join(spoken)})
            raise
        except Exception as e:
            logger.error("Turn error: %s", e)
            await self.send_json({"type": "error", "message": "Connection error with speech services."})
        finally:
            await self.send_json({"type": "turn_complete"})
            await self.send_json({"type": "state", "state": "listening"})


    async def _generate_summary(self) -> CallSummary:
        """Extracts validated post-call summary."""
        user_turns = [m for m in self.history if m.get("role") == "user" and m.get("content", "").strip()]
        if not user_turns:
            return CallSummary(
                customer_intent=CustomerIntent.OTHER,
                order_id=None,
                resolution_status=ResolutionStatus.UNRESOLVED,
                call_summary="Call ended before customer conversation took place.",
            )

        transcript = "\n".join(
            f"{'Customer' if m.get('role') == 'user' else 'Aria'}: {m.get('content', '')}"
            for m in self.history if m.get("content")
        )

        client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY", "").strip())
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"

        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": f"TRANSCRIPT:\n{transcript}"}
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=600,
            )
            data = json.loads(resp.choices[0].message.content or "{}")

            # Ground order_id: must be one actually looked up during call
            raw_order = data.get("order_id")
            if raw_order and raw_order not in self.looked_up_orders:
                matched = [o for o in self.looked_up_orders if o in str(raw_order)]
                data["order_id"] = matched[0] if matched else (list(self.looked_up_orders)[0] if self.looked_up_orders else None)

            return CallSummary.model_validate(data)
        except Exception as e:
            logger.warning("Summary generation error: %s", e)
            return CallSummary(
                customer_intent=CustomerIntent.OTHER,
                order_id=list(self.looked_up_orders)[0] if self.looked_up_orders else None,
                resolution_status=ResolutionStatus.RESOLVED if user_turns else ResolutionStatus.UNRESOLVED,
                call_summary="Call completed. Summary could not be extracted automatically.",
            )

