# WhatsApp Business Calling API — Complete Reference

> Compiled from Meta Developer Docs, Pipecat, yCloud, Infobip, and Scribd API Spec (v0.97)
> Last updated: 2026-03-23

---

## Table of Contents
1. [Overview](#overview)
2. [Prerequisites & Setup](#prerequisites--setup)
3. [Architecture](#architecture)
4. [User-Initiated Call Flow](#user-initiated-call-flow)
5. [Business-Initiated Call Flow](#business-initiated-call-flow)
6. [API Endpoints](#api-endpoints)
7. [Webhook Events](#webhook-events)
8. [Call Actions](#call-actions)
9. [SDP Handling](#sdp-handling)
10. [SIP Integration](#sip-integration)
11. [Limits & Restrictions](#limits--restrictions)
12. [Error Codes](#error-codes)
13. [Current Implementation Analysis](#current-implementation-analysis)

---

## Overview

WhatsApp Business Calling API enables VoIP calls between WhatsApp users and businesses using a **"Bring-Your-Own-VoIP-system"** approach:
- **Meta** handles the connection to the WhatsApp user
- **You** handle the business leg (WebRTC or SIP)

Two call directions:
- **User-Initiated**: User calls business from WhatsApp app
- **Business-Initiated**: Business calls user (requires explicit permission first)

All calls are **end-to-end encrypted** and use **VoIP only** (cannot connect to PSTN/landlines).

---

## Prerequisites & Setup

### Required
- [x] WhatsApp Business App on Meta Developer Console
- [x] Verified phone number on Cloud API
- [x] Payment method added and verified
- [x] `whatsapp_business_messaging` permission enabled
- [x] Webhook URL configured
- [x] Subscribed to `calls` webhook field (AND `messages` field)
- [x] Calling toggle enabled in Meta Developer Dashboard
- [x] Messaging limit ≥ 1,000 business-initiated conversations per rolling 24hr

### Environment Variables Needed
```
WHATSAPP_TOKEN=<access_token>
WHATSAPP_PHONE_ID=<phone_number_id>
WHATSAPP_APP_SECRET=<app_secret>
WEBHOOK_VERIFY_TOKEN=<your_verify_token>
```

### Enable Calling via API (alternative to dashboard toggle)
```bash
POST https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/settings
{
  "calling": {
    "status": "ENABLED",
    "iconVisibility": "DEFAULT"
  }
}
```

---

## Architecture

```
┌──────────────┐     WebRTC (SDP)      ┌──────────────┐     WebRTC/Audio      ┌──────────────┐
│  WhatsApp    │ ◄──────────────────► │  Meta Cloud  │ ◄──────────────────► │  Your Server │
│  User App    │     E2E Encrypted     │  API         │     Graph API +      │  (Mello)     │
└──────────────┘                       └──────────────┘     Webhooks          └──────┬───────┘
                                                                                      │
                                                                               ┌──────▼───────┐
                                                                               │  AI Engine   │
                                                                               │  (Hume EVI)  │
                                                                               └──────────────┘
```

**Signaling**: HTTPS/TLS (Graph API + Webhooks)
**Media**: WebRTC (ICE + DTLS + SRTP) — default
**Alternative Media**: SIP with WebRTC or SDES SRTP (requires explicit configuration)
**Audio Codec**: OPUS (G.711 coming soon)

---

## User-Initiated Call Flow

This is what happens when a WhatsApp user calls your business number:

```
Step  Who           Action                        Details
───── ──────────── ──────────────────────────── ─────────────────────────────
  1   User          Taps call button              In WhatsApp chat with business
  2   Meta → You    POST /whatsapp webhook        event="connect" + SDP offer
  3   You           Create WebRTC PeerConnection  Using aiortc or browser WebRTC
  4   You           Set remote SDP (offer)        From webhook payload
  5   You           Create SDP answer             Via WebRTC createAnswer()
  6   You → Meta    POST /{phone_id}/calls        action="pre_accept" + SDP answer
  7   You → Meta    POST /{phone_id}/calls        action="accept" + SDP answer
  8   Both          Media flows (audio)           WebRTC ICE/DTLS/SRTP established
  9   Either        Hangup                        Terminate event sent
 10   You → Meta    POST /{phone_id}/calls        action="terminate" (for billing!)
```

### Timing
- You have **30-60 seconds** to accept after receiving the connect webhook
- If not accepted, WhatsApp shows "Not Answered" to user and sends terminate webhook
- **pre_accept is optional but HIGHLY recommended** — it pre-establishes media connection so audio starts immediately on accept (no clipping)

### Webhook: Incoming Call (connect)
```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<WABA_ID>",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": { "phone_number_id": "<PHONE_NUMBER_ID>" },
        "calls": [{
          "id": "<CALL_ID>",
          "from": "<USER_PHONE>",
          "event": "connect",
          "session": {
            "sdp": "<RFC 4566 SDP OFFER>",
            "sdp_type": "offer"
          },
          "biz_opaque_callback_data": "<optional tracking, up to 512 chars>"
        }]
      },
      "field": "calls"
    }]
  }]
}
```

### API: Pre-Accept Call
```bash
POST https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/calls
Authorization: Bearer {TOKEN}
Content-Type: application/json

{
  "call_id": "<CALL_ID from webhook>",
  "action": "pre_accept",
  "session": {
    "sdp": "<YOUR SDP ANSWER>",
    "sdp_type": "answer"
  }
}
```

### API: Accept Call
```bash
POST https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/calls
Authorization: Bearer {TOKEN}
Content-Type: application/json

{
  "call_id": "<CALL_ID from webhook>",
  "action": "accept",
  "session": {
    "sdp": "<YOUR SDP ANSWER>",
    "sdp_type": "answer"
  }
}
```

**IMPORTANT**: If you send `accept` before `pre_accept`, the API will reject the call!

### API: Reject Call
```bash
POST https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/calls
Authorization: Bearer {TOKEN}

{
  "call_id": "<CALL_ID>",
  "action": "reject"
}
```

---

## Business-Initiated Call Flow

Business calls user. **Blocked in USA, Canada, Egypt, Vietnam, Nigeria.**

```
Step  Who           Action                           Details
───── ──────────── ─────────────────────────────── ─────────────────────────────
  1   You → User    Send call_permission_request      Interactive or template message
  2   User          Accepts/Rejects permission        Webhook with call_permission_reply
  3   You → Meta    POST /{phone_id}/calls             action="connect" + SDP offer
  4   Meta → You    Webhook: connect                   SDP answer from Meta
  5   Meta → You    Webhook: status RINGING            User's phone ringing
  6   Meta → You    Webhook: status ACCEPTED           User answered
  7   Both          Media flows                        Audio streaming
  8   Either        Hangup → terminate webhook
```

### Step 1: Request Permission (Free-form)
```bash
POST https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages
Authorization: Bearer {TOKEN}

{
  "messaging_product": "whatsapp",
  "to": "<USER_PHONE>",
  "type": "interactive",
  "interactive": {
    "type": "call_permission_request",
    "action": {
      "name": "call_permission_request"
    },
    "body": {
      "text": "We would like to call you to discuss your mental health check-in."
    }
  }
}
```

### Step 1b: Request Permission (Template)
Create a template with component type `call_permission_request`, then send via template message.

### Permission Response Webhook
```json
{
  "messages": [{
    "type": "interactive",
    "interactive": {
      "type": "call_permission_reply",
      "call_permission_reply": {
        "response": "accept",          // or "reject"
        "is_permanent": false,
        "expiration_timestamp": "...",
        "response_source": "user_action"  // or "automatic"
      }
    }
  }]
}
```

### Step 3: Initiate Outbound Call
```bash
POST https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/calls
Authorization: Bearer {TOKEN}

{
  "to": "<USER_PHONE>",
  "action": "connect",
  "session": {
    "sdp": "<YOUR SDP OFFER>",
    "sdp_type": "offer"
  }
}
```

### Status Webhooks (Business-Initiated)
```json
// RINGING
{ "status": "RINGING", "recipientPhone": "+91..." }

// ACCEPTED (user answered)
{ "status": "ACCEPTED", "recipientPhone": "+91..." }
```

---

## API Endpoints

### Main Calling Endpoint
```
POST https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/calls
Authorization: Bearer {WHATSAPP_TOKEN}
Content-Type: application/json
```

**All call actions go through this single endpoint** — differentiated by the `action` field.

### Actions Summary

| Action        | Direction         | Purpose                                    |
|---------------|-------------------|--------------------------------------------|
| `pre_accept`  | User-initiated    | Pre-establish media (optional, recommended)|
| `accept`      | User-initiated    | Accept call, start media                   |
| `reject`      | User-initiated    | Reject incoming call                       |
| `connect`     | Business-initiated| Initiate outbound call                     |
| `terminate`   | Both              | End active call                            |

---

## Webhook Events

### Subscribe to webhook fields:
- `messages` — text, media, interactive messages
- `calls` — all call events (connect, terminate, status)

### Event Types

| Event         | Trigger                              | Key Fields                        |
|---------------|--------------------------------------|-----------------------------------|
| `connect`     | User calls business                  | call_id, from, session.sdp        |
| `disconnect`  | Connection dropped                   | call_id                           |
| `terminate`   | Call ended (either party)            | call_id, status, duration         |
| Status update | Business-initiated call state change | status (RINGING/ACCEPTED/etc)     |

### Terminate Webhook Payload
```json
{
  "calls": [{
    "id": "<CALL_ID>",
    "from": "<USER_PHONE>",
    "event": "terminate",
    "startTime": 1733734738000,
    "endTime": 1733734771000,
    "duration": 33,
    "status": "COMPLETED"  // or "FAILED", "REJECTED"
  }]
}
```

---

## SDP Handling

- Must comply with **RFC 4566**
- WhatsApp only accepts **SHA-256** fingerprints in SDP
- Filter out non-SHA-256 fingerprints from your SDP answer
- Non-compliant SDP causes errors like "Invalid Connection info"

### SDP Filtering (already in our code)
```python
def filter_sdp_fingerprints(sdp: str) -> str:
    """WhatsApp only accepts SHA-256 fingerprints."""
    lines = sdp.split("\r\n")
    return "\r\n".join(
        l for l in lines
        if not (l.startswith("a=fingerprint:") and "sha-256" not in l.lower())
    )
```

---

## SIP Integration

Alternative to WebRTC for the business leg:

- **SIP with WebRTC media**: Use SIP for signaling, WebRTC for media
- **SIP with SDES SRTP**: Use SIP for signaling, SDES for media encryption
- Requires **explicit configuration** (not default)
- Audio codec: OPUS (G.711 coming soon)
- If using SIP, you don't need to subscribe to `calls` webhook field

### When to use SIP
- You have existing call center infrastructure (Asterisk, FreeSWITCH, etc.)
- You want to integrate with PBX/contact center systems
- For Mello (AI voice agent), **WebRTC is the correct choice**

---

## Limits & Restrictions

| Limit                              | Value                                   |
|------------------------------------|-----------------------------------------|
| Max concurrent calls               | 1,000 per account                       |
| Call duration limit                 | None                                    |
| Answer timeout                     | 30-60 seconds                           |
| Business-initiated daily limit     | 100 calls per user (was 10 before Dec 2025) |
| Call permission requests           | 1 per 24hr, 2 per 7 days               |
| Permission reset                   | After a connected call occurs           |
| biz_opaque_callback_data           | Up to 512 characters                    |
| DTMF support                       | 0-9, #, * (500ms duration, 100ms gap)  |

### Geographic Restrictions
**Business-initiated calling blocked in**: USA, Canada, Egypt, Vietnam, Nigeria
**User-initiated calling blocked in**: Cuba, Iran, North Korea, Syria, Ukraine (Crimea/Donetsk/Luhansk)

### Unanswered Call Thresholds
- Sandbox: 5 consecutive unanswered → warning
- Production: 2 consecutive unanswered → warning

---

## Error Codes

| Code    | Meaning                                              |
|---------|------------------------------------------------------|
| 138006  | Lack of call permission for this business number     |
| SDP err | Invalid Connection info / SDP validation failure     |

---

## Current Implementation Analysis

### What's Correct in `combine_server.py`
- ✅ Webhook verification (GET /whatsapp)
- ✅ Webhook receiver (POST /whatsapp) with signature verification
- ✅ SDP fingerprint filtering (SHA-256 only)
- ✅ Pre-accept → Accept two-step flow
- ✅ WebRTC via aiortc (correct for server-side AI agent)
- ✅ Audio bridge to Hume EVI
- ✅ Call cleanup on disconnect/terminate/fail
- ✅ Separate webhook paths (/webhook for text, /whatsapp for calls)

### Potential Issues Found

1. **API URL path may be wrong** (line 412):
   ```python
   # CURRENT (possibly wrong):
   url = f"https://graph.facebook.com/v19.0/phone_numbers/{WHATSAPP_PHONE_ID}/calls"

   # SHOULD BE:
   url = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_ID}/calls"
   ```
   The `phone_numbers/` prefix is not standard. The Meta Graph API uses `/{phone_number_id}/calls` directly.

2. **API version outdated**: v19.0 → should be v22.0 (latest)

3. **Missing terminate call on hangup**: When the call ends, we should explicitly call `action="terminate"` for accurate billing, even if we receive a disconnect/terminate webhook.

4. **Webhook structure mismatch**: The code processes calls from `value.get("calls", [])` which is correct. But the event field parsing (`call.get("event")`) should handle both `connect` and `terminate` events properly.

5. **No DTMF handling**: No support for keypad input (may be needed for IVR flows).

---

## Sources
- [Meta Official Docs](https://developers.facebook.com/docs/whatsapp/cloud-api/calling/)
- [Pipecat WhatsApp Integration](https://docs.pipecat.ai/guides/features/whatsapp)
- [Pipecat API Reference](https://reference-server.pipecat.ai/en/latest/api/pipecat.transports.whatsapp.api.html)
- [yCloud Calling Examples](https://docs.ycloud.com/reference/whatsapp-calling-examples)
- [Infobip User-Initiated Calling](https://www.infobip.com/docs/whatsapp/whatsapp-business-calling/user-initiated-calling)
- [wuSeller API Guide](https://www.wuseller.com/whatsapp-business-knowledge-hub/whatsapp-business-calling-api-integration-sip-limits-2026/)
- [ChakraHQ Feature Details](https://chakrahq.com/article/whatsapp-cloud-api-calling-feature-details/)
- [Cloud API Calling GraphAPI Spec v0.97](https://www.scribd.com/document/861640303/Cloud-API-Calling-GraphAPI-Spec-Beta-V0-97)
