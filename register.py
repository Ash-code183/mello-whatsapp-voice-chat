import requests

# Your specific details
PHONE_NUMBER_ID = "1057824980740751"
ACCESS_TOKEN = "EAAnqZAETwIu0BQzyQ9smuoSsXekSGe9K6dHZC1Wei7eP0SKacagZBpGva18IfcRC2VDZAZAqJps8JBBlbzDE2V8wBvQk5qZCZAMhIwRRTLqMowpZB5I805o3mbZAOPZBxbmW99E2ZC8ObA779yzzS8h4LxQOxKALZCMuic9Rdy8QSEGZAUZAA3vmydFxry7qJAnGe87ldGAgZDZD"
CERTIFICATE = (
    "CmIKHgi19oLf9oqZAxIGZW50OndhIgVNZWxsb1CapoHOBhpAimhs3ID0pk2KbpImkI6A"
    "IufZR+Li9B3x9XQQt1AMYpYDANzCHiOJ2ncxtqnPfzxtNgd/dsD2y+Cuq2M8Qt5ACBIvb"
    "RZr+7Kml/7zWrK7maRtKZJY4ONUzPYF3GoAhosc/Aj2RjNyXCAg0oLm1pN34DQ="
)

# The registration endpoint
url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/register"

# Payload without the 'pin' field
payload = {
    "messaging_product": "whatsapp",
    "pin": "123456",  # Replace with the actual PIN you received
    "certificate": CERTIFICATE,
}

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

# Sending the request
response = requests.post(url, json=payload, headers=headers)

print(f"Status Code: {response.status_code}")
print(f"Response: {response.json()}")
