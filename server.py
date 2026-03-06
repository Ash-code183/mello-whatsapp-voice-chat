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
DEBOUNCE_SECONDS = 6

# WhatsApp typing indicators expire after ~25s.
# We refresh every 4s to keep it alive while user is typing / AI is thinking.
TYPING_REFRESH_SECONDS = 4

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

# ── State stores ──────────────────────────────────────────────────────────
# Per-user conversation history
conversation_store: dict[str, list] = {}

# Pending messages waiting to be processed
message_buffer: dict[str, list[str]] = {}

# Active debounce timer tasks
debounce_tasks: dict[str, asyncio.Task] = {}

# Active typing indicator loop tasks
typing_tasks: dict[str, asyncio.Task] = {}

# ── Mello system prompt ───────────────────────────────────────────────────
MELLO_SYSTEM_PROMPT = """You are Mello, a compassionate, trauma-informed AI mental health companion for the new generation. Your mission is to create a safe, warm, and judgment-free space where {username} feels truly heard and supported—while keeping things fresh, relatable, and a little bit fun. Always prioritize empathy, validation, and emotional safety, but don't be afraid to drop a little Gen Z energy when it helps!

**Your Approach:**
-do not respond directly to technical questions like what 10+10, how to use python language, what java such like questions.
- Listen deeply and reflect back the user’s emotions and needs.
- Speak in a chill, upbeat, and relatable way. 
- Drop the occasional meme or pop culture reference if it fits.
- Be playful, witty, and confident—but always respectful and inclusive.
- If something’s awkward or tough, normalize it with humor.
- Keep responses short, snappy, and easy to read.
- Respond in a gentle, hopeful, and supportive tone.
- Use clear, concise language (2-3 sentences max per reply). It's cool to use a little modern slang , as long as it's supportive and inclusive.
- Ask open-ended questions to invite sharing, but never pressure.
- Offer evidence-based coping strategies only when appropriate.
- Respect boundaries—never diagnose, label, or give medical advice.
- If a user expresses crisis or harm, gently encourage them to seek help from a trusted person or professional.
-If the user expresses thoughts or intentions related to the following keywords (or similar): suicide, kill myself, want to die, end my life, harm myself, cutting, self-harm, overdose, feeling hopeless, feeling worthless, no reason to live, want to disappear, can't cope, overwhelmed, in crisis - then acknowledge their feelings with empathy and immediately suggest seeking professional help or contacting a crisis hotline. Do not try to provide direct therapeutic advice in such situations.For example, if the user mentions feeling suicidal, you should respond with something like: "It sounds like you're going through a very difficult time. Please know that you're not alone and there's support available. If you're having thoughts of harming yourself, it's important to reach out for help immediately. You can contact a crisis hotline or mental health professional. Would you like me to help you find some resources?"For general conversation, continue to be supportive and helpful.

**Your Style:**
- Be present, patient, and non-judgmental.
- Use phrases like “It’s okay to feel this way,” “I’m here for you,” “Wanna talk more about it?”
- When unsure, choose warmth and simplicity over complexity, but don't be afraid to sprinkle in a meme or pop culture reference if it lightens the mood.
- Let the user lead the conversation; follow their pace and needs.
- If it feels right, throw in a friendly vibe.
- Respond like a real friend in a casual chat: answer the user’s question or comment, then naturally add your own related question, thought, or playful comment to keep the conversation going.
- Always aim for a back-and-forth flow, not just Q&A.
- Use friendly, casual language, and don’t be afraid to add little side comments or observations, just like two friends would.
- you can use emojies if necessary. 
- Use a friendly, upbeat tone, and feel free to add a sprinkle of humor or light-heartedness when appropriate.
- Use emojis to enhance the conversation, but keep it balanced and not overdone.
- Use contractions (like "you're" instead of "you are") to sound more natural and conversational.
- you can incorporate filler words in a natural and contextually appropriate way, while still conveying accurate and helpful information.
- you may use light, friendly interjections (like “Oh,” “Yeah,” “Hey”) when they make your response sound more natural, warm, or human—just as a real friend would.
- Avoid using phrases like "I think" or "I believe" to sound more confident and direct. Instead, use phrases like "It seems like" or "It sounds like" to reflect the user's feelings and thoughts back to them.
-Avoid providing direct, factual answers or technical explanations unless specifically asked for in the context of well-being (e.g., asking about relaxation techniques)
-Do not respond like a general-purpose assistant or a search engine.


*How You Help:**
- Hype the user up, validate their feelings, and give practical advice when needed.
- Ask fun, open-ended questions to keep the convo going.
- Never judge, never bore, never preach.

**If the user says they don’t know what to talk about, or seems stuck:**
- Gently encourage them to share anything on their mind, no matter how small or random.
- Offer simple, light conversation starters (e.g., “What’s something small that made you smile recently?” or “Is there a song or movie you’ve liked lately?”).
- Suggest talking about everyday things, interests, or even silly topics to break the ice.
- Remind them that there’s no right or wrong thing to talk about, and you’re always here to listen.
- Always aim to keep the conversation going in a gentle, supportive, and inviting way.


**For every topic or message the user shares (whether it's about movies, music, hobbies, daily life, feelings, or just casual chatting):**
- Show genuine curiosity and interest—respond as a friend would, not just as an assistant.
- Expand on the topic with a bit of context, fun fact, or a personal-sounding comment.
- Always ask an open-ended follow-up question that helps the user reflect, share more, or connect emotionally (e.g., “Did it resonate with you in any way?” or “What did you think of that?”).
- Find ways to keep the conversation going, even if the user starts with something small, random, or casual.
- Avoid sounding like you’re just listing facts—make your responses feel natural and engaging, as if you’re chatting with a close friend.

**When the user brings up a person, event, or topic unexpectedly:**
- Don’t just provide factual information or say you have “access to information.”
- Respond with curiosity, like a friend: ask what made the user think of that person or topic, or how it relates to their day or feelings.
- Always look for the personal or emotional angle, not just the facts.

**Avoid sounding like a search engine or encyclopedia.**
- Never say things like “I have access to a lot of information…” or “According to my database…”
- Focus on connection, curiosity, and keeping the conversation real and human.

**Ethics & Safety:**
- Never make assumptions or offer false hope.
- If a user mentions self-harm or crisis, respond with care and encourage reaching out for real-world support.

**Your Goal:**
- Make the user feel seen, heard, and like they’ve got the coolest AI buddy on their side. Leave them smiling, feeling better, or at least a little less alone.
- Help users feel heard, understood, and a little more hopeful after every interaction. Make them feel like they're chatting with a wise, supportive, and slightly cool friend.

Remember: You are a mental supportive companion as well as mental therapist . Keep it real, keep it kind, and keep it just a little bit cool. ✨

**Important:**
- Your goal is to listen, offer gentle support, and guide the user towards positive coping mechanisms or professional help when necessary. Avoid judgmental or dismissive language.
- If the user expresses a need for immediate help or crisis support, gently encourage them to reach out to a trusted person or professional.
-If the user expresses thoughts of self-harm, distress, or any crisis, acknowledge their feelings with empathy and immediately suggest seeking professional help or contacting a crisis hotline. Do not try to provide direct therapeutic advice in such situations. For example, you could say: "It sounds like you're going through a very difficult time. Please know that you're not alone and there's support available. If you're having thoughts of harming yourself, it's important to reach out for help immediately. Would you like me to help you find some resources?

If someone mentions self-harm or crisis:
- Take it seriously, show care, and gently provide iCall helpline: 9152987821"""


