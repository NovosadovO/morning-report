#!/usr/bin/env python3
"""
Financial Goal Tracker 💎
Мета: Фінансова незалежність Олега
- Портфель → мета в USD
- Місячний прогрес + прогноз
- Gemini-мотивація
"""

import os
import json
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

try:
    from portfolio import load_positions, get_total_portfolio_usd
    from storage import load as storage_load
except ImportError:
    load_positions = get_total_portfolio_usd = storage_load = None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ─── КОНФІГ ──────────────────────────────────────────────────────────────────
# Олег: мета — фінансова незалежність
# Який розмір портфеля дає достатній пасивний дохід?
# Припущення: 4% правило (4% від портфеля в рік)
# Олег витрачає: ~$2000/місяць = $24k/рік
# Потребує: $24k / 0.04 = $600k портфеля

FINANCIAL_GOALS = {
    "short_term": {
        "name": "Мініміна подушка безпеки",
        "amount_usd": 10_000,
        "description": "6 місяців витрат ($2k/міс)",
        "emoji": "🟡"
    },
    "mid_term": {
        "name": "Перша мета",
        "amount_usd": 50_000,
        "description": "Початок пасивного доходу",
        "emoji": "🟠"
    },
    "long_term": {
        "name": "ФІНАНСОВА НЕЗАЛЕЖНІСТЬ 🚀",
        "amount_usd": 600_000,
        "description": "4% правило: $24k витрат/рік",
        "emoji": "🟢"
    }
}


def _gem_post(url, body, tag):
    """Простий запит до Gemini"""
    import urllib.request
    import json as _json
    
    try:
        data = _json.dumps(body).encode('utf-8')
        req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            response = _json.loads(resp.read().decode())
            try:
                text = response['candidates'][0]['content']['parts'][0].get('text', '')
                return text if text else None
            except (KeyError, IndexError):
                return None
    except Exception as e:
        print(f"⚠️ Gemini error [{tag}]: {e}")
        return None


def calculate_portfolio_progress():
    """Рахує прогрес портфеля до мети"""
    
    if not load_positions or not get_total_portfolio_usd:
        return None
    
    try:
        positions = load_positions()
        if not positions:
            return None
        
        # Витягуємо сумарну вартість всіх позицій
        total_usd = get_total_portfolio_usd(positions)
        if not total_usd:
            return None
        
        progress = {}
        
        for goal_key, goal_data in FINANCIAL_GOALS.items():
            target = goal_data["amount_usd"]
            pct = min(int(total_usd / target * 100), 100)
            progress[goal_key] = {
                "current": total_usd,
                "target": target,
                "percent": pct,
                "remaining": max(target - total_usd, 0),
                "name": goal_data["name"],
                "emoji": goal_data["emoji"]
            }
        
        return {
            "total_usd": total_usd,
            "goals": progress,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    except Exception as e:
        print(f"❌ Progress calc error: {e}")
        return None


def format_progress_report():
    """Форматує звіт про прогрес в красивий текст"""
    
    progress = calculate_portfolio_progress()
    if not progress:
        return "⚠️ Портфель недоступний"
    
    lines = []
    lines.append(f"💎 <b>ФІНАНСОВА МЕТА</b>")
    lines.append(f"Портфель: <b>${progress['total_usd']:,.0f}</b>")
    lines.append("")
    
    # Показуємо всі три мети
    for goal_key in ["short_term", "mid_term", "long_term"]:
        goal = progress["goals"][goal_key]
        pct = goal["percent"]
        
        # Прогрес-бар
        filled = int(pct / 5)  # 100% = 20 chars
        bar = "█" * filled + "░" * (20 - filled)
        
        lines.append(f"{goal['emoji']} <b>{goal['name']}</b>")
        lines.append(f"   {bar} {pct}%")
        
        if goal['remaining'] > 0:
            lines.append(f"   Залишилось: ${goal['remaining']:,.0f}")
        else:
            lines.append(f"   ✅ ДОСЯГНУТО!")
        
        lines.append("")
    
    return "\n".join(lines)


def generate_monthly_projection():
    """Генеріює прогноз на місяць вперед (нерухомий + DeFi yields)"""
    
    progress = calculate_portfolio_progress()
    if not progress:
        return None
    
    current = progress['total_usd']
    
    # Припущення:
    # - DeFi staking: 8% APY = ~0.67% на місяць
    # - Крипто growth: 2-5% на місяць (або мінус)
    # - Середньо: 1.5% на місяць
    
    monthly_growth_rate = 0.015  # 1.5%
    
    projection = {
        "current": current,
        "1_month": current * (1 + monthly_growth_rate),
        "3_months": current * ((1 + monthly_growth_rate) ** 3),
        "6_months": current * ((1 + monthly_growth_rate) ** 6),
        "12_months": current * ((1 + monthly_growth_rate) ** 12),
    }
    
    # Коли досягнеш $600k?
    target = 600_000
    if current >= target:
        months_to_goal = 0
    else:
        months_to_goal = int(__import__('math').log(target / current) / __import__('math').log(1 + monthly_growth_rate))
    
    projection["months_to_goal"] = months_to_goal
    projection["date_to_goal"] = (datetime.now(timezone.utc) + timedelta(days=months_to_goal * 30)).strftime("%Y-%m")
    
    return projection


def generate_gemini_motivation():
    """Генеріює мотивуючий AI-аналіз через Gemini"""
    
    if not GEMINI_API_KEY:
        return None
    
    progress = calculate_portfolio_progress()
    if not progress:
        return None
    
    projection = generate_monthly_projection()
    
    current = progress['total_usd']
    goal_long = 600_000
    pct_to_goal = int(current / goal_long * 100)
    
    prompt = f"""Ти — фінансовий коуч Олега. Аналізуй його прогрес до фінансової незалежності:

📊 ПОТОЧНА СИТУАЦІЯ:
- Портфель: ${current:,.0f}
- Мета: ${goal_long:,.0f}
- Прогрес: {pct_to_goal}%
- Темп: 1.5% на місяць (на основі DeFi yields + крипто growth)
- До мети: ~{projection['months_to_goal']} місяців

🎯 ДОСЯГНЕННЯ:
[Дай 1 позитивне спостереження про прогрес]

💪 РЕКОМЕНДАЦІЯ:
[1 конкретна дія для прискорення досягнення мети]

📈 МОТИВАЦІЯ:
[Вдохнови Олега, покажи скільки він вже зробив + че чекає попереду]

СТИЛЬ: Теплий, реалістичний, мотивуючий (3-4 пропозиції, емодзі)
МОВА: Українська"""

    body = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 1,
            "maxOutputTokens": 500,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    text = _gem_post(url, body, "financial_motivation")
    return text if text else "💎 Ти на правильному шляху до свободи! 🚀"


def get_financial_goal_block():
    """Повертає блок для годинного звіту"""
    
    report = format_progress_report()
    
    motivation = generate_gemini_motivation()
    
    block = f"""{report}

🤖 <b>Gemini-мотивація:</b>
{motivation}"""
    
    return block


if __name__ == "__main__":
    print(get_financial_goal_block())
