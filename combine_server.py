"""
Mello WhatsApp Text Chatbot — Azure OpenAI Edition
---------------------------------------------------
Features:
  - Smart debounce buffer (8s after last message)
  - Mello "typing..." indicator shown to user while processing
  - Typing indicator refreshed every 4s so it never disappears
  - Per-user conversation memory (last 20 messages)
  - Blue read ticks on every received message
  - Emoji-safe logging for Windows

Flow:
  User sends message(s)
    → marked as read immediately (blue ticks)
    → Mello starts showing "typing..." to user
    → 8s debounce window — each new message resets the clock
    → after 8s silence → all buffered messages sent to AI
    → typing indicator stops → Mello's reply sent
"""

import os
import sys
import json
import asyncio
import logging
from datetime import datetime
from fractions import Fraction
from dotenv import load_dotenv

import httpx
import interventions
from openai import AsyncAzureOpenAI
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Fix emoji logging on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────
WHATSAPP_TOKEN       = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID    = os.getenv("WHATSAPP_PHONE_ID")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "mello_verify_123")

AZURE_OPENAI_KEY      = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-5.2-chat")
AZURE_API_VERSION     = os.getenv("AZURE_API_VERSION", "2024-12-01-preview")

WHATSAPP_API_URL = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"

# Wait this many seconds after the user's LAST message before replying.
# Longer = more time for slow typers. 8s is the sweet spot.
DEBOUNCE_SECONDS = 8

# WhatsApp typing indicators expire after ~25s.
# We refresh every 4s to keep it alive while user is typing / AI is thinking.
# ── Logging ──────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/mello.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("mello")

# ── App & Azure client ────────────────────────────────────────────────────
app = FastAPI(title="Mello WhatsApp Agent")

azure_client = AsyncAzureOpenAI(
    api_key=AZURE_OPENAI_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT or "",
    api_version=AZURE_API_VERSION,
    timeout=60.0,
    max_retries=0,
)