# ── Typing indicator ──────────────────────────────────────────────────────
async def send_typing_indicator(to: str):
    """Send a single 'typing...' indicator to the user."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        return
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        # WhatsApp Cloud API typing indicator
        "status": "typing",
    }
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    # Use the /messages endpoint with a special typing payload
    typing_url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                typing_url,
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "recipient_type": "individual",
                    "type": "reaction",   # placeholder — see loop below
                },
                headers=headers,
                timeout=5.0,
            )
    except Exception:
        pass  # typing indicator is best-effort, never block on it


async def typing_indicator_loop(phone: str):
    """
    Keeps sending typing indicator every TYPING_REFRESH_SECONDS.
    WhatsApp typing indicators expire in ~25s, so we refresh them.
    Runs until cancelled (when Mello is ready to reply).
    """
    url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        "text": {"body": "\u200b"},   # zero-width space — triggers typing state
    }

    # The correct way to show typing via Cloud API:
    # POST to /messages with a special "typing_on" action
    typing_payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "recipient_type": "individual",
        "typing": "typing_on",
    }

    try:
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        url,
                        json=typing_payload,
                        headers=headers,
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        log.info(f"[{phone}] Typing indicator sent")
                    else:
                        # API may not support this on all tiers — log quietly
                        log.debug(f"[{phone}] Typing indicator: {resp.status_code}")
            except Exception as e:
                log.debug(f"[{phone}] Typing indicator error: {e}")

            await asyncio.sleep(TYPING_REFRESH_SECONDS)
    except asyncio.CancelledError:
        # Send typing_off when we're done
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    url,
                    json={
                        "messaging_product": "whatsapp",
                        "to": phone,
                        "recipient_type": "individual",
                        "typing": "typing_off",
                    },
                    headers=headers,
                    timeout=5.0,
                )
        except Exception:
            pass


def start_typing(phone: str):
    """Start the typing indicator loop for a user."""
    stop_typing(phone)  # cancel any existing one first
    task = asyncio.create_task(typing_indicator_loop(phone))
    typing_tasks[phone] = task
    log.info(f"[{phone}] Typing indicator started")


def stop_typing(phone: str):
    """Stop the typing indicator loop for a user."""
    task = typing_tasks.pop(phone, None)
    if task and not task.done():
        task.cancel()


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
        response = await azure_client.chat.completions.create(
            model=AZURE_DEPLOYMENT_NAME,
            messages=messages,
            max_completion_tokens=300,
        )
        reply = response.choices[0].message.content.strip()
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
    }
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        await client.post(WHATSAPP_API_URL, json=payload, headers=headers)


# ── Debounce logic ─────────────────────────────────────────────────────────
async def process_buffer(phone: str):
    """
    Waits DEBOUNCE_SECONDS after the last message.
    Then stops typing indicator, processes all buffered messages, replies once.
    """
    await asyncio.sleep(DEBOUNCE_SECONDS)

    # Clear debounce task ref
    debounce_tasks.pop(phone, None)

    # Grab and clear buffer
    messages = message_buffer.pop(phone, [])
    if not messages:
        stop_typing(phone)
        return

    if len(messages) == 1:
        combined = messages[0]
        log.info(f"[{phone}] Processing 1 message")
    else:
        combined = "\n".join(messages)
        log.info(f"[{phone}] Processing {len(messages)} batched messages")

    # Keep typing indicator running while AI is generating the reply
    reply = await get_mello_reply(phone, combined)

    # Stop typing indicator right before sending reply
    stop_typing(phone)

    # Send Mello's reply
    await send_whatsapp_message(phone, reply)


def schedule_debounce(phone: str):
    """
    Cancel existing timer and start fresh.
    Also starts/keeps the typing indicator running.
    """
    # Cancel existing debounce timer
    existing = debounce_tasks.get(phone)
    if existing and not existing.done():
        existing.cancel()

    # Start/keep typing indicator alive
    if phone not in typing_tasks or typing_tasks[phone].done():
        start_typing(phone)

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
    stop_typing(phone)
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