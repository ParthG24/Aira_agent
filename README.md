# Aura Skincare — AI Voice Support Agent (Aria)

A real-time, browser-based voice customer support agent built for **Aura Skincare**. You can open the web app, click **Start Call**, speak naturally, and get instant spoken replies with strict brand policy enforcement.

---

## Simple Architecture

```mermaid
flowchart LR
    User([Customer Voice]) <-->|Audio Stream| Browser[Web Browser]
    Browser <-->|WebSocket /ws/call| Server[FastAPI Backend]
    
    subgraph Server_Pipeline [Voice & Logic Pipeline]
        Server --> STT[Groq Whisper STT]
        STT --> LLM[Groq LLM Reasoning]
        LLM <--> Tools[Deterministic Python Guardrails & DB]
        LLM --> TTS[Sarvam AI Voice / Edge-TTS]
    end
    
    TTS --> Browser
```

### The Core Principle: "Never Trust the LLM"
* **The LLM handles the conversation:** Tone, politeness, empathy, and natural Indian English phrasing (*"Sir"*, *"Ma'am"*).
* **Deterministic Python code handles the rules:** The LLM is never allowed to calculate refund amounts, return windows, or decide if someone is allowed to see an order. All business logic and security checks live in regular Python code.

---

## Key Guardrails

1. **IDOR & Caller Privacy Protection:**
   The backend verifies order ownership in Python before giving data to the LLM. If Priya Sharma asks for Rahul Verma's order (`ORD-102`), the tool rejects it directly. The LLM never sees another customer's data.
2. **2-Step Confirmation for Cancellations:**
   Destructive actions cannot happen on an LLM's whim. Step 1 checks if the order is still `Processing` and flags a pending confirmation. Only after a second explicit verbal confirmation from the user does the tool cancel the order.
3. **Hard-Coded Business Policies:**
   - **Returns:** Strictly within 7 days of delivery and unopened.
   - **Shipping:** Free strictly above ₹499 (₹499 pays ₹50 shipping).
   - **Cash on Delivery (COD):** Allowed only up to ₹2,500.
   - The LLM does not calculate dates or math; Python code evaluates the rules and provides the answer.
4. **Out-of-Scope Deflection:**
   Non-brand requests (like booking flights or general trivia) are politely deflected back to Aura Skincare.

---

## Voice Experience Features

* **Real-Time Barge-In (Interruption):** If the customer speaks while Aria is speaking, playback cuts off instantly and the server stops the current turn.
* **10-Second Silence Detection:** After Aria finishes speaking, a 10-second timer starts. If the user is silent, Aria asks if they are still there (*"Take your time, Ma'am!"*). After another 10 seconds of silence, she gracefully ends the call.
* **Natural Indian Voice:** Uses Sarvam AI (`bulbul:v3`, voice *Shreya*) with automatic fallback to Edge-TTS (`en-IN-NeerjaNeural`).
* **Post-Call Structured Summary:** When the call ends, the backend generates an enum-validated JSON summary (`customer_intent`, `order_id`, `resolution_status`, `call_summary`) validated by Pydantic.

---

## Quickstart & Local Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and add your API keys:
```env
GROQ_API_KEY=your_groq_api_key
SARVAM_API_KEY=your_sarvam_api_key
GROQ_MODEL=openai/gpt-oss-20b
PORT=8000
```
*(Note: If `SARVAM_API_KEY` is not provided, it automatically falls back to Edge-TTS.)*

### 3. Run the Application
```bash
uvicorn app.main:app --reload --port 8000
```
Open **http://localhost:8000** in your browser and click **Start Call**.

---

## Testing & Evaluation

* **Unit Tests (14 tests, offline, <1.5s):**
  ```bash
  pytest tests/ -v
  ```
* **Automated Scenarios Evaluation (14 test cases):**
  ```bash
  python scripts/run_scenarios.py
  ```
  Results are saved to `docs/eval_results.md`.

---

## Section 9: Evaluation Questions & Answers

### 1. Why did you choose this architecture and tech stack?
I wanted the agent to respond quickly and feel like a real conversation. WebSockets let me send audio and messages through one connection, Groq gives fast responses, and streaming TTS (Sarvam AI / Edge-TTS) converts replies into speech while the AI is still generating them. I also added a backup speech system in case the main one fails.

### 2. What was the most difficult part and how did you solve it?
The hardest part was handling interruptions while the AI was speaking. I had to coordinate the browser's audio capture, playback queue, WebSocket, and backend tasks. I added calibrated VAD checks to avoid cancelling speech due to small background noises. When interrupted, the client stops audio immediately and tells the server to cancel the task. I also made sure only sentences actually sent to the client are saved in the conversation history.

### 3. If you had one more week, what would you improve first and why?
I'd improve speech recognition first by moving it to the server using Silero VAD and Whisper or Deepgram with WebRTC. This would improve browser compatibility and make interruptions even more responsive. After that, I'd add an expanded product knowledge base for skincare questions and improve echo cancellation to avoid false interruptions.

### 4. What would need to change if the agent handled 1,000 conversations a day?
I'd move session data to Redis, use PostgreSQL for order management with row-level locking on cancellations, and add fallback LLM providers if Groq hits rate limits. I'd cache common responses to reduce TTS latency, add monitoring to track errors and response times, and mask personal information in transcripts to improve privacy and comply with applicable data protection requirements.
