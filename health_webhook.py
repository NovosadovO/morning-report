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


def parse_metrics(data):
    """Парсить формат Healthy Widgets: {'metrics': [{'name': '...', 'data': [...]}]}"""
    result = {}

    metrics = data.get("metrics", [])
    if not metrics:
        # Спробуємо плоский формат
        return data

    for metric in metrics:
        name = metric.get("name", "")
        entries = metric.get("data", [])
        if not entries:
            continue

        # Беремо останнє значення
        last = entries[-1]
        qty = last.get("qty")
        if qty is None:
            continue

        result[name] = qty

    return result


def format_health_report(raw_data):
    """Форматує дані від Healthy Widgets у читабельний звіт."""
    local = datetime.now(timezone.utc) + timedelta(hours=2)
    date_str = local.strftime("%d.%m.%Y %H:%M")

    # Парсимо вкладену структуру
    data = parse_metrics(raw_data)

    lines = [f"<b>Health звіт · {date_str}</b>", "━━━━━━━━━━━━━━━━━━━━"]

    # Вага
    weight = data.get("weight_body_mass")
    if weight:
        try:
            w = float(weight)
            lines.append(f"\n⚖️ <b>Вага:</b> {w:.1f} кг")
        except: pass

    # Кроки — сума за день
    steps = data.get("step_count") or data.get("steps")
    if steps is None:
        # Спробуємо знайти в metrics масиві і посумувати за сьогодні
        today = local.strftime("%Y-%m-%d")
        for metric in raw_data.get("metrics", []):
            if "step" in metric.get("name","").lower():
                total = sum(
                    float(e.get("qty",0))
                    for e in metric.get("data",[])
                    if e.get("date","").startswith(today)
                )
                if total > 0:
                    steps = total
                    break
    if steps:
        try:
            s = int(float(steps))
            emoji = "🔥" if s >= 10000 else ("👍" if s >= 7000 else "🚶")
            lines.append(f"\n{emoji} <b>Кроки:</b> {s:,}")
            if s >= 10000:
                lines.append("   Норму виконано!")
            else:
                lines.append(f"   До норми: {10000-s:,} кроків")
        except: pass

    # Пульс
    hr = data.get("heart_rate") or data.get("resting_heart_rate")
    if hr:
        try:
            h = int(float(hr))
            emoji = "❤️" if 60 <= h <= 80 else ("⚠️" if h > 100 else "💙")
            lines.append(f"\n{emoji} <b>Пульс:</b> {h} bpm")
        except: pass

    # Вода
    water = data.get("dietary_water") or data.get("water")
    if water:
        try:
            w = float(water)
            # Конвертуємо мл якщо треба
            if w < 20:  # літри
                w_ml = int(w * 1000)
            else:
                w_ml = int(w)
            emoji = "💧" if w_ml >= 2000 else "🫗"
            lines.append(f"\n{emoji} <b>Вода:</b> {w_ml} мл")
            if w_ml < 2000:
                lines.append(f"   До норми: {2000-w_ml} мл")
        except: pass

    # Сон
    sleep = data.get("sleep_analysis") or data.get("sleep")
    if sleep:
        try:
            s = float(sleep)
            if 0 < s < 24:
                emoji = "😴" if s >= 7 else "😵"
                lines.append(f"\n{emoji} <b>Сон:</b> {s:.1f} год")
                if s < 7:
                    lines.append("   Менше норми (7-9г)")
        except: pass

    # Активні калорії
    kcal = data.get("active_energy_burned") or data.get("active_energy")
    if kcal:
        try:
            k = int(float(kcal))
            lines.append(f"\n🔥 <b>Активні калорії:</b> {k} ккал")
        except: pass

    # Дистанція
    dist = data.get("walking_running_distance") or data.get("distance")
    if dist:
        try:
            d = float(dist)
            lines.append(f"\n🗺 <b>Дистанція:</b> {d:.2f} км")
        except: pass

    # SpO2
    spo2 = data.get("oxygen_saturation")
    if spo2:
        try:
            o = float(spo2)
            if o <= 1: o *= 100
            emoji = "✅" if o >= 95 else "⚠️"
            lines.append(f"\n{emoji} <b>SpO2:</b> {o:.0f}%")
        except: pass

    # Якщо нічого не розпізнали
    if len(lines) <= 2:
        lines.append("\n📊 Отримані метрики:")
        for k, v in list(data.items())[:10]:
            if v is not None:
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
