/**
 * Aura Skincare - Aria Voice Support Client.
 * Handles microphone capture, calibrated human-speech VAD, WebSockets, barge-in,
 * waveform visualization, and gapless audio playback.
 */

let ws = null;
let audioCtx = null;
let micStream = null;
let scriptProcessor = null;
let micSource = null;

let currentState = "idle"; // "idle" | "listening" | "thinking" | "speaking"
let currentCustomer = "Priya Sharma"; // Active authenticated user
let audioQueue = [];
let isPlayingAudio = false;
let currentSourceNode = null;
let serverTurnPending = false; // True while server is generating and streaming audio for an active turn

let isSpeakingUtterance = false;
let audioBufferAccumulator = [];
let preRollBuffer = [];
let silenceSamples = 0;
let consecutiveSpeechFrames = 0;
let echoCooldownUntil = 0;

let callTimerInterval = null;
let callStartTimestamp = 0;

const SAMPLE_RATE = 16000;
const SILENCE_LIMIT_SAMPLES = Math.round(SAMPLE_RATE * 0.8); // 800ms natural conversational pause

// DOM Elements
const btnStart = document.getElementById("btnStartCall");
const btnEnd = document.getElementById("btnEndCall");
const statusDot = document.getElementById("statusDot");
const stateLabel = document.getElementById("stateLabel");
const waveContainer = document.getElementById("waveContainer");
const transcriptFeed = document.getElementById("transcriptFeed");
const callTimer = document.getElementById("callTimer");
const summaryModal = document.getElementById("summaryModal");
const summaryJsonBlock = document.getElementById("summaryJsonBlock");
const btnCloseModal = document.getElementById("btnCloseModal");
const btnDismiss = document.getElementById("btnDismiss");
const btnCopySummary = document.getElementById("btnCopySummary");
const waveBars = document.querySelectorAll(".wave-bar");
const limeOrb = document.getElementById("limeOrb");
const heroPromptText = document.getElementById("heroPromptText");
const btnSendChat = document.getElementById("btnSendChat");
const chatInput = document.getElementById("chatInput");
const btnDockBot = document.getElementById("btnDockBot");

function setState(state) {
  currentState = state;
  if (statusDot) {
    statusDot.className = `status-dot dot-${state}`;
  }
  if (waveContainer) {
    waveContainer.className = `audio-wave-container ${state}`;
  }
  if (limeOrb) {
    limeOrb.className = `lime-3d-orb ${state}`;
  }

  const heroTitles = {
    idle: "How Can I Help You Today?",
    listening: "Aria is Listening...",
    thinking: "Checking Records...",
    speaking: "Aria is Speaking..."
  };
  if (heroPromptText) {
    heroPromptText.textContent = heroTitles[state] || "How Can I Help You Today?";
  }

  const descriptions = {
    idle: "Ready to connect",
    listening: "Aria listening... Speak naturally",
    thinking: "Checking records...",
    speaking: "Aria speaking (interrupt anytime)",
  };
  if (stateLabel) {
    stateLabel.textContent = descriptions[state] || "";
  }
}

// --- Audio Capture & VAD ---
function downsampleTo16k(inputData, inputSampleRate) {
  if (inputSampleRate === 16000) return floatToPCM16(inputData);
  const ratio = inputSampleRate / 16000;
  const newLen = Math.round(inputData.length / ratio);
  const result = new Int16Array(newLen);
  let offsetResult = 0;
  let offsetInput = 0;
  while (offsetResult < result.length) {
    const nextOffsetInput = Math.round((offsetResult + 1) * ratio);
    let sum = 0, count = 0;
    for (let i = offsetInput; i < nextOffsetInput && i < inputData.length; i++) {
      sum += inputData[i];
      count++;
    }
    const val = Math.max(-1, Math.min(1, count > 0 ? sum / count : 0));
    result[offsetResult++] = val < 0 ? val * 0x8000 : val * 0x7fff;
    offsetInput = nextOffsetInput;
  }
  return result;
}

