#!/usr/bin/env python3
"""
Health Webhook Server:
  POST /telegram  — Telegram bot webhook (єдине активне призначення цього сервера)
  GET  /          — health check

ІСТОРІЯ (запит Олега, 09.10): раніше тут жили ще три джерела здоров'я —
Health Auto Export ZIP (/upload), Healthy Widgets JSON (POST /) і QWatch Pro
автосинк через Apple Shortcuts (/qw, /qwatch, /health). Усі видалені:
дані здоров'я тепер ВИКЛЮЧНО з Garmin Connect (garmin_sync.py, пише
qwatch_data.json через qwsync.save() напряму з коду, без зовнішніх вебхуків).
Сервер лишається тільки як транспорт для Telegram-вебхука бота.
"""

import os, json
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.request

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]


def send_telegram(text):
    # quiet-guard: режим сну (/сон) — жодних сповіщень і нагадувань до 04:00
    try:
        import quiet as _q_g
        if _q_g.blocked("msg"):
            print("[quiet] 🌙 сон: send_telegram придушено", flush=True)
            return False
    except Exception:
        pass
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4090],
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)


# ─── HTTP Handler ────────────────────────────────────────────────────────────

def read_body(handler):
    """Зчитує тіло запиту, враховуючи chunked encoding."""
    content_length = int(handler.headers.get("Content-Length", 0))
    if content_length:
        return handler.rfile.read(content_length)
    # Chunked або без Content-Length — читаємо до закриття
    data = b""
    while True:
        chunk = handler.rfile.read(4096)
        if not chunk:
            break
        data += chunk
    return data


# Дедуп апдейтів webhook (update_id -> час отримання)
_WH_SEEN = {}


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[Health] {format % args}", flush=True)

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Health Webhook OK")

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        content_type = self.headers.get("Content-Type", "")
        content_length = self.headers.get("Content-Length", "0")
        print(f"[POST] path={path} ct={content_type} len={content_length}", flush=True)

        # ── Telegram webhook — єдине, що цей сервер обробляє ─────────────────
        if path == "/telegram":
            self._handle_telegram_webhook()
            return

        # Усе інше (старі /upload, /qw, /qwatch, /health, POST /) — видалено
        # разом із іншими джерелами даних здоров'я (запит Олега, 09.10).
        self.send_response(410)
        self.end_headers()
        self.wfile.write(b'{"ok":false,"error":"endpoint removed, Garmin Connect only"}')

    def _handle_telegram_webhook(self):
        """Приймає апдейти від Telegram і віддає їх у ту саму обробку, що й polling.

        Telegram вимагає ШВИДКУ відповідь 200, інакше повторює апдейт і зрештою
        призупиняє доставку. Тому: спершу віддаємо 200, потім обробляємо у потоці.
        Захист — секретний заголовок, щоб ніхто сторонній не міг слати апдейти.
        """
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
        except Exception as e:
            print(f"[TG-WH] read error: {e}", flush=True)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        secret_expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        secret_got = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret_expected and secret_got != secret_expected:
            print("[TG-WH] REJECTED: bad secret token", flush=True)
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"forbidden")
            return

        # 200 одразу — до обробки
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        try:
            self.wfile.write(b"OK")
        except Exception:
            pass

        def _work():
            try:
                update = json.loads(raw.decode("utf-8"))
            except Exception as e:
                print(f"[TG-WH] bad JSON: {e}", flush=True)
                return
            uid = update.get("update_id")
            # Дедуп: Telegram повторює апдейт, якщо не отримав 200 вчасно
            global _WH_SEEN
            now = time.time()
            for k in [k for k, v in list(_WH_SEEN.items()) if now - v > 600]:
                _WH_SEEN.pop(k, None)
            if uid in _WH_SEEN:
                print(f"[TG-WH] duplicate update_id={uid} — skip", flush=True)
                return
            _WH_SEEN[uid] = now
            kind = ("callback_query" if update.get("callback_query")
                    else "message" if update.get("message") else "other")
            print(f"[TG-WH] update_id={uid} type={kind}", flush=True)
            try:
                import bot as _bot
                _bot.process_update(update)
            except Exception as e:
                import traceback
                print(f"[TG-WH] process_update failed: {e}", flush=True)
                traceback.print_exc()

        threading.Thread(target=_work, daemon=True, name="tg-webhook").start()


def run_server():
    port = int(os.environ.get("PORT", 8080))
    # ThreadingHTTPServer, а не HTTPServer: Telegram шле апдейти пачками, а
    # однопотоковий сервер обробляв би їх послідовно і ловив таймаути.
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"=== Health Webhook Server on port {port} ===", flush=True)
    print("Endpoints: POST /telegram (тільки це активне)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
