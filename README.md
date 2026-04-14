# Mello — WhatsApp Text Chatbot

AI mental health companion for WhatsApp, powered by Claude (Anthropic).

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
```
Fill in `.env`:
- `ANTHROPIC_API_KEY` → from https://console.anthropic.com
- `WHATSAPP_PHONE_ID` → from Meta Developer Console (step below)
- `WHATSAPP_TOKEN` → from Meta Developer Console (step below)

### 3. Run the server
```bash
python combine_server.py
```
Open → http://localhost:8000 — you can test Mello AI from the dashboard
immediately, even before WhatsApp is configured.

---

## Meta WhatsApp Setup (one-time)

### Step 1 — Create a Meta Developer App
1. Go to https://developers.facebook.com
2. My Apps → Create App → Business → give it a name
3. Add Product → WhatsApp → Set Up

### Step 2 — Get your credentials
In Meta Console → WhatsApp → API Setup:
- Copy **Phone Number ID** → paste as `WHATSAPP_PHONE_ID` in `.env`
- Copy **Temporary Access Token** → paste as `WHATSAPP_TOKEN` in `.env`
- Add **your personal phone number** as a test recipient

### Step 3 — Expose your server with ngrok
```bash
# Install ngrok: https://ngrok.com/download
ngrok http 8000
# Copy the https://xxxx.ngrok.io URL
```

### Step 4 — Configure Webhook in Meta Console
1. Meta Console → WhatsApp → Configuration
2. Callback URL: `https://xxxx.ngrok.io/webhook`
3. Verify Token: `mello_verify_123` (or whatever you set in .env)
4. Click Verify and Save
5. Webhook Fields → Subscribe to `messages`

### Step 5 — Test it!
Open WhatsApp on your Android phone → send a message to your Meta test number → Mello replies!

---

## Architecture

```
Your Android WhatsApp
       ↓  (sends message)
Meta WhatsApp Cloud API
       ↓  (POST webhook)
server.py  /webhook endpoint
       ↓
Claude AI (Mello persona)
       ↓
WhatsApp Cloud API  (sends reply)
       ↓
Your Android WhatsApp  ✓
```

## File structure
```
whatsapp-mello/
├── server.py          — Main FastAPI server + webhook handler
├── requirements.txt
├── .env.example       — Copy to .env and fill in
├── logs/
│   └── mello.log      — Conversation logs
└── static/
    └── dashboard.html — Debug dashboard at localhost:8000
```