function floatToPCM16(input) {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    output[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return output;
}

async function startAudioCapture() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!audioCtx || audioCtx.state === "closed") audioCtx = new AudioContextClass();
  if (audioCtx.state === "suspended") await audioCtx.resume();

  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (err) {
    alert("Microphone access is required to speak with Aria. Please grant permission in your browser.");
    throw err;
  }

  micSource = audioCtx.createMediaStreamSource(micStream);
  scriptProcessor = audioCtx.createScriptProcessor(2048, 1, 1);
  micSource.connect(scriptProcessor);
  scriptProcessor.connect(audioCtx.destination);

  scriptProcessor.onaudioprocess = (e) => {
    e.outputBuffer.getChannelData(0).fill(0); // Prevent feedback loop to speakers
    if (currentState === "idle" || !ws || ws.readyState !== WebSocket.OPEN) return;

    const inputData = e.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < inputData.length; i++) sum += inputData[i] * inputData[i];
    const rms = Math.sqrt(sum / inputData.length);

    // Dynamic wave bar animation based on live speech energy
    if (waveBars && waveBars.length > 0 && (currentState === "listening" || currentState === "speaking")) {
      const hBase = Math.min(48, Math.max(10, rms * 450));
      waveBars.forEach((bar, idx) => {
        const factor = 0.7 + (idx % 3) * 0.25;
        bar.style.height = `${Math.round(hBase * factor)}px`;
      });
    }

    const now = Date.now();
    const isSpeakingNow = (currentState === "speaking" || isPlayingAudio);
    const inCooldown = (now < echoCooldownUntil);

    if (isSpeakingNow || inCooldown) {
      preRollBuffer = [];
      if (!isSpeakingUtterance) consecutiveSpeechFrames = 0;
    }

    // Calibrated VAD:
    // - During Aria playback (barge-in): 0.030 threshold on 2 frames (~80ms).
    //   Safely ignores speaker audio bleed (0.015-0.022), but triggers reliably on deliberate human voice.
    // - During echo cooldown: 0.022
    // - Normal listening: 0.015 on 2 frames (~80ms) — ignores quiet background noise while catching conversational speech.
    const threshold = isSpeakingNow ? 0.030 : (inCooldown ? 0.022 : 0.015);
    const minFrames = 2;
    const pcm16Chunk = downsampleTo16k(inputData, audioCtx.sampleRate);

    if (rms > threshold) {
      consecutiveSpeechFrames++;
      if (consecutiveSpeechFrames >= minFrames) {
        silenceSamples = 0;
        if (!isSpeakingUtterance) {
          isSpeakingUtterance = true;
          audioBufferAccumulator = [...preRollBuffer];
          preRollBuffer = [];
          if (isSpeakingNow) handleClientBargeIn();
        }
      }
      if (isSpeakingUtterance) audioBufferAccumulator.push(pcm16Chunk);
    } else {
      consecutiveSpeechFrames = 0;
      if (isSpeakingUtterance) {
        silenceSamples += pcm16Chunk.length;
        audioBufferAccumulator.push(pcm16Chunk);
        if (silenceSamples >= SILENCE_LIMIT_SAMPLES) {
          isSpeakingUtterance = false;
          silenceSamples = 0;
          dispatchUtterance();
        }
      } else if (!isSpeakingNow && !inCooldown) {
        preRollBuffer.push(pcm16Chunk);
        if (preRollBuffer.length > 16) preRollBuffer.shift(); // 16 chunks = ~650ms cushion
      }
    }
  };
}

function dispatchUtterance() {
  if (currentState === "idle" || audioBufferAccumulator.length === 0) {
    audioBufferAccumulator = [];
    return;
  }

  let totalLen = 0, maxAbs = 0;
  for (const arr of audioBufferAccumulator) {
    totalLen += arr.length;
    for (let i = 0; i < arr.length; i++) {
      const v = Math.abs(arr[i]);
      if (v > maxAbs) maxAbs = v;
    }
  }

  // Filter out tiny clicks or room noise (< 200ms or amplitude < 260)
  if (totalLen < 3200 || maxAbs < 260) {
    audioBufferAccumulator = [];
    return;
  }

  const merged = new Int16Array(totalLen);
  let offset = 0;
  for (const arr of audioBufferAccumulator) {
    merged.set(arr, offset);
    offset += arr.length;
  }
  audioBufferAccumulator = [];

  if (currentState !== "idle" && ws && ws.readyState === WebSocket.OPEN) {
    serverTurnPending = true;
    ws.send(merged.buffer);
  }
}

// --- Barge-in & Playback Handling ---
function handleClientBargeIn() {
  serverTurnPending = false;
  echoCooldownUntil = Date.now() + 350;
  stopAudioPlayback();
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "barge_in" }));
  }
  setState("listening");
}

function stopAudioPlayback() {
  audioQueue = [];
  isPlayingAudio = false;
  if (currentSourceNode) {
    try {
      currentSourceNode.stop();
      currentSourceNode.disconnect();
    } catch (e) {}
    currentSourceNode = null;
  }
}