async def create_azure_chat_completion(messages: list[dict]) -> dict:
    endpoint = (AZURE_OPENAI_ENDPOINT or "").rstrip("/")
    if not endpoint or not AZURE_OPENAI_KEY:
        raise RuntimeError("Azure OpenAI credentials are not configured")

    url = (
        f"{endpoint}/openai/deployments/{AZURE_DEPLOYMENT_NAME}/chat/completions"
        f"?api-version={AZURE_API_VERSION}"
    )
    payload = {
        "messages": messages,
        "max_completion_tokens": 300,
    }
    headers = {
        "api-key": AZURE_OPENAI_KEY,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

# ── State stores ──────────────────────────────────────────────────────────
# Per-user conversation history
conversation_store: dict[str, list] = {}

# Pending messages waiting to be processed
message_buffer: dict[str, list[str]] = {}

# Active debounce timer tasks
debounce_tasks: dict[str, asyncio.Task] = {}

# Active typing indicator loop tasks
# ── Mello system prompt ───────────────────────────────────────────────────
MELLO_SYSTEM_PROMPT = """You are Mello, a warm and empathetic mental health companion designed for Indian users.

Your personality:
- Warm, caring, and non-judgmental — like a trusted friend who listens deeply
- Culturally aware of Indian context: family pressure, academic stress, career expectations, relationship dynamics
- You understand and are comfortable with Hinglish (Hindi-English code switching)
- You never diagnose or prescribe — you support, validate, and gently guide
- You encourage professional help when situations are serious

Your communication style for WhatsApp text:
- Keep replies concise and conversational — this is WhatsApp, not an essay
- Use simple, warm language. Avoid clinical or robotic tone
- Respond in the same language the user writes in (Hindi, English, or Hinglish)
- Use line breaks to make messages readable on mobile
- Occasionally use a single relevant emoji to feel human (don't overdo it)
- Ask one focused follow-up question to understand them better
- Never give a wall of bullet points — keep it natural and human

IMPORTANT — Message batching:
The user may send multiple short messages in a row (like people do on WhatsApp).
These will be delivered to you joined with newlines as a single block.
Treat them as one continuous thought and reply naturally to all of them together.
Do NOT address each message separately — synthesize them into one warm, flowing reply.

If someone mentions self-harm or crisis:
- Take it seriously, show care, and gently provide iCall helpline: 9152987821"""


# ── Typing indicator ──────────────────────────────────────────────────────
# ── AI reply ──────────────────────────────────────────────────────────────
async def get_mello_reply(user_phone: str, combined_message: str) -> str:
    if user_phone not in conversation_store:
        conversation_store[user_phone] = []

    history = conversation_store[user_phone]
    history.append({"role": "user", "content": combined_message})

    if len(history) > 20:
        history = history[-20:]
        conversation_store[user_phone] = history

    messages = [{"role": "system", "content": MELLO_SYSTEM_PROMPT}] + history

    try:
        response = await create_azure_chat_completion(messages)
        content = response["choices"][0]["message"].get("content", "")
        if isinstance(content, list):
            reply = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
        else:
            reply = str(content).strip()
        if not reply:
            reply = "Main yahan hoon. Thoda aur bataoge?"
        history.append({"role": "assistant", "content": reply})

        log.info(f"[{user_phone}] User: {combined_message[:80].replace(chr(10), ' | ')}")
        log.info(f"[{user_phone}] Mello: {reply[:80]}")
        return reply

    except Exception as e:
        log.error(f"Azure OpenAI error: {e}")
        return "Ek second ruko... thoda technical issue aa gaya.\nPlease dobara try karo."


# ── WhatsApp API helpers ───────────────────────────────────────────────────
async def send_whatsapp_message(to: str, text: str):
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        log.warning("WhatsApp credentials not set — skipping send")
        return

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(WHATSAPP_API_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            log.error(f"WhatsApp send failed {resp.status_code}: {resp.text}")
        else:
            log.info(f"Message sent to {to} ✓")


async def mark_as_read(message_id: str):
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        return
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {
            "type": "text",
        },
    }
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(WHATSAPP_API_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            log.warning(f"Mark-as-read failed {resp.status_code}: {resp.text[:200]}")


# ── Debounce logic ─────────────────────────────────────────────────────────
async def process_buffer(phone: str):
    """
    Waits DEBOUNCE_SECONDS after the last message.
    Then processes all buffered messages and replies once.
    """
    await asyncio.sleep(DEBOUNCE_SECONDS)

    # Clear debounce task ref
    debounce_tasks.pop(phone, None)

    # Grab and clear buffer
    messages = message_buffer.pop(phone, [])
    if not messages:
        return

    if len(messages) == 1:
        combined = messages[0]
        log.info(f"[{phone}] Processing 1 message")
    else:
        combined = "\n".join(messages)
        log.info(f"[{phone}] Processing {len(messages)} batched messages")

    reply = await get_mello_reply(phone, combined)

    # Send Mello's reply
    await send_whatsapp_message(phone, reply)


def schedule_debounce(phone: str):
    """
    Cancel existing timer and start fresh.
    """
    # Cancel existing debounce timer
    existing = debounce_tasks.get(phone)
    if existing and not existing.done():
        existing.cancel()

    # Start fresh debounce timer
    task = asyncio.create_task(process_buffer(phone))
    debounce_tasks[phone] = task


# ── Webhook verification (GET) ─────────────────────────────────────────────
@app.get("/webhook")
async def verify_webhook(request: Request):
    params    = dict(request.query_params)
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        log.info("Webhook verified by Meta ✓")
        return PlainTextResponse(content=challenge)

    raise HTTPException(status_code=403, detail="Verification failed")


# ── Webhook receiver (POST) ────────────────────────────────────────────────
@app.post("/webhook")
async def receive_webhook(request: Request):
    log.info(f"Webhook hit: {request.method} {request.url.path}")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # DEBUG: Log raw webhook body to understand structure
    log.info(f"[WEBHOOK RAW] {json.dumps(body)[:500]}")

    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        field = changes.get("field", "unknown")
        value = changes["value"]

        log.info(f"[WEBHOOK] field={field} keys={list(value.keys())}")

        if value.get("calls"):
            log.info(f"[WEBHOOK] Call event detected! calls={json.dumps(value['calls'])[:300]}")
        await process_whatsapp_change(value, source="/webhook")

    except (KeyError, IndexError) as e:
        log.error(f"Webhook parse error: {e} | body={json.dumps(body)[:200]}")

    return {"status": "ok"}


# ── Direct test endpoint ───────────────────────────────────────────────────
@app.post("/test-message")
async def test_message(request: Request):
    body    = await request.json()
    message = body.get("message", "").strip()
    phone   = body.get("phone", "dashboard_test")
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    reply = await get_mello_reply(phone, message)
    return {"reply": reply}


# ── Conversation viewer ────────────────────────────────────────────────────
@app.get("/conversations")
async def get_conversations():
    return {
        phone: {
            "message_count": len(msgs),
            "last_message": msgs[-1]["content"][:120] if msgs else None,
            "history": msgs,
            "pending_buffer": message_buffer.get(phone, []),
        }
        for phone, msgs in conversation_store.items()
    }


@app.delete("/conversations/{phone}")
async def clear_conversation(phone: str):
    conversation_store.pop(phone, None)
    message_buffer.pop(phone, None)
    task = debounce_tasks.pop(phone, None)
    if task: task.cancel()
    return {"status": "cleared", "phone": phone}


# ── Health check ───────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "whatsapp_configured": bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID),
        "azure_configured": bool(AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT),
        "deployment": AZURE_DEPLOYMENT_NAME,
        "debounce_seconds": DEBOUNCE_SECONDS,
        "active_conversations": len(conversation_store),
        "users_with_pending_messages": list(debounce_tasks.keys()),
        "timestamp": datetime.utcnow().isoformat(),
    }


# ── Voice Call Handler ────────────────────────────────────────────────────
# Calls come in via /whatsapp (separate from text webhook at /webhook)
# Same port 8000 — one ngrok tunnel handles both

import hmac
import hashlib
import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack, RTCConfiguration, RTCIceServer

WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
HUME_API_KEY        = os.getenv("HUME_API_KEY")
HUME_CONFIG_ID      = os.getenv("HUME_CONFIG_ID", "")

# Active calls: call_id → { pc, hume_ws, hume_session, phone }
active_calls: dict[str, dict] = {}


def log_call_event(call_id: str, event: str, details: str = ""):
    message = f"[CALL] [{call_id}] {event}"
    if details:
        message = f"{message} | {details}"
    log.info(message)


def log_call_error(call_id: str, details: str):
    log.error(f"[CALL] [{call_id}] CALL_ERROR | {details}")


class HumeAudioTrack(MediaStreamTrack):
    """
    Virtual audio track that feeds Hume EVI output back to WhatsApp caller.

    Key insight: Hume sends audio FASTER than real-time (bursts of variable-sized WAV chunks).
    WebRTC expects CONSISTENT 20ms chunks (960 samples at 48kHz).

    Solution: Buffer all incoming audio, output exactly 960 samples per recv() call.
    """
    kind = "audio"

    # Constants for 48kHz, 20ms chunks
    SAMPLE_RATE = 48000
    CHUNK_SAMPLES = 960  # 20ms at 48kHz
    CHUNK_BYTES = CHUNK_SAMPLES * 2  # 16-bit = 2 bytes per sample

    def __init__(self):
        super().__init__()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._buffer = bytearray()  # Accumulates decoded PCM audio
        self._timestamp = 0
        self._start: float | None = None  # Wall-clock time when playback started
        self._first_chunk = True

    def _decode_wav(self, wav_data: bytes) -> bytes:
        """Decode WAV to raw PCM bytes."""
        import io
        import wave

        try:
            with io.BytesIO(wav_data) as wav_io:
                with wave.open(wav_io, 'rb') as wav:
                    if self._first_chunk:
                        log.info(f"[HUME_OUT] WAV: rate={wav.getframerate()} ch={wav.getnchannels()} width={wav.getsampwidth()}")
                        self._first_chunk = False
                    return wav.readframes(wav.getnframes())
        except Exception as e:
            log.warning(f"[HUME_OUT] WAV decode failed: {e}")
            return b''

    async def recv(self):
        import av
        import time

        # REAL-TIME PACING: Sleep FIRST to maintain 20ms intervals
        # Only pace AFTER first frame (when _start is set)
        if self._start is not None:
            target_time = self._start + (self._timestamp / self.SAMPLE_RATE)
            wait = target_time - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

        # Drain queue into buffer (non-blocking check for available data)
        while True:
            try:
                # Quick check for data - don't wait long as we've already paced above
                timeout = 0.005 if len(self._buffer) < self.CHUNK_BYTES else 0.0
                audio_data = await asyncio.wait_for(self._queue.get(), timeout=timeout)

                # Decode WAV and add to buffer
                if audio_data[:4] == b'RIFF':
                    pcm = self._decode_wav(audio_data)
                else:
                    pcm = audio_data
                self._buffer.extend(pcm)

            except asyncio.TimeoutError:
                break  # No more data available right now

        # Extract exactly CHUNK_BYTES (960 samples) from buffer
        if len(self._buffer) >= self.CHUNK_BYTES:
            audio_bytes = bytes(self._buffer[:self.CHUNK_BYTES])
            del self._buffer[:self.CHUNK_BYTES]
        else:
            # Not enough data - pad with silence
            audio_bytes = bytes(self._buffer) + bytes(self.CHUNK_BYTES - len(self._buffer))
            self._buffer.clear()

        # Create consistent 20ms frame for WebRTC
        frame = av.AudioFrame(format="s16", layout="mono", samples=self.CHUNK_SAMPLES)
        frame.planes[0].update(audio_bytes)
        frame.sample_rate = self.SAMPLE_RATE
        frame.pts = self._timestamp
        frame.time_base = Fraction(1, self.SAMPLE_RATE)

        # Increment timestamp for next frame's scheduling
        self._timestamp += self.CHUNK_SAMPLES

        # Set _start AFTER first frame is ready (not before buffer drain)
        # This ensures pacing is relative to when we actually started outputting
        if self._start is None:
            self._start = time.time()

        return frame


async def wa_call_action(call_id: str, payload: dict):
    """POST a call action to WhatsApp Cloud API."""
    url = f"https://graph.facebook.com/v23.0/{WHATSAPP_PHONE_ID}/calls"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    log.info(f"[{call_id}] API REQUEST: {payload.get('action')} → {url}")
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=headers, timeout=10.0)
        log.info(f"[{call_id}] API RESPONSE: {payload.get('action')} → {resp.status_code}")
        log.info(f"[{call_id}] API BODY: {resp.text[:500]}")
        try:
            return resp.json()
        except Exception:
            return {"error": {"message": resp.text, "code": resp.status_code}}


