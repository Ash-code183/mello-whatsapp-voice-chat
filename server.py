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
from dotenv import load_dotenv

import httpx
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

# ── Mello system prompt ───────────────────────────────────────────────────
MELLO_SYSTEM_PROMPT = """You are Mello, a warm and empathetic mental health companion designed for Indian users.

mello gender: female

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
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        entry   = body["entry"][0]
        changes = entry["changes"][0]
        value   = changes["value"]

        # Ignore status updates
        if "statuses" in value and "messages" not in value:
            return {"status": "ok"}

        messages = value.get("messages", [])
        if not messages:
            return {"status": "ok"}

        message    = messages[0]
        msg_type   = message.get("type")
        from_phone = message.get("from")
        msg_id     = message.get("id")

        # Non-text messages
        if msg_type != "text":
            await send_whatsapp_message(
                from_phone,
                "Abhi main sirf text messages samajh sakta hoon.\nApni baat likhkar bhejein!"
            )
            return {"status": "ok"}

        user_text = message["text"]["body"].strip()
        log.info(f"[{from_phone}] Received: {user_text}")

        # 1. Mark as read immediately → blue ticks
        await mark_as_read(msg_id)

        # 2. Add to buffer
        if from_phone not in message_buffer:
            message_buffer[from_phone] = []
        message_buffer[from_phone].append(user_text)

        log.info(f"[{from_phone}] Buffer: {len(message_buffer[from_phone])} msg(s) — resetting {DEBOUNCE_SECONDS}s timer")

        # 3. Reset debounce timer + keep typing indicator alive
        schedule_debounce(from_phone)

    except (KeyError, IndexError) as e:
        log.error(f"Webhook parse error: {e}")

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
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
