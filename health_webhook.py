#!/usr/bin/env python3
"""
Health Webhook Server — приймає дані від Healthy Widgets і надсилає звіт в Telegram.
Запускається як окремий процес на порту 8080.
"""

import os, json, threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]


def send_telegram(text):
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
        print(f"Telegram error: {e}")


def format_health_report(data):
    """Форматує дані від Healthy Widgets у читабельний звіт."""
    local = datetime.now(timezone.utc) + timedelta(hours=2)
    date_str = local.strftime("%d.%m.%Y %H:%M")

    lines = [f"<b>Health звіт · {date_str}</b>", "━━━━━━━━━━━━━━━━━━━━"]

    # Кроки
    steps = (data.get("step_count") or data.get("stepCount") or
             data.get("steps") or data.get("HKQuantityTypeIdentifierStepCount"))
    if steps:
        try:
            s = int(float(str(steps).split()[0]))
            emoji = "🔥" if s >= 10000 else ("👍" if s >= 7000 else "🚶")
            lines.append(f"\n{emoji} <b>Кроки:</b> {s:,}")
            if s >= 10000:
                lines.append("   Норму виконано!")
            else:
                lines.append(f"   До норми: {10000-s:,} кроків")
        except: pass

    # Пульс
    hr = (data.get("heart_rate") or data.get("heartRate") or
          data.get("HKQuantityTypeIdentifierHeartRate"))
    if hr:
        try:
            h = int(float(str(hr).split()[0]))
            emoji = "❤️" if 60 <= h <= 80 else ("⚠️" if h > 100 else "💙")
            lines.append(f"\n{emoji} <b>Пульс:</b> {h} bpm")
        except: pass

    # Вага
    weight = (data.get("body_mass") or data.get("bodyMass") or
              data.get("weight") or data.get("HKQuantityTypeIdentifierBodyMass"))
    if weight:
        try:
            w = float(str(weight).split()[0])
            lines.append(f"\n⚖️ <b>Вага:</b> {w:.1f} кг")
        except: pass

    # Сон
    sleep = (data.get("sleep") or data.get("sleepAnalysis") or
             data.get("HKCategoryTypeIdentifierSleepAnalysis"))
    if sleep:
        try:
            s = float(str(sleep).split()[0])
            if 0 < s < 24:
                emoji = "😴" if s >= 7 else "😵"
                lines.append(f"\n{emoji} <b>Сон:</b> {s:.1f} год")
                if s < 7:
                    lines.append("   Менше норми (7-9г)")
        except: pass

    # Активні калорії
    kcal = (data.get("active_energy") or data.get("activeEnergy") or
            data.get("HKQuantityTypeIdentifierActiveEnergyBurned"))
    if kcal:
        try:
            k = int(float(str(kcal).split()[0]))
            lines.append(f"\n🔥 <b>Активні калорії:</b> {k} ккал")
        except: pass

    # Дистанція
    dist = (data.get("distance") or data.get("distanceWalkingRunning") or
            data.get("HKQuantityTypeIdentifierDistanceWalkingRunning"))
    if dist:
        try:
            d = float(str(dist).split()[0])
            lines.append(f"\n🗺 <b>Дистанція:</b> {d:.2f} км")
        except: pass

    # Кисень у крові
    spo2 = (data.get("oxygen_saturation") or data.get("oxygenSaturation") or
            data.get("HKQuantityTypeIdentifierOxygenSaturation"))
    if spo2:
        try:
            o = float(str(spo2).split()[0])
            if o <= 1: o *= 100
            emoji = "✅" if o >= 95 else "⚠️"
            lines.append(f"\n{emoji} <b>SpO2:</b> {o:.0f}%")
        except: pass

    # Якщо нічого не розпізнали — показуємо raw
    if len(lines) <= 2:
        lines.append("\n📊 Дані отримано:")
        for k, v in list(data.items())[:15]:
            if v:
                lines.append(f"  {k}: {v}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[Health Webhook] {format % args}", flush=True)

    def do_GET(self):
        """Health check endpoint."""
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

            print(f"[Health] Received {length} bytes: {body[:200]}", flush=True)

            # Парсимо JSON
            data = {}
            if body:
                try:
                    data = json.loads(body.decode("utf-8", errors="replace"))
                except Exception:
                    # Спробуємо як form-encoded
                    from urllib.parse import parse_qs
                    parsed = parse_qs(body.decode("utf-8", errors="replace"))
                    data = {k: v[0] for k, v in parsed.items()}

            # Форматуємо і надсилаємо
            report = format_health_report(data)
            send_telegram(report)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())

        except Exception as e:
            print(f"[Health] Error: {e}", flush=True)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())


def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"=== Health Webhook Server on port {port} ===", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