async def wa_call_action_logged(call_id: str, payload: dict):
    resp = await wa_call_action(call_id, payload)
    action = str(payload.get("action", "unknown")).upper()
    if isinstance(resp, dict) and resp.get("error"):
        err = resp.get("error", {})
        log_call_error(call_id, f"{action} FAILED | code={err.get('code')} msg={err.get('message', '')[:200]}")
    else:
        log_call_event(call_id, f"CALL_{action}", "success")
    return resp


def filter_sdp_fingerprints(sdp: str) -> str:
    """WhatsApp only accepts SHA-256 fingerprints — filter others out."""
    lines = sdp.split("\r\n")
    return "\r\n".join(
        l for l in lines
        if not (l.startswith("a=fingerprint:") and "sha-256" not in l.lower())
    )


import re

def sanitize_sdp_for_whatsapp(sdp: str) -> str:
    """Sanitize SDP answer to be WhatsApp-compatible.

    WhatsApp's SDP validator requires:
    1. IPv4 in connection lines (c=) — rejects IPv6
    2. IPv4 ICE candidates — rejects IPv6 candidates
    3. Port in m= line must match an IPv4 candidate's port
    """
    lines = sdp.split("\r\n")
    sanitized = []

    # First pass: find our best IPv4 candidate (IP and port)
    ipv4_candidates = []
    for line in lines:
        if line.startswith("a=candidate:") and " typ " in line:
            parts = line.split()
            # candidate format: a=candidate:foundation component protocol priority ip port typ type
            if len(parts) >= 8:
                ip = parts[4]
                port = parts[5]
                ctype = parts[7]  # host, srflx, relay
                # Check if IPv4 (no colons in IP)
                if ":" not in ip:
                    priority = 0
                    if ctype == "srflx":
                        priority = 3  # Prefer STUN-resolved public IP
                    elif ctype == "relay":
                        priority = 2
                    elif ctype == "host":
                        priority = 1
                    ipv4_candidates.append((priority, ip, port, ctype))

    # Sort by priority (highest first) and pick best candidate
    ipv4_candidates.sort(reverse=True, key=lambda x: x[0])

    if ipv4_candidates:
        _, best_ip, best_port, best_type = ipv4_candidates[0]
        log.info(f"[SDP] Best IPv4 candidate: {best_ip}:{best_port} (type={best_type})")
    else:
        best_ip = "0.0.0.0"
        best_port = "9"  # Discard port as fallback
        log.warning(f"[SDP] No IPv4 candidates found! Using fallback {best_ip}:{best_port}")

    for line in lines:
        # Replace IPv6 connection lines with our best IPv4
        if line.startswith("c=IN IP6"):
            sanitized.append(f"c=IN IP4 {best_ip}")
            log.info(f"[SDP] Replaced IPv6 connection line → c=IN IP4 {best_ip}")
            continue

        # Update m= line port to match our IPv4 candidate port
        if line.startswith("m=audio "):
            # m=audio PORT protocol codecs
            match = re.match(r"(m=audio )(\d+)( .+)", line)
            if match:
                old_port = match.group(2)
                new_line = f"{match.group(1)}{best_port}{match.group(3)}"
                if old_port != best_port:
                    log.info(f"[SDP] Updated m=audio port: {old_port} → {best_port}")
                sanitized.append(new_line)
                continue

        # Remove IPv6 ICE candidates (contain : in the IP field)
        if line.startswith("a=candidate:"):
            parts = line.split()
            if len(parts) >= 6:
                ip = parts[4]
                port = parts[5]
                if ":" in ip:  # IPv6 address
                    log.info(f"[SDP] Removed IPv6 candidate: {ip}")
                    continue
                else:
                    log.info(f"[SDP] Keeping IPv4 candidate: {ip}:{port}")

        # Remove non-SHA256 fingerprints (WhatsApp only accepts sha-256)
        if line.startswith("a=fingerprint:") and "sha-256" not in line.lower():
            log.info(f"[SDP] Removed non-SHA256 fingerprint: {line[:50]}")
            continue

        # Remove ANY line containing IPv6 references (extra safety)
        if "IP6" in line:
            log.info(f"[SDP] Removed line with IP6: {line[:50]}")
            continue

        sanitized.append(line)

    result = "\r\n".join(sanitized)
    return result


