import requests

# Your specific details
PHONE_NUMBER_ID = "1057824980740751"
ACCESS_TOKEN = "EAAnqZAETwIu0BRK8NwxPlx1XHp2vMFyZB7lLeX5yCLVR7qKmWapjNCilX2ZAnOhKpQdsu1syTTw9P3n7qgvut8W00wmWYsGo9ZC75MdjuCENNQiD0rZCxaZAmxacMJjMLaaIZA99vAhUIcIl4R2GLyjkbPmI3ZA6VSxLELVHV4Ef7nMPsJSiXvpenLSZBw3deoPBk8QZDZD"
RECIPIENT_PHONE = "917498304051"

# The messages endpoint
url = f"https://graph.facebook.com/v22.0/1057824980740751/messages"

# Payload for a template message (required for first contact)
payload = {
    "messaging_product": "whatsapp",
    "to": RECIPIENT_PHONE,
    "type": "template",
    "template": {
        "name": "hello_world",  # Standard test template
        "language": {"code": "en_US"},
    },
}

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

# Sending the request
response = requests.post(url, json=payload, headers=headers)

print(f"Status Code: {response.status_code}")
print(f"Response: {response.json()}")
