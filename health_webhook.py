#!/usr/bin/env python3
"""
Health Webhook Server:
  POST /          — Healthy Widgets JSON (щоденний)
  POST /upload    — Health Auto Export ZIP (щотижневий/місячний звіт)
  GET  /          — health check
"""

import os, json, csv, io, zipfile, tempfile
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
        print(f"Telegram error: {e}", flush=True)


# ─── Health Auto Export ZIP Parser ───────────────────────────────────────────

def parse_hae_csv(content):
    """
    Парсить HAE CSV формат:
    - Рядки 0-4: перші 5 назв метрик (по одній на рядок)
    - Рядок 5: решта 120 назв метрик через кому
    - Рядки 6+: дані по датах (125 колонок)
    """
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)

    if len(rows) < 7:
        return None, []

    full_headers = [rows[i][0] for i in range(5)] + rows[5]
    data_rows = rows[6:]
    return full_headers, data_rows


def get_val(row, headers, name):
    """Знаходить колонку за підрядком назви і повертає float або None."""
    for i, h in enumerate(headers):
        if name in h and i < len(row) and row[i].strip():
            try:
                return float(row[i].strip())
            except:
                pass
    return None


def analyze_hae_zip(zip_bytes):
    """Аналізує ZIP від Health Auto Export, повертає dict зі статистикою."""
    stats = {}

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # Знаходимо головний CSV (HealthAutoExport-*.csv)
        main_csv = None
        workout_csvs = []
        for name in zf.namelist():
            if name.startswith("HealthAutoExport-") and name.endswith(".csv"):
                main_csv = name
            elif "Дистанція ходьби + бігу" in name and name.endswith(".csv") and "Route" not in name:
                workout_csvs.append(name)

        if not main_csv:
            return None

        content = zf.read(main_csv).decode("utf-8", errors="replace")
        headers, data_rows = parse_hae_csv(content)

        if not headers:
            return None

        # Визначаємо діапазон дат
        dates = [r[0][:10] for r in data_rows if r[0].strip()]
        stats["period_start"] = dates[0] if dates else "?"
        stats["period_end"] = dates[-1] if dates else "?"
        stats["days"] = len(dates)

        # Кроки
        steps_vals = []
        max_steps = 0
        max_steps_day = ""
        for r in data_rows:
            v = get_val(r, headers, "Кількість кроків")
            if v is not None:
                steps_vals.append(v)
                if v > max_steps:
                    max_steps = v
                    max_steps_day = r[0][:10]

        if steps_vals:
            stats["avg_steps"] = int(sum(steps_vals) / len(steps_vals))
            stats["max_steps"] = int(max_steps)
            stats["max_steps_day"] = max_steps_day

        # Вага
        weight_vals = []
        for r in data_rows:
            v = get_val(r, headers, "Вага (кг)")
            if v is not None:
                weight_vals.append((r[0][:10], v))

        if weight_vals:
            stats["weight_start"] = weight_vals[0][1]
            stats["weight_end"] = weight_vals[-1][1]
            stats["weight_diff"] = round(weight_vals[-1][1] - weight_vals[0][1], 1)

        # Дистанція (загальна щоденна)
        dist_vals = [get_val(r, headers, "Дистанція ходьби + бігу") for r in data_rows]
        dist_vals = [v for v in dist_vals if v is not None]
        if dist_vals:
            stats["total_dist_km"] = round(sum(dist_vals), 1)
            stats["avg_dist_km"] = round(sum(dist_vals) / len(dist_vals), 1)

        # Сон
        sleep_vals = [get_val(r, headers, "Аналіз сну [Уві сні]") for r in data_rows]
        sleep_vals = [v for v in sleep_vals if v is not None and v > 0]
        if sleep_vals:
            stats["avg_sleep"] = round(sum(sleep_vals) / len(sleep_vals), 1)
            stats["min_sleep"] = round(min(sleep_vals), 1)
            stats["max_sleep"] = round(max(sleep_vals), 1)

        # VO2 Max
        vo2_vals = [get_val(r, headers, "VO2 Макс") for r in data_rows]
        vo2_vals = [v for v in vo2_vals if v is not None]
        if vo2_vals:
            stats["vo2_max"] = round(vo2_vals[-1], 1)

        # HRV
        hrv_vals = [get_val(r, headers, "Варіабельність серцевого ритму") for r in data_rows]
        hrv_vals = [v for v in hrv_vals if v is not None]
        if hrv_vals:
            stats["hrv_avg"] = round(sum(hrv_vals) / len(hrv_vals), 0)

        # Пробіжки — кількість унікальних дат з workout CSV
        run_dates = set()
        for fname in workout_csvs:
            raw = zf.read(fname).decode("utf-8", errors="replace")
            for line in raw.split("\n")[1:]:
                if line.strip():
                    d = line[:10]
                    if d.startswith("20"):
                        run_dates.add(d)
        if run_dates:
            stats["run_days"] = len(run_dates)

    return stats