async def bridge_audio(call_id: str, user_track, hume_ws, hume_audio_out: HumeAudioTrack):
    """Bridge audio between WhatsApp WebRTC track (48kHz stereo) ↔ Hume EVI WebSocket (48kHz mono)."""
    import base64
    import numpy as np

    frame_count = 0
    intervention_state = interventions.get_initial_state()

    async def send_user_audio():
        nonlocal frame_count
        try:
            while True:
                frame = await user_track.recv()
                frame_count += 1

                # Log first frame format for debugging
                if frame_count == 1:
                    log.info(f"[{call_id}] AUDIO_IN format={frame.format.name} layout={frame.layout.name} rate={frame.sample_rate} samples={frame.samples}")

                # Use to_ndarray() for reliable audio extraction
                audio_array = frame.to_ndarray()  # Shape: (channels, samples) for planar, (1, samples*channels) for packed

                if frame_count == 1:
                    log.info(f"[{call_id}] AUDIO_ARRAY shape={audio_array.shape} dtype={audio_array.dtype}")

                # Convert to mono by averaging channels if stereo
                if audio_array.shape[0] == 2:  # Stereo (planar: 2 channels)
                    mono_array = ((audio_array[0].astype(np.int32) + audio_array[1].astype(np.int32)) // 2).astype(np.int16)
                elif len(audio_array.shape) == 2 and audio_array.shape[0] == 1:
                    # Packed stereo: reshape and average
                    samples = audio_array[0]
                    if frame.layout.name == "stereo":
                        # Interleaved: [L0, R0, L1, R1, ...]
                        left = samples[0::2]
                        right = samples[1::2]
                        mono_array = ((left.astype(np.int32) + right.astype(np.int32)) // 2).astype(np.int16)
                    else:
                        mono_array = samples.astype(np.int16)
                else:
                    mono_array = audio_array.flatten().astype(np.int16)

                audio_bytes = mono_array.tobytes()

                if frame_count == 1:
                    log.info(f"[{call_id}] AUDIO_OUT_TO_HUME len={len(audio_bytes)} samples={len(audio_bytes)//2}")

                await hume_ws.send_str(json.dumps({
                    "type": "audio_input",
                    "data": base64.b64encode(audio_bytes).decode(),
                }))
        except Exception as e:
            log.debug(f"[{call_id}] send_user_audio ended: {e}")

    async def receive_hume_audio():
        try:
            async for msg in hume_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    t = data.get("type", "")
                    if t == "audio_output":
                        audio_bytes = base64.b64decode(data.get("data", ""))
                        await hume_audio_out._queue.put(audio_bytes)
                    elif t == "user_message":
                        transcript = data.get('message', {}).get('content', '')
                        log.info(f"[{call_id}] User: {transcript}")

                        # Check for intervention
                        intervention = interventions.detect_intervention(data, intervention_state)
                        if intervention:
                            log.info(f"[{call_id}] INTERVENTION: {intervention['type']} (priority={intervention['priority']})")
                            # Inject guidance via variables (matches Hume config's {{intervention_guidance}})
                            await hume_ws.send_str(json.dumps({
                                "type": "session_settings",
                                "variables": {
                                    "intervention_guidance": intervention['guidance']
                                }
                            }))
                    elif t == "assistant_message":
                        log.info(f"[{call_id}] Mello: {data.get('message',{}).get('content','')}")
                    elif t == "error":
                        log_call_error(call_id, f"Hume error: {data}")
        except Exception as e:
            log_call_error(call_id, f"receive_hume_audio ended: {e}")

    await asyncio.gather(send_user_audio(), receive_hume_audio(), return_exceptions=True)
    log_call_event(call_id, "CALL_STOP", "audio bridge ended")


async def handle_incoming_call(call_id: str, from_phone: str, sdp_offer: str):
    log_call_event(call_id, "USER_TRY_CALL", f"from={from_phone}")

    # Log SDP offer for debugging
    log.info(f"[{call_id}] SDP OFFER (first 300 chars): {sdp_offer[:300]}")

    # 1. Create WebRTC peer connection with STUN servers (CRITICAL for NAT traversal)
    ice_config = RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun2.l.google.com:19302"]),
        ]
    )
    pc = RTCPeerConnection(configuration=ice_config)
    hume_audio_out = HumeAudioTrack()
    pc.addTrack(hume_audio_out)

    active_calls[call_id] = {"pc": pc, "phone": from_phone}

    user_track_holder = {"track": None}

    @pc.on("track")
    def on_track(track):
        log_call_event(call_id, "CALL_TRACK", f"kind={track.kind}")
        if track.kind == "audio":
            user_track_holder["track"] = track

    @pc.on("connectionstatechange")
    async def on_state():
        log_call_event(call_id, "CALL_STATE", pc.connectionState)
        if pc.connectionState == "failed":
            log_call_error(call_id, "WebRTC connection failed")
            await cleanup_call(call_id, reason="error")
        elif pc.connectionState == "closed":
            await cleanup_call(call_id, reason="stop")
        elif pc.connectionState == "disconnected":
            await cleanup_call(call_id, reason="cut")

    @pc.on("iceconnectionstatechange")
    async def on_ice_state():
        log_call_event(call_id, "ICE_STATE", pc.iceConnectionState)

    @pc.on("icegatheringstatechange")
    async def on_ice_gathering():
        log_call_event(call_id, "ICE_GATHERING", pc.iceGatheringState)

    # 2. Set remote SDP (filter non-SHA256 fingerprints)
    filtered = filter_sdp_fingerprints(sdp_offer)
    await pc.setRemoteDescription(RTCSessionDescription(sdp=filtered, type="offer"))

    # 3. Create SDP answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Log raw SDP answer for debugging
    raw_sdp = pc.localDescription.sdp
    log.info(f"[{call_id}] SDP ANSWER RAW (first 300 chars): {raw_sdp[:300]}")

    # 4. Sanitize SDP: force IPv4, remove IPv6 candidates (WhatsApp rejects IPv6)
    clean_sdp = sanitize_sdp_for_whatsapp(raw_sdp)
    log.info(f"[{call_id}] SDP ANSWER CLEAN (first 300 chars): {clean_sdp[:300]}")

    # DEBUG: Log critical SDP fields
    for line in clean_sdp.split("\r\n"):
        if line.startswith(("a=ice-ufrag:", "a=ice-pwd:", "a=fingerprint:", "a=candidate:", "a=setup:")):
            log.info(f"[{call_id}] SDP FIELD: {line[:100]}")

    # Count candidates in clean SDP
    candidate_count = len([l for l in clean_sdp.split("\r\n") if l.startswith("a=candidate:")])
    log.info(f"[{call_id}] SDP has {candidate_count} candidate(s)")

    session_payload = {
        "sdp": clean_sdp,
        "sdp_type": "answer",
    }

    # 4. Pre-accept with SDP answer to establish WebRTC early.
    await wa_call_action_logged(call_id, {
        "messaging_product": "whatsapp",
        "call_id": call_id,
        "action": "pre_accept",
        "session": session_payload,
    })

    # 5. Accept call with same SDP answer
    await wa_call_action_logged(call_id, {
        "messaging_product": "whatsapp",
        "call_id": call_id,
        "action": "accept",
        "session": session_payload,
    })

    log_call_event(call_id, "CALL_INITIATED", f"from={from_phone}")

    # 6. Wait for audio track then connect Hume EVI
    for _ in range(20):
        if user_track_holder["track"]:
            break
        await asyncio.sleep(0.1)

    config_param = f"&config_id={HUME_CONFIG_ID}" if HUME_CONFIG_ID else ""
    hume_url = f"wss://api.hume.ai/v0/evi/chat?api_key={HUME_API_KEY}{config_param}"

    session = aiohttp.ClientSession()
    hume_ws = await session.ws_connect(hume_url)
    log.info(f"[{call_id}] Connected to Hume EVI ✓")

    # CRITICAL: Send session_settings to tell Hume our audio format
    # WhatsApp sends 48kHz stereo, we convert to mono and send at 48kHz
    session_settings = {
        "type": "session_settings",
        "audio": {
            "channels": 1,
            "encoding": "linear16",
            "sample_rate": 48000
        },
        "variables": {
            "intervention_guidance": ""  # Empty initially, set when intervention detected
        }
    }
    await hume_ws.send_str(json.dumps(session_settings))
    log.info(f"[{call_id}] Sent session_settings to Hume: 48kHz mono linear16 + intervention_guidance var")

    log_call_event(call_id, "CALL_HUME_CONNECTED")
    active_calls[call_id].update({"hume_ws": hume_ws, "hume_session": session})

    if user_track_holder["track"]:
        asyncio.create_task(bridge_audio(call_id, user_track_holder["track"], hume_ws, hume_audio_out))
    else:
        log_call_event(call_id, "CALL_STOP", "no user audio track received")

    log.info(f"[{call_id}] Call live ✓ — Mello is listening")
    log_call_event(call_id, "CALL_LIVE", "Mello is listening")


