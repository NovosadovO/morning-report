#!/usr/bin/env python3
"""
weekly_coach.py — AI Тижневий Коуч (Компонент C)

Запускається щонеділі о 20:30 (UTC+2).
Збирає дані тижня: біг, вага, звички, здоров'я, крипто, настрій.
Аналізує через Gemini 2.5 Flash: паттерни, що вийшло, що ні, план на наступний тиждень.
Надсилає в Telegram: текст + графіки.
"""

import os
import json
import urllib.request
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "2100366814")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)


def _tg(method, params=None, files=None):
    """Telegram API call."""
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    if files:
        import urllib.parse
        import io
        boundary = "----FormBoundary7MA4YWxkTrZu0gW"
        body_parts = []
        for key, val in (params or {}).items():
            body_parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}".encode()
            )
        for key, (filename, data) in files.items():
            body_parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n".encode()
                + data
            )
        body_parts.append(f"--{boundary}--\r\n".encode())
        body = b"\r\n".join(body_parts)
        req = urllib.request.Request(base, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        body = json.dumps(params or {}).encode()
        req = urllib.request.Request(base, data=body,
            headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[WeeklyCoach] Telegram {method} error: {e}")
        return {}


def _send(text, parse_mode="HTML"):
    _tg("sendMessage", {"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": parse_mode})


def _send_photo(img_bytes, caption=""):
    _tg("sendPhoto", {"chat_id": TELEGRAM_CHAT, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("chart.png", img_bytes)})


# ─── ЗБІР ДАНИХ ───────────────────────────────────────────────────────────────

def _collect_week_data():
    """Збирає всі дані за останній тиждень."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))

    now = _now_local()
    week_start = now - timedelta(days=7)
    data = {"week_start": week_start.strftime("%d.%m"), "week_end": now.strftime("%d.%m")}

    # ── Strava / Біг ──
    try:
        from strava import get_week_stats, get_last_activity
        week = get_week_stats()
        last = get_last_activity()
        data["strava"] = {
            "runs": week.get("runs", 0) if week else 0,
            "km": week.get("km", 0) if week else 0,
            "duration_min": week.get("duration_min", 0) if week else 0,
            "last_activity": last,
        }
    except Exception as e:
        print(f"[WeeklyCoach] strava error: {e}")
        data["strava"] = {"runs": 0, "km": 0, "duration_min": 0}

    # ── Вага ──
    try:
        from storage import load
        weight_data = load("weight.json", default={})
        week_weights = {}
        for date_str, v in weight_data.items():
            try:
                dt = datetime.fromisoformat(date_str)
                if dt >= week_start.replace(tzinfo=None):
                    week_weights[date_str] = v.get("weight", v) if isinstance(v, dict) else v
            except Exception:
                pass
        data["weight"] = {
            "entries": week_weights,
            "count": len(week_weights),
            "avg": round(sum(week_weights.values()) / len(week_weights), 1) if week_weights else None,
            "last": list(week_weights.values())[-1] if week_weights else None,
        }
    except Exception as e:
        print(f"[WeeklyCoach] weight error: {e}")
        data["weight"] = {"entries": {}, "count": 0, "avg": None}

    # ── Звички ──
    try:
        from storage import load_habits
        from habits import HABITS
        habits_db = load_habits()
        week_habits = {}
        for date_str, day_data in habits_db.items():
            try:
                dt = datetime.fromisoformat(date_str)
                if dt >= week_start.replace(tzinfo=None):
                    week_habits[date_str] = day_data
            except Exception:
                pass

        habit_stats = {}
        for h in HABITS:
            done_count = sum(1 for d in week_habits.values() if d.get(h["id"]) is True)
            habit_stats[h["name"]] = {"done": done_count, "days": len(week_habits)}

        data["habits"] = {
            "days_tracked": len(week_habits),
            "stats": habit_stats,
        }
    except Exception as e:
        print(f"[WeeklyCoach] habits error: {e}")
        data["habits"] = {"days_tracked": 0, "stats": {}}

    # ── Health / Кроки / Сон ──
    try:
        from storage import load_health
        health_db = load_health()
        week_health = {}
        for date_str, h in health_db.items():
            try:
                dt = datetime.fromisoformat(date_str)
                if dt >= week_start.replace(tzinfo=None):
                    week_health[date_str] = h
            except Exception:
                pass

        all_steps = [h.get("steps", 0) for h in week_health.values() if h.get("steps")]
        all_sleep = [h.get("sleep_hours", 0) for h in week_health.values() if h.get("sleep_hours")]
        all_hr = [h.get("heart_rate", 0) for h in week_health.values() if h.get("heart_rate")]
        all_score = [h.get("health_score", 0) for h in week_health.values() if h.get("health_score")]

        data["health"] = {
            "days": len(week_health),
            "avg_steps": round(sum(all_steps) / len(all_steps)) if all_steps else None,
            "avg_sleep": round(sum(all_sleep) / len(all_sleep), 1) if all_sleep else None,
            "avg_hr": round(sum(all_hr) / len(all_hr)) if all_hr else None,
            "avg_score": round(sum(all_score) / len(all_score), 1) if all_score else None,
        }
    except Exception as e:
        print(f"[WeeklyCoach] health error: {e}")
        data["health"] = {}

    # ── Настрій ──
    try:
        from monitor import load_json_file, MOOD_FILE
        mood_db = load_json_file(MOOD_FILE, default={})
        week_mood = {}
        for date_str, score in mood_db.items():
            try:
                dt = datetime.fromisoformat(date_str)
                if dt >= week_start.replace(tzinfo=None):
                    week_mood[date_str] = score
            except Exception:
                pass
        mood_values = list(week_mood.values())
        data["mood"] = {
            "entries": week_mood,
            "avg": round(sum(mood_values) / len(mood_values), 1) if mood_values else None,
        }
    except Exception as e:
        print(f"[WeeklyCoach] mood error: {e}")
        data["mood"] = {"avg": None}

    # ── Крипто P&L ──
    try:
        ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
        url = (f"https://api.coingecko.com/api/v3/coins/markets"
               f"?vs_currency=usd&ids={ids}&price_change_percentage=7d,24h")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            crypto_raw = json.loads(r.read())
        crypto_data = []
        for c in crypto_raw:
            ch7 = c.get("price_change_percentage_7d_in_currency") or 0
            ch24 = c.get("price_change_percentage_24h") or 0
            crypto_data.append({
                "symbol": c["symbol"].upper(),
                "price": c["current_price"],
                "change_7d": round(ch7, 1),
                "change_24h": round(ch24, 1),
            })
        data["crypto"] = crypto_data
    except Exception as e:
        print(f"[WeeklyCoach] crypto error: {e}")
        data["crypto"] = []

    return data


# ─── GEMINI АНАЛІЗ ────────────────────────────────────────────────────────────

def _build_coach_prompt(week_data: dict) -> str:
    """Будує промпт для AI-коуча."""
    now = _now_local()
    weekday_ua = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"][now.weekday()]

    lines = [
        "Ти персональний AI-коуч Олега Новосадова. Зараз неділя вечір — час тижневого підсумку.",
        "",
        "ТВОЯ ЗАДАЧА:",
        "1. Проаналізуй тиждень по всіх напрямках",
        "2. Знайди паттерни (що вийшло, що ні, чому)",
        "3. Дай конкретний план на наступний тиждень",
        "4. Підтримай і мотивуй — ти друг, не суддя",
        "",
        "ФОРМАТ ВІДПОВІДІ (Telegram HTML):",
        "• Стислий summary тижня (3-4 речення)",
        "• 💪 Що вийшло добре",
        "• 🔄 Що можна покращити (конкретно, 1-2 пункти)",
        "• 📊 Паттерн тижня (цікаве спостереження)",
        "• 🎯 3 конкретні цілі на наступний тиждень",
        "• 🔥 Одне мотиваційне речення",
        "",
        "Мова: ТІЛЬКИ українська. Стиль: як хороший тренер-друг. Без зайвих слів.",
        "",
        f"═══ ДАНІ ТИЖНЯ ({week_data['week_start']}–{week_data['week_end']}) ═══",
    ]

    # Strava
    strava = week_data.get("strava", {})
    lines += [
        "",
        "🏃 БІГ:",
        f"  Пробіжок: {strava.get('runs', 0)}",
        f"  Дистанція: {strava.get('km', 0)} км",
        f"  Час: {strava.get('duration_min', 0)} хв",
    ]

    # Вага
    weight = week_data.get("weight", {})
    lines += [
        "",
        "⚖️ ВАГА:",
        f"  Записів: {weight.get('count', 0)}",
        f"  Середня: {weight.get('avg') or 'немає даних'} кг",
        f"  Остання: {weight.get('last') or 'немає'} кг (ціль: 78 кг)",
    ]

    # Звички
    habits = week_data.get("habits", {})
    lines += ["", "💊 ЗВИЧКИ:"]
    for name, stat in habits.get("stats", {}).items():
        pct = round(stat["done"] / stat["days"] * 100) if stat["days"] > 0 else 0
        lines.append(f"  {name}: {stat['done']}/{stat['days']} днів ({pct}%)")

    # Health
    health = week_data.get("health", {})
    if health:
        lines += [
            "",
            "❤️ ЗДОРОВ'Я:",
            f"  Середні кроки: {health.get('avg_steps') or 'нема'}",
            f"  Середній сон: {health.get('avg_sleep') or 'нема'} г",
            f"  Середній ЧСС: {health.get('avg_hr') or 'нема'} bpm",
            f"  Середній health score: {health.get('avg_score') or 'нема'}/100",
        ]

    # Настрій
    mood = week_data.get("mood", {})
    if mood.get("avg"):
        mood_label = {1: "важко", 2: "нижче норми", 3: "нормально", 4: "добре", 5: "чудово"}
        avg = mood["avg"]
        label = mood_label.get(round(avg), str(avg))
        lines += ["", f"😊 НАСТРІЙ: середній {avg}/5 ({label})"]
        if mood.get("entries"):
            for d, s in sorted(mood["entries"].items()):
                lines.append(f"  {d[5:]}: {'⭐' * s}")

    # Крипто
    crypto = week_data.get("crypto", [])
    if crypto:
        lines += ["", "💹 КРИПТО (зміна за 7 днів):"]
        for c in crypto:
            sign = "+" if c["change_7d"] > 0 else ""
            lines.append(f"  {c['symbol']}: ${c['price']:,.0f} ({sign}{c['change_7d']}%)")

    lines += ["", "Дай глибокий тижневий аналіз і конкретний план. Будь конструктивним і підтримуючим."]
    return "\n".join(lines)


def _ask_gemini_coach(prompt: str) -> str:
    """Делегує до monitor._gem_post — СПІЛЬНИЙ rate-limiter на весь процес."""
    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 1200,
            "temperature": 0.75,
            "thinkingConfig": {"thinkingBudget": 0},
        }
    }).encode()
    try:
        from monitor import _gem_post
        resp = _gem_post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            payload, timeout=45, tag="weekly_coach", max_retries=3
        )
        if isinstance(resp, dict) and resp.get("candidates"):
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"].strip()
        return "⚠️ AI помилка: порожня відповідь"
    except Exception as e:
        return f"⚠️ AI помилка: {e}"


# ─── ГРАФІКИ ──────────────────────────────────────────────────────────────────

def _generate_charts(week_data: dict) -> list:
    """Генерує графіки для тижневого звіту. Повертає список (caption, bytes)."""
    charts = []
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from charts import plot_weekly_dashboard
        img = plot_weekly_dashboard()
        if img:
            charts.append(("📊 Тижневий дашборд", img))
    except Exception as e:
        print(f"[WeeklyCoach] weekly dashboard chart error: {e}")

    # Графік ваги
    try:
        from charts import plot_weight_trend
        img = plot_weight_trend(days=30)
        if img:
            charts.append(("⚖️ Динаміка ваги (30 днів)", img))
    except Exception as e:
        print(f"[WeeklyCoach] weight chart error: {e}")

    return charts


# ─── ГОЛОВНА ФУНКЦІЯ ──────────────────────────────────────────────────────────

def run_weekly_coach():
    """
    Запускає тижневий AI-аналіз.
    Викликається щонеділі о 20:30 UTC+2.
    """
    print("[WeeklyCoach] Starting weekly analysis...", flush=True)

    # Перевіряємо що не надсилали цього тижня
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import load, save
        coach_state = load("weekly_coach_state.json", default={})
        now = _now_local()
        week_key = now.strftime("%Y-W%W")
        if coach_state.get("last_sent_week") == week_key:
            print(f"[WeeklyCoach] Already sent this week ({week_key}), skipping", flush=True)
            return
    except Exception as e:
        print(f"[WeeklyCoach] state check error: {e}")
        coach_state = {}
        week_key = _now_local().strftime("%Y-W%W")

    try:
        # 1. Збираємо дані
        _send("🔄 Збираю дані тижня для аналізу...")
        week_data = _collect_week_data()
        print(f"[WeeklyCoach] Data collected: strava={week_data.get('strava',{}).get('km',0)}km", flush=True)

        # 2. AI аналіз
        prompt = _build_coach_prompt(week_data)
        analysis = _ask_gemini_coach(prompt)
        print(f"[WeeklyCoach] AI analysis done ({len(analysis)} chars)", flush=True)

        # 3. Надсилаємо аналіз
        now = _now_local()
        header = (
            f"🤖 <b>ТИЖНЕВИЙ ЗВІТ AI-КОУЧА</b>\n"
            f"📅 {week_data['week_start']} – {week_data['week_end']}\n"
            f"{'─' * 30}\n\n"
        )
        _send(header + analysis)

        # 4. Графіки
        charts = _generate_charts(week_data)
        for caption, img_bytes in charts:
            try:
                _send_photo(img_bytes, caption)
            except Exception as e:
                print(f"[WeeklyCoach] send chart error: {e}")

        # 5. Зберігаємо стан
        try:
            from storage import save
            coach_state["last_sent_week"] = week_key
            coach_state["last_sent_ts"] = now.isoformat()
            save("weekly_coach_state.json", coach_state)
        except Exception as e:
            print(f"[WeeklyCoach] save state error: {e}")

        print("[WeeklyCoach] Done!", flush=True)

    except Exception as e:
        print(f"[WeeklyCoach] ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        _send(f"⚠️ Тижневий звіт: помилка — {e}")


if __name__ == "__main__":
    run_weekly_coach()
