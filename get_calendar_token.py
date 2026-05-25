#!/usr/bin/env python3
"""
Скрипт для отримання Google Calendar OAuth2 refresh token.
Запускати ЛОКАЛЬНО або через Railway one-off command.
"""
import os, json, urllib.parse, urllib.request

CLIENT_ID     = os.environ.get("GMAIL_CLIENT_ID", "878341164164-4qki4apv3mmo2s8006v9ks10q61sf5uk.apps.googleusercontent.com")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "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")

SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
])

# Step 1: отримуємо auth URL
params = {
    "client_id": CLIENT_ID,
    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
    "response_type": "code",
    "scope": SCOPES,
    "access_type": "offline",
    "prompt": "consent",
}
auth_url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)
print("\n=== КРОК 1 ===")
print("Відкрий це посилання в браузері:")
print(auth_url)
print()

# Step 2: вводимо code
code = input("Вставте code з браузера: ").strip()

# Step 3: обмінюємо на tokens
body = urllib.parse.urlencode({
    "code": code,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
    "grant_type": "authorization_code",
}).encode()

req = urllib.request.Request(
    "https://oauth2.googleapis.com/token",
    data=body, method="POST"
)
with urllib.request.urlopen(req, timeout=15) as r:
    tokens = json.loads(r.read())

print("\n=== РЕЗУЛЬТАТ ===")
print("access_token:", tokens.get("access_token", "")[:30], "...")
print("refresh_token:", tokens.get("refresh_token", "NOT FOUND"))
print()
print("Додай в Railway env:")
print(f"GMAIL_REFRESH_TOKEN={tokens.get('refresh_token', '')}")