async def safe_handle_incoming_call(call_id: str, from_phone: str, sdp_offer: str):
    try:
        await handle_incoming_call(call_id, from_phone, sdp_offer)
    except Exception as e:
        log_call_error(call_id, f"setup failed: {e}")
        await cleanup_call(call_id, reason="error")


async def cleanup_call(call_id: str, reason: str = "stop"):
    call = active_calls.pop(call_id, None)
    if not call:
        return
    if reason == "cut":
        log_call_event(call_id, "CALL_CUT")
    else:
        log_call_event(call_id, "CALL_STOP", reason)
    try:
        if "hume_ws" in call: await call["hume_ws"].close()
        if "hume_session" in call: await call["hume_session"].close()
        if "pc" in call: await call["pc"].close()
    except Exception as e:
        log_call_error(call_id, f"cleanup failed: {e}")


async def process_whatsapp_change(value: dict, source: str):
    # Ignore pure status updates.
    if "statuses" in value and "messages" not in value and "calls" not in value:
        return

    for call in value.get("calls", []):
        call_id = call.get("id")
        from_phone = call.get("from", "unknown")
        event = (call.get("event") or "").lower()

        log.info(f"[CALL] Processing call event: id={call_id} from={from_phone} event={event}")

        if event == "connect":
            session = call.get("session", {})
            sdp_offer = session.get("sdp", "")
            sdp_type = session.get("sdp_type", "unknown")
            log_call_event(call_id, "USER_TRY_CALL", f"from={from_phone} source={source} sdp_type={sdp_type}")

            if not sdp_offer:
                log_call_error(call_id, "No SDP offer in connect event!")
                continue
            asyncio.create_task(safe_handle_incoming_call(call_id, from_phone, sdp_offer))
        elif event in ("disconnect", "terminate"):
            log_call_event(call_id, f"CALL_{event.upper()}", f"source={source}")
            await cleanup_call(call_id, reason="cut" if event == "disconnect" else "stop")

    messages = value.get("messages", [])
    if not messages:
        return

    message = messages[0]
    msg_type = message.get("type")
    from_phone = message.get("from")
    msg_id = message.get("id", "")

    # Meta may route call signaling-like events through messages on some numbers.
    if msg_type == "call" or msg_id.startswith("wacid."):
        log.info(f"[{from_phone}] Call signaling event via {source} (type={msg_type})")
        return

    if msg_type != "text":
        await send_whatsapp_message(
            from_phone,
            "Abhi main sirf text messages samajh sakta hoon.\nApni baat likhkar bhejein!"
        )
        return

    user_text = message["text"]["body"].strip()
    log.info(f"[{from_phone}] Received via {source}: {user_text}")
    await mark_as_read(msg_id)

    if from_phone not in message_buffer:
        message_buffer[from_phone] = []
    message_buffer[from_phone].append(user_text)

    log.info(
        f"[{from_phone}] Buffer: {len(message_buffer[from_phone])} msg(s) "
        f"— resetting {DEBOUNCE_SECONDS}s timer"
    )
    schedule_debounce(from_phone)