def format_hae_report(stats, period=None):
    """Форматує статистику HAE у Telegram повідомлення."""
    local = datetime.now(timezone.utc) + timedelta(hours=2)
    ts = local.strftime("%d.%m.%Y %H:%M")

    p_start = stats.get("period_start", "?")
    p_end = stats.get("period_end", "?")

    # Форматуємо дати
    def fmt_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%d.%m")
        except:
            return s

    period_str = f"{fmt_date(p_start)} – {fmt_date(p_end)}"

    lines = [
        f"<b>🏃 Health звіт · {period_str}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Кроки
    if "avg_steps" in stats:
        avg = stats["avg_steps"]
        mx = stats.get("max_steps", 0)
        mx_day = fmt_date(stats.get("max_steps_day", ""))
        emoji = "🔥" if avg >= 10000 else ("👍" if avg >= 7000 else "🚶")
        lines.append(f"\n{emoji} <b>Кроки (середнє/день):</b> {avg:,}")
        if mx:
            lines.append(f"   Рекорд: {mx:,} ({mx_day})")

    # Дистанція
    if "total_dist_km" in stats:
        lines.append(f"\n🗺 <b>Дистанція:</b> {stats['total_dist_km']} км за {stats['days']} днів")
        lines.append(f"   Середнє: {stats['avg_dist_km']} км/день")

    # Пробіжки
    if "run_days" in stats:
        lines.append(f"\n👟 <b>Пробіжки:</b> {stats['run_days']} за місяць")

    # Вага
    if "weight_start" in stats:
        diff = stats["weight_diff"]
        arrow = "📈" if diff > 0 else ("📉" if diff < 0 else "➡️")
        sign = "+" if diff > 0 else ""
        lines.append(f"\n⚖️ <b>Вага:</b> {stats['weight_start']} → {stats['weight_end']} кг")
        lines.append(f"   {arrow} {sign}{diff} кг за період")

    # Сон
    if "avg_sleep" in stats:
        avg_s = stats["avg_sleep"]
        emoji = "😴" if avg_s >= 7 else "😵"
        lines.append(f"\n{emoji} <b>Сон (середнє):</b> {avg_s} год")
        if "min_sleep" in stats:
            lines.append(f"   Мін: {stats['min_sleep']}г / Макс: {stats['max_sleep']}г")

    # VO2 Max
    if "vo2_max" in stats:
        v = stats["vo2_max"]
        fitness = "Відмінно 🏅" if v >= 55 else ("Добре 👍" if v >= 45 else "Середнє")
        lines.append(f"\n❤️ <b>VO2 Max:</b> {v} мл/(кг·хв) — {fitness}")

    # HRV
    if "hrv_avg" in stats:
        lines.append(f"\n💓 <b>HRV середнє:</b> {int(stats['hrv_avg'])} мс")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>Відправлено: {ts}</i>")

    return "\n".join(lines)


# ─── Healthy Widgets JSON Parser (щоденний) ──────────────────────────────────

def parse_metrics(data):
    result = {}
    metrics = data.get("metrics", [])
    if not metrics:
        return data
    from datetime import date
    today = date.today().isoformat()
    for metric in metrics:
        name = metric.get("name", "")
        entries = metric.get("data", [])
        if not entries:
            continue
        if "step" in name.lower():
            total = sum(float(e.get("qty", 0)) for e in entries
                        if e.get("date", "").startswith(today))
            if total == 0:
                total = sum(float(e.get("qty", 0)) for e in entries)
            result[name] = total
            continue
        if "sleep" in name.lower():
            last = entries[-1]
            total = last.get("totalSleep") or last.get("asleep") or last.get("qty")
            if total:
                result[name] = total
            continue
        last = entries[-1]
        qty = last.get("qty")
        if qty is not None:
            result[name] = qty
    return result


def format_health_report(raw_data):
    local = datetime.now(timezone.utc) + timedelta(hours=2)
    date_str = local.strftime("%d.%m.%Y %H:%M")
    data = parse_metrics(raw_data)
    lines = [f"<b>Health звіт · {date_str}</b>", "━━━━━━━━━━━━━━━━━━━━"]

    weight = data.get("weight_body_mass")
    if weight:
        try:
            lines.append(f"\n⚖️ <b>Вага:</b> {float(weight):.1f} кг")
        except: pass

    steps = data.get("step_count") or data.get("steps")
    if steps is None:
        today = local.strftime("%Y-%m-%d")
        for metric in raw_data.get("metrics", []):
            if "step" in metric.get("name","").lower():
                total = sum(float(e.get("qty",0)) for e in metric.get("data",[])
                            if e.get("date","").startswith(today))
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

    hr = data.get("heart_rate") or data.get("resting_heart_rate")
    if hr:
        try:
            h = int(float(hr))
            emoji = "❤️" if 60 <= h <= 80 else ("⚠️" if h > 100 else "💙")
            lines.append(f"\n{emoji} <b>Пульс:</b> {h} bpm")
        except: pass

    water = data.get("dietary_water") or data.get("water")
    if water:
        try:
            w_ml = int(float(water) * 1000) if float(water) < 20 else int(float(water))
            emoji = "💧" if w_ml >= 2000 else "🫗"
            lines.append(f"\n{emoji} <b>Вода:</b> {w_ml} мл")
            if w_ml < 2000:
                lines.append(f"   До норми: {2000-w_ml} мл")
        except: pass

    sleep = data.get("sleep_analysis") or data.get("sleep")
    if sleep:
        try:
            s = float(sleep)
            if 0 < s < 24:
                emoji = "😴" if s >= 7 else "😵"
                lines.append(f"\n{emoji} <b>Сон:</b> {s:.1f} год")
        except: pass

    kcal = data.get("active_energy_burned") or data.get("active_energy")
    if kcal:
        try:
            lines.append(f"\n🔥 <b>Активні калорії:</b> {int(float(kcal))} ккал")
        except: pass

    dist = data.get("walking_running_distance") or data.get("distance")
    if dist:
        try:
            lines.append(f"\n🗺 <b>Дистанція:</b> {float(dist):.2f} км")
        except: pass

    if len(lines) <= 2:
        lines.append("\n📊 Отримані метрики:")
        for k, v in list(data.items())[:10]:
            if v is not None:
                lines.append(f"  {k}: {v}")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


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

        # Route: /upload або якщо тіло схоже на ZIP/multipart/octet
        if path == "/upload" or "multipart" in content_type or "octet" in content_type or "zip" in content_type:
            self._handle_zip_upload()
        else:
            self._handle_widgets_json()

    def _handle_zip_upload(self):
        """Приймає ZIP від Health Auto Export і надсилає розширений звіт."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b""

            print(f"[ZIP] Received {len(body)} bytes", flush=True)
            print(f"[ZIP] Headers: {dict(self.headers)}", flush=True)
            print(f"[ZIP] Body first 100: {body[:100]}", flush=True)

            # Якщо тіло порожнє — повідом в Telegram для діагностики
            if not body:
                send_telegram("⚠️ HAE: запит отримано але тіло порожнє. Перевір налаштування.")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Empty body")
                return

            if not body:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Empty body")
                return

            # Якщо це multipart — витягуємо ZIP файл
            content_type = self.headers.get("Content-Type", "")
            if "multipart" in content_type:
                zip_bytes = extract_zip_from_multipart(body, content_type)
            else:
                zip_bytes = body

            if not zip_bytes or not zip_bytes.startswith(b"PK"):
                print(f"[ZIP] Not a valid ZIP: {zip_bytes[:20]}", flush=True)
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Not a ZIP file")
                return

            stats = analyze_hae_zip(zip_bytes)

            if not stats:
                send_telegram("⚠️ Health ZIP отримано, але не вдалось розпарсити CSV.")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "parsed": False}).encode())
                return

            report = format_hae_report(stats)
            send_telegram(report)
            print(f"[ZIP] Report sent. Stats: {stats}", flush=True)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "stats": stats}).encode())

        except Exception as e:
            import traceback
            print(f"[ZIP] Error: {e}\n{traceback.format_exc()}", flush=True)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _handle_widgets_json(self):
        """Приймає JSON від Healthy Widgets (щоденний)."""
        try:
            local = datetime.now(timezone.utc) + timedelta(hours=2)
            h, m = local.hour, local.minute
            in_window = (h == 18 and m >= 45) or (h == 19 and m <= 15)

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

            print(f"[Widgets] {local.strftime('%H:%M')} | {length}b | window={in_window}", flush=True)
            print(f"[Widgets] Raw body: {body[:500]}", flush=True)

            if not in_window:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "skipped": "outside window"}).encode())
                return

            data = {}
            if body:
                try:
                    parsed = json.loads(body.decode("utf-8", errors="replace"))
                    if "data" in parsed:
                        inner = parsed["data"]
                        if isinstance(inner, str):
                            try:
                                data = json.loads(inner)
                            except:
                                import ast
                                data = ast.literal_eval(inner)
                        elif isinstance(inner, dict):
                            data = inner
                        else:
                            data = parsed
                    else:
                        data = parsed
                except Exception as e:
                    print(f"[Widgets] JSON error: {e}", flush=True)
                    from urllib.parse import parse_qs
                    parsed_form = parse_qs(body.decode("utf-8", errors="replace"))
                    raw = parsed_form.get("data", [None])[0]
                    if raw:
                        try:
                            import ast
                            data = ast.literal_eval(raw)
                        except:
                            data = {k: v[0] for k, v in parsed_form.items()}
                    else:
                        data = {k: v[0] for k, v in parsed_form.items()}

            print(f"[Widgets] Keys: {list(data.keys())[:10]}", flush=True)
            report = format_health_report(data)
            send_telegram(report)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())

        except Exception as e:
            print(f"[Widgets] Error: {e}", flush=True)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())


def extract_zip_from_multipart(body, content_type):
    """Витягує ZIP файл з multipart/form-data."""
    import re
    boundary_match = re.search(r'boundary=([^\s;]+)', content_type)
    if not boundary_match:
        return body

    boundary = boundary_match.group(1).encode()
    parts = body.split(b"--" + boundary)

    for part in parts:
        if b"Content-Disposition" in part and (b".zip" in part.lower() or b"application/zip" in part.lower() or b"application/octet" in part.lower()):
            # Знайти кінець заголовків (\r\n\r\n)
            header_end = part.find(b"\r\n\r\n")
            if header_end != -1:
                return part[header_end + 4:].rstrip(b"\r\n")

    # Якщо не знайшли по типу — беремо перший бінарний part
    for part in parts:
        if b"Content-Disposition" in part:
            header_end = part.find(b"\r\n\r\n")
            if header_end != -1:
                data = part[header_end + 4:].rstrip(b"\r\n")
                if data.startswith(b"PK"):
                    return data

    return body


def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"=== Health Webhook Server on port {port} ===", flush=True)
    print("Endpoints: GET / | POST / (Widgets) | POST /upload (HAE ZIP)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
