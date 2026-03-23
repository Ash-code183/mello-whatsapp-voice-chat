from fastapi import FastAPI, Request, Response
import json

import requests

app = FastAPI()

ACCESS_TOKEN = "EAAnqZAETwIu0BRK8NwxPlx1XHp2vMFyZB7lLeX5yCLVR7qKmWapjNCilX2ZAnOhKpQdsu1syTTw9P3n7qgvut8W00wmWYsGo9ZC75MdjuCENNQiD0rZCxaZAmxacMJjMLaaIZA99vAhUIcIl4R2GLyjkbPmI3ZA6VSxLELVHV4Ef7nMPsJSiXvpenLSZBw3deoPBk8QZDZD"


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    # Extract the incoming message details
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" in value:
            message = value["messages"][0]
            recipient_id = message["from"]

            # Dynamically capture the phone ID that received the message
            incoming_phone_id = value["metadata"]["phone_number_id"]

            # 1. Mark the incoming message as read and trigger the short typing flash
            mark_as_read(incoming_phone_id, message["id"])

            # 2. Send your AI response (Mello's reply)
            send_whatsapp_message(
                incoming_phone_id,
                recipient_id,
                "Hey ðŸ˜Š Iâ€™m here. How are you feeling right now?",
            )
    except Exception as e:
        print(f"Error processing webhook: {e}")

    return Response(status_code=200)


def mark_as_read(phone_id, message_id):
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    requests.post(url, json=payload, headers=headers)


def send_whatsapp_message(phone_id, recipient_id, text):
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_id,
        "type": "text",
        "text": {"body": text},
    }
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    requests.post(url, json=payload, headers=headers)
