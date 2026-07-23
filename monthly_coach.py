#!/usr/bin/env python3
"""
monthly_coach.py — AI Місячний Коуч

Запускається 1-го числа кожного місяця о 20:30 (UTC+2), аналізує ПОПЕРЕДНІЙ місяць.
Збирає дані місяця: біг, вага, звички, здоров'я, крипто, настрій, пошта, астро.
Аналізує через Gemini: великі тренди, паттерни, план на новий місяць.
Надсилає в Telegram: текст + місячний графік.
"""

import os
import json
import base64 as _b64
import urllib.request
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "2100366814")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)


def _tg(method, params=None, files=None):
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    if files:
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
        print(f"[MonthlyCoach] Telegram {method} error: {e}")
        return {}


def _send(text, parse_mode="HTML"):
    _tg("sendMessage", {"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": parse_mode})


def _send_photo(img_bytes, caption=""):
    _tg("sendPhoto", {"chat_id": TELEGRAM_CHAT, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("chart.png", img_bytes)})


# ─── ЗБІР ДАНИХ ───────────────────────────────────────────────────────────────

def _collect_month_data():
    """Збирає всі дані за останні 30 днів."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))

    now = _now_local()
    month_start = now - timedelta(days=30)
    data = {"month_start": month_start.strftime("%d.%m"), "month_end": now.strftime("%d.%m"),
            "month_name": now.strftime("%B %Y")}

    # ── Strava / Біг ──
    try:
        from strava import get_month_stats, get_last_activity
        month = get_month_stats()
        last = get_last_activity()
        data["strava"] = {
            "runs": month.get("runs", 0) if month else 0,
            "km": month.get("km", 0) if month else 0,
            "last_activity": last,
        }
    except Exception as e:
        print(f"[MonthlyCoach] strava error: {e}")
        data["strava"] = {"runs": 0, "km": 0}

    # ── Вага ──
    try:
        from storage import load
        weight_data = load("weight.json", default={})
        month_weights = {}
        for date_str, v in weight_data.items():
            try:
                dt = datetime.fromisoformat(date_str)
                if dt >= month_start.replace(tzinfo=None):
                    month_weights[date_str] = v.get("weight", v) if isinstance(v, dict) else v
            except Exception:
                pass
        sorted_keys = sorted(month_weights.keys())
        data["weight"] = {
            "count": len(month_weights),
            "start": month_weights[sorted_keys[0]] if sorted_keys else None,
            "end": month_weights[sorted_keys[-1]] if sorted_keys else None,
        }
        if data["weight"]["start"] is not None and data["weight"]["end"] is not None:
            data["weight"]["diff"] = round(data["weight"]["end"] - data["weight"]["start"], 1)
    except Exception as e:
        print(f"[MonthlyCoach] weight error: {e}")
        data["weight"] = {"count": 0}

    # ── Звички ──
    try:
        from storage import load_habits
        from habits import HABITS
        habits_db = load_habits()
        month_habits = {}
        for date_str, day_data in habits_db.items():
            try:
                dt = datetime.fromisoformat(date_str)
                if dt >= month_start.replace(tzinfo=None):
                    month_habits[date_str] = day_data
            except Exception:
                pass
        habit_stats = {}
        for h in HABITS:
            done_count = sum(1 for d in month_habits.values() if d.get(h["id"]) is True)
            habit_stats[h["name"]] = {"done": done_count, "days": len(month_habits)}
        data["habits"] = {"days_tracked": len(month_habits), "stats": habit_stats}
    except Exception as e:
        print(f"[MonthlyCoach] habits error: {e}")
        data["habits"] = {"days_tracked": 0, "stats": {}}

    # ── Health ──
    try:
        from storage import load_health
        health_db = load_health()
        month_health = {}
        for date_str, h in health_db.items():
            try:
                dt = datetime.fromisoformat(date_str)
                if dt >= month_start.replace(tzinfo=None):
                    month_health[date_str] = h
            except Exception:
                pass
        all_steps = [h.get("steps", 0) for h in month_health.values() if h.get("steps")]
        all_sleep = [h.get("sleep_hours", 0) for h in month_health.values() if h.get("sleep_hours")]
        data["health"] = {
            "days": len(month_health),
            "avg_steps": round(sum(all_steps) / len(all_steps)) if all_steps else None,
            "avg_sleep": round(sum(all_sleep) / len(all_sleep), 1) if all_sleep else None,
        }
    except Exception as e:
        print(f"[MonthlyCoach] health error: {e}")
        data["health"] = {}

    # ── Крипто ──
    try:
        ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
        url = (f"https://api.coingecko.com/api/v3/coins/markets"
               f"?vs_currency=usd&ids={ids}&price_change_percentage=30d,7d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            crypto_raw = json.loads(r.read())
        crypto_data = []
        for c in crypto_raw:
            ch30 = c.get("price_change_percentage_30d_in_currency") or 0
            crypto_data.append({
                "symbol": c["symbol"].upper(),
                "price": c["current_price"],
                "change_30d": round(ch30, 1),
            })
        data["crypto"] = crypto_data
    except Exception as e:
        print(f"[MonthlyCoach] crypto error: {e}")
        data["crypto"] = []

    # ── Пошта ──
    try:
        gh_url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/important_emails.json"
        gh_headers = {"Authorization": f"token {os.environ.get('GITHUB_TOKEN', '')}", "User-Agent": "monthly_coach"}
        req = urllib.request.Request(gh_url, headers=gh_headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            gh_data = json.loads(r.read())
        important = json.loads(_b64.b64decode(gh_data["content"]).decode())
        pending = [e for e in important if not e.get("replied")]
        data["emails"] = {"important_total": len(important), "pending": len(pending)}
    except Exception as e:
        print(f"[MonthlyCoach] emails error: {e}")
        data["emails"] = {"important_total": 0, "pending": 0}

    # ── Астро ──
    try:
        from astro import get_natal_transits_short
        data["astro_text"] = get_natal_transits_short(max_aspects=5) or ""
    except Exception as e:
        print(f"[MonthlyCoach] astro error: {e}")
        data["astro_text"] = ""

    return data


# ─── GEMINI АНАЛІЗ ────────────────────────────────────────────────────────────

def _build_coach_prompt(m: dict) -> str:
    lines = [
        "Ти персональний AI-коуч Олега Новосадова. Сьогодні 1-е число місяця — час МІСЯЧНОГО підсумку "
        "(значно ширший погляд, ніж тижневий — великі тренди, а не деталі).",
        "",
        "ТВОЯ ЗАДАЧА:",
        "1. Знайди ВЕЛИКІ тренди місяця (не деталі окремих днів)",
        "2. Порівняй з ціллю: фінансова незалежність (крипто-інвестиції) + схуднення до 78 кг",
        "3. Дай 3-4 стратегічні висновки і план на новий місяць",
        "4. Підтримай — це погляд на довгу дистанцію, не критика",
        "",
        "ФОРМАТ (Telegram HTML):",
        "• 📈 Великий тренд місяця (2-3 речення)",
        "• 💪 Що зростає/покращується",
        "• ⚠️ Що потребує уваги на новий місяць",
        "• 🎯 3-4 стратегічні цілі на наступний місяць",
        "• 🔥 Одне сильне мотиваційне речення",
        "",
        "Мова: ТІЛЬКИ українська. Стиль: стратег-коуч на довгу дистанцію.",
        "",
        f"═══ ДАНІ МІСЯЦЯ ({m['month_start']}–{m['month_end']}) ═══",
    ]

    strava = m.get("strava", {})
    lines += ["", "🏃 БІГ ЗА МІСЯЦЬ:", f"  Пробіжок: {strava.get('runs', 0)}", f"  Дистанція: {strava.get('km', 0)} км"]

    weight = m.get("weight", {})
    if weight.get("count"):
        lines += ["", "⚖️ ВАГА ЗА МІСЯЦЬ:",
                   f"  На початку: {weight.get('start')} кг → в кінці: {weight.get('end')} кг",
                   f"  Зміна: {weight.get('diff', 0):+.1f} кг (ціль: 78 кг)"]

    habits = m.get("habits", {})
    if habits.get("stats"):
        lines += ["", "💊 ЗВИЧКИ ЗА МІСЯЦЬ:"]
        for name, stat in habits["stats"].items():
            pct = round(stat["done"] / stat["days"] * 100) if stat["days"] > 0 else 0
            lines.append(f"  {name}: {stat['done']}/{stat['days']} днів ({pct}%)")

    health = m.get("health", {})
    if health:
        lines += ["", "❤️ ЗДОРОВ'Я ЗА МІСЯЦЬ:",
                   f"  Середні кроки: {health.get('avg_steps') or 'нема'}",
                   f"  Середній сон: {health.get('avg_sleep') or 'нема'} г"]

    crypto = m.get("crypto", [])
    if crypto:
        lines += ["", "💹 КРИПТО (зміна за 30 днів):"]
        for c in crypto:
            sign = "+" if c["change_30d"] > 0 else ""
            lines.append(f"  {c['symbol']}: ${c['price']:,.0f} ({sign}{c['change_30d']}%)")

    emails = m.get("emails", {})
    if emails.get("important_total"):
        lines += ["", "📧 ПОШТА:", f"  Важливих листів на контролі: {emails['important_total']}",
                   f"  Досі без відповіді: {emails.get('pending', 0)}"]

    astro_text = m.get("astro_text", "")
    if astro_text:
        lines += ["", "🔮 АСТРО (домінуючі транзити зараз):", astro_text[:700]]

    lines += ["", "Дай стратегічний місячний аналіз з фокусом на великі тренди, врахуй пошту і астро. Будь конструктивним."]
    return "\n".join(lines)


def _ask_gemini_coach(prompt: str) -> str:
    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 1400,
            "temperature": 0.75,
            "thinkingConfig": {"thinkingBudget": 0},
        }
    }).encode()
    try:
        from monitor import _gem_post
        resp = _gem_post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            payload, timeout=45, tag="monthly_coach", max_retries=3
        )
        if isinstance(resp, dict) and resp.get("candidates"):
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"].strip()
        return "⚠️ AI помилка: порожня відповідь"
    except Exception as e:
        return f"⚠️ AI помилка: {e}"


def _generate_charts():
    charts = []
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from charts import plot_monthly_dashboard, plot_mood_energy, plot_goals_progress
        img = plot_monthly_dashboard()
        if img:
            charts.append(("📊 Місячний дашборд", img))
    except Exception as e:
        print(f"[MonthlyCoach] monthly dashboard chart error: {e}")

    try:
        from charts import plot_mood_energy
        img = plot_mood_energy(days=30)
        if img:
            charts.append(("😊 Настрій/енергія за місяць", img))
    except Exception as e:
        print(f"[MonthlyCoach] mood chart error: {e}")

    try:
        from charts import plot_goals_progress
        img = plot_goals_progress(days=90)
        if img:
            charts.append(("🎯 Прогрес до цілі (вага)", img))
    except Exception as e:
        print(f"[MonthlyCoach] goals chart error: {e}")

    return charts


def run_monthly_coach():
    """Запускає місячний AI-аналіз. Викликається 1-го числа кожного місяця о 20:30 UTC+2."""
    print("[MonthlyCoach] Starting monthly analysis...", flush=True)

    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import load, save
        coach_state = load("monthly_coach_state.json", default={})
        now = _now_local()
        month_key = now.strftime("%Y-%m")
        if coach_state.get("last_sent_month") == month_key:
            print(f"[MonthlyCoach] Already sent this month ({month_key}), skipping", flush=True)
            return
    except Exception as e:
        print(f"[MonthlyCoach] state check error: {e}")
        coach_state = {}
        month_key = _now_local().strftime("%Y-%m")

    try:
        _send("🔄 Збираю дані місяця для стратегічного аналізу...")
        month_data = _collect_month_data()
        print(f"[MonthlyCoach] Data collected: strava={month_data.get('strava',{}).get('km',0)}km", flush=True)

        prompt = _build_coach_prompt(month_data)
        analysis = _ask_gemini_coach(prompt)
        print(f"[MonthlyCoach] AI analysis done ({len(analysis)} chars)", flush=True)

        now = _now_local()
        header = (
            f"🗓 <b>МІСЯЧНИЙ ЗВІТ AI-КОУЧА</b>\n"
            f"📅 {month_data['month_start']} – {month_data['month_end']}\n"
            f"{'─' * 30}\n\n"
        )
        _send(header + analysis)

        for caption, img_bytes in _generate_charts():
            try:
                _send_photo(img_bytes, caption)
            except Exception as e:
                print(f"[MonthlyCoach] send chart error: {e}")

        # ── Активність відповідей за місяць (response_log) ──────────────────
        try:
            import response_log as _rl_mo
            summary = _rl_mo.summarize_by_category(days=30)
            if summary:
                _cat_names = {
                    "diary": "📔 Щоденник", "micro_checkin": "💭 Мікро-опитування",
                    "mood": "✨ Настрій", "habit_sleep": "🌙 Звички/сон",
                    "event_done": "✅ Виконання подій", "email_reply": "📧 Відповіді на листи",
                    "calendar_confirm": "📅 Підтвердження календаря", "calendar_reminder_confirm": "📅 Нагадування",
                    "shopping_confirm": "🛒 Покупки", "quick_reply_ok": "👍 Швидкі Ок",
                    "quick_reply_more": "❓ Розкажи більше", "quick_reply_note": "📝 Занотовано",
                    "chat": "💬 Чат з АІ",
                }
                lines = [f"• {_cat_names.get(k, k)}: {v}" for k, v in sorted(summary.items(), key=lambda x: -x[1])]
                total = sum(summary.values())
                _send(f"📊 <b>ТВОЯ АКТИВНІСТЬ ЗА МІСЯЦЬ</b>\n\nВсього {total} взаємодій з ботом:\n" + "\n".join(lines))
        except Exception as _e_rl_mo:
            print(f"[MonthlyCoach] response_log summary error: {_e_rl_mo}")

        try:
            from storage import save
            coach_state["last_sent_month"] = month_key
            coach_state["last_sent_ts"] = now.isoformat()
            save("monthly_coach_state.json", coach_state)
        except Exception as e:
            print(f"[MonthlyCoach] save state error: {e}")

        print("[MonthlyCoach] Done!", flush=True)

    except Exception as e:
        print(f"[MonthlyCoach] ERROR: {e}", flush=True)


if __name__ == "__main__":
    print("=== Monthly Coach ===")