@app.get("/whatsapp")
async def verify_call_webhook(request: Request):
    p = dict(request.query_params)
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == WEBHOOK_VERIFY_TOKEN:
        log.info("Call webhook verified ✓")
        return PlainTextResponse(content=p.get("hub.challenge"))
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/whatsapp")
async def receive_call_webhook(request: Request):
    log.info(f"Call webhook hit: {request.method} {request.url.path}")
    body_bytes = await request.body()

    # Verify signature if App Secret configured
    if WHATSAPP_APP_SECRET:
        sig = request.headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(
            WHATSAPP_APP_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if sig and not hmac.compare_digest(expected, sig):
            log.warning("Call webhook signature mismatch")
            raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    log.info(f"Call webhook: {json.dumps(body)[:400]}")

    try:
        value = body["entry"][0]["changes"][0]["value"]
        await process_whatsapp_change(value, source="/whatsapp")

    except (KeyError, IndexError) as e:
        log.error(f"Call webhook parse error: {e}")

    return {"status": "ok"}


# ── Dashboard ──────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("static/dashboard.html") as f:
        return f.read()

app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    import uvicorn
    log.info("Starting Mello WhatsApp Agent (Azure OpenAI)...")
    log.info(f"Deployment: {AZURE_DEPLOYMENT_NAME}")
    log.info(f"Debounce window: {DEBOUNCE_SECONDS}s")
    log.info(f"Webhook verify token: {WEBHOOK_VERIFY_TOKEN}")
    uvicorn.run("combine_server:app", host="0.0.0.0", port=8000, reload=True)
