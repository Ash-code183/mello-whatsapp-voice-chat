import httpx, os, json
from dotenv import load_dotenv
load_dotenv()

endpoint = os.getenv("AZURE_OPENAI_ENDPOINT").rstrip("/")
key = os.getenv("AZURE_OPENAI_KEY")
deployment = os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-5.2-chat")

url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-12-01-preview"

payload = {
    "messages": [{"role": "user", "content": "say hello"}],
    "max_tokens": 50
}

try:
    r = httpx.post(url, json=payload, headers={"api-key": key}, timeout=30)
    print("Status:", r.status_code)
    print("Response:", r.text[:300])
except Exception as e:
    print("ERROR:", e)