async function queueAudioPlayback(arrayBuffer) {
  if (!audioCtx) return;
  try {
    const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer.slice(0));
    audioQueue.push(audioBuffer);
    if (!isPlayingAudio) playNextInQueue();
  } catch (err) {
    console.error("Audio decode error:", err);
  }
}

function playNextInQueue() {
  if (audioQueue.length === 0) {
    const wasSpeaking = isPlayingAudio || currentState === "speaking";
    isPlayingAudio = false;
    currentSourceNode = null;
    if (currentState === "speaking") setState("listening");
    // Strictly send playback_done ONLY when server has sent all speech for this turn AND playback finished
    if (wasSpeaking && !serverTurnPending && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "playback_done" }));
    }
    return;
  }


  isPlayingAudio = true;
  setState("speaking");

  const buffer = audioQueue.shift();
  currentSourceNode = audioCtx.createBufferSource();
  currentSourceNode.buffer = buffer;
  currentSourceNode.connect(audioCtx.destination);

  currentSourceNode.onended = () => {
    echoCooldownUntil = Date.now() + 250;
    playNextInQueue();
  };

  currentSourceNode.start();
}

// --- Transcript Rendering ---
function appendTranscript(role, text) {
  const emptyState = transcriptFeed.querySelector(".empty-state");
  if (emptyState) emptyState.remove();

  // If previous bubble is from assistant, append streamed sentences smoothly
  const lastBubble = transcriptFeed.lastElementChild;
  if (role === "assistant" && lastBubble && lastBubble.classList.contains("assistant") && lastBubble.classList.contains("chat-row")) {
    const textEl = lastBubble.querySelector(".bubble-text");
    if (textEl && !textEl.textContent.endsWith(text)) {
      textEl.textContent += " " + text;
      transcriptFeed.scrollTop = transcriptFeed.scrollHeight;
      return;
    }
  }

  const row = document.createElement("div");
  row.className = `chat-row ${role}`;

  const author = document.createElement("span");
  author.className = "chat-author";
  if (role === "assistant") {
    author.innerHTML = `<span class="lime-crescent" style="display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--lime);box-shadow:0 0 5px var(--lime);"></span> Aria AI`;
  } else {
    author.textContent = currentCustomer;
  }

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  
  const textEl = document.createElement("div");
  textEl.className = "bubble-text";
  textEl.textContent = text;
  bubble.appendChild(textEl);

  row.appendChild(author);
  row.appendChild(bubble);
  transcriptFeed.appendChild(row);
  transcriptFeed.scrollTop = transcriptFeed.scrollHeight;
}

// --- WebSocket Connection ---
async function connectWebSocket() {
  await startAudioCapture();

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${window.location.host}/ws/call`);
  ws.binaryType = "arraybuffer";

  btnStart.disabled = true;
  btnEnd.disabled = false;

  ws.onopen = () => {
    setState("listening");
    transcriptFeed.innerHTML = "";
    ws.send(JSON.stringify({ type: "set_user", customer: currentCustomer }));
    ws.send(JSON.stringify({ type: "start_call" }));
    startTimer();
  };

  ws.onmessage = (event) => {
    if (typeof event.data === "string") {
      const msg = JSON.parse(event.data);
      if (msg.type === "state") {
        if (msg.state === "listening" && (isPlayingAudio || audioQueue.length > 0)) return;
        setState(msg.state);
      } else if (msg.type === "transcript") {
        appendTranscript(msg.role, msg.text);
      } else if (msg.type === "summary") {
        showSummaryModal(msg.data);
      } else if (msg.type === "turn_complete") {
        serverTurnPending = false;
        if (!isPlayingAudio && audioQueue.length === 0 && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "playback_done" }));
        }
      } else if (msg.type === "call_ended") {
        cleanupAudio();
        stopTimer();
        setState("idle");
        btnStart.disabled = false;
        btnEnd.disabled = true;
      } else if (msg.type === "error") {
        console.warn("Server message:", msg.message);
      }
    } else if (event.data instanceof ArrayBuffer) {
      if (currentState !== "idle") {
        queueAudioPlayback(event.data);
      }
    }
  };

  ws.onclose = () => {
    stopTimer();
    setState("idle");
    cleanupAudio();
    btnStart.disabled = false;
    btnEnd.disabled = true;
  };
}

function startTimer() {
  callStartTimestamp = Date.now();
  callTimerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - callStartTimestamp) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const s = String(elapsed % 60).padStart(2, "0");
    if (callTimer) callTimer.textContent = `${m}:${s}`;
  }, 1000);
}

function stopTimer() {
  if (callTimerInterval) clearInterval(callTimerInterval);
  if (callTimer) callTimer.textContent = "00:00";
}

function cleanupAudio() {
  isSpeakingUtterance = false;
  audioBufferAccumulator = [];
  preRollBuffer = [];
  consecutiveSpeechFrames = 0;
  silenceSamples = 0;

  if (micStream) {
    micStream.getTracks().forEach((t) => {
      try { t.stop(); } catch (e) {}
    });
    micStream = null;
  }
  if (scriptProcessor) {
    try {
      scriptProcessor.onaudioprocess = null;
      scriptProcessor.disconnect();
    } catch (e) {}
    scriptProcessor = null;
  }
  if (micSource) {
    try { micSource.disconnect(); } catch (e) {}
    micSource = null;
  }
  audioQueue = [];
  isPlayingAudio = false;
  if (currentSourceNode) {
    try {
      currentSourceNode.stop();
      currentSourceNode.disconnect();
    } catch (e) {}
    currentSourceNode = null;
  }
}

function showSummaryModal(summaryData) {
  console.log("Received post-call summary:", summaryData);
  const jsonStr = JSON.stringify(summaryData, null, 2);
  if (summaryJsonBlock) {
    summaryJsonBlock.textContent = jsonStr;
    summaryJsonBlock.classList.add("summary-highlight");
    setTimeout(() => summaryJsonBlock.classList.remove("summary-highlight"), 1200);
  }
  const modalJsonBlock = document.getElementById("modalJsonBlock");
  if (modalJsonBlock) {
    modalJsonBlock.textContent = jsonStr;
  }
}

// --- Event Listeners ---
btnStart.addEventListener("click", () => connectWebSocket());
btnEnd.addEventListener("click", () => {
  cleanupAudio();
  stopTimer();
  setState("idle");
  btnStart.disabled = false;
  btnEnd.disabled = true;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end_call" }));
  }
});

if (btnCloseModal) btnCloseModal.addEventListener("click", () => summaryModal && summaryModal.classList.add("hidden"));
if (btnDismiss) btnDismiss.addEventListener("click", () => summaryModal && summaryModal.classList.add("hidden"));
if (btnCopySummary) {
  btnCopySummary.addEventListener("click", () => {
    if (summaryJsonBlock) {
      navigator.clipboard.writeText(summaryJsonBlock.textContent);
      btnCopySummary.textContent = "Copied!";
      setTimeout(() => { btnCopySummary.textContent = "Copy JSON"; }, 2000);
    }
  });
}

// Architecture & Security Philosophy Modal Listeners
const btnOpenDocs = document.getElementById("btnOpenDocs");
const btnCloseDocs = document.getElementById("btnCloseDocs");
const docsModal = document.getElementById("docsModal");

if (btnOpenDocs && docsModal) {
  btnOpenDocs.addEventListener("click", () => docsModal.classList.remove("hidden"));
}
if (btnCloseDocs && docsModal) {
  btnCloseDocs.addEventListener("click", () => docsModal.classList.add("hidden"));
}
if (docsModal) {
  docsModal.addEventListener("click", (e) => {
    if (e.target === docsModal) docsModal.classList.add("hidden");
  });
}

// User Context Switcher
document.querySelectorAll(".caller-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".caller-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentCustomer = btn.getAttribute("data-user");
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "set_user", customer: currentCustomer }));
    }
  });
});

// Interactive Chips, Order Cards & Quick Cards
document.querySelectorAll(".chip-btn, .order-item-box, .chip, .order-card, .quick-card").forEach((el) => {
  el.addEventListener("click", async () => {
    const q = el.getAttribute("data-query") || el.getAttribute("data-text");
    if (!q) return;

    if (!ws || ws.readyState !== WebSocket.OPEN) {
      await connectWebSocket();
      setTimeout(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "user_text", text: q }));
        }
      }, 600);
    } else {
      ws.send(JSON.stringify({ type: "user_text", text: q }));
    }
  });
});

// Direct text input
function sendChatText() {
  if (!chatInput) return;
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    connectWebSocket().then(() => {
      setTimeout(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "user_text", text }));
        }
      }, 600);
    });
  } else {
    ws.send(JSON.stringify({ type: "user_text", text }));
  }
}

if (btnSendChat) btnSendChat.addEventListener("click", sendChatText);
if (chatInput) {
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      sendChatText();
    }
  });
}

if (btnDockBot && chatInput) {
  btnDockBot.addEventListener("click", () => {
    chatInput.focus();
  });
}
