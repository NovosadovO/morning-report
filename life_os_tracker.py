"""
LIFE OS TRACKER — повна система управління життям Олега
Стежить за 10 сферами і генерує персоналізовані рекомендації
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

TZ_OFFSET = 2  # UTC+2

def _get_hour():
    """Поточна година UTC+2"""
    return (datetime.utcnow().hour + TZ_OFFSET) % 24

def load_json(filename: str, default=None):
    """Загрузити JSON"""
    try:
        with open(f"data/{filename}", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default or {}

def save_json(filename: str, data: dict):
    """Зберегти JSON"""
    os.makedirs("data", exist_ok=True)
    try:
        with open(f"data/{filename}", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[LIFE_OS] Save error: {e}")

# ============= 10 СФЕР ЖИТТЯ =============

class LifeOSTracker:
    """Стежить за 10 сферами життя"""
    
    def __init__(self):
        self.health = load_json("daily_health.json", {})
        self.finance = load_json("crypto_tracker.json", {})
        self.work = load_json("work_schedule.json", {})
        self.learning = load_json("learning_progress.json", {})
        self.relations = load_json("relations.json", {})
        self.goals = load_json("goals.json", {})
        self.astro = load_json("astro_analysis.json", {})
        self.calendar = load_json("monitor_calendar_reminded.json", {})
        self.home = load_json("shopping_list.json", {})
        self.energy = load_json("energy_log.json", {})
    
    def check_health(self) -> Tuple[str, bool]:
        """
        🏥 ЗДОРОВ'Я: вага, біг, сон, кроки, пульс, стрес
        Повертає (message, should_notify)
        """
        today = datetime.now().strftime("%Y-%m-%d")
        today_health = self.health.get(today, {})
        
        weight = today_health.get("weight")
        sleep = today_health.get("sleep_hours")
        runs = today_health.get("runs", 0)
        steps = today_health.get("steps", 0)
        
        alerts = []
        
        # Чек сну
        if sleep and sleep < 6:
            alerts.append(f"⚠️ Мало сну: {sleep}h (мета 7-8h)")
        elif sleep and sleep > 9:
            alerts.append(f"⚠️ Забагато сну: {sleep}h (може бути втома)")
        
        # Чек кроків
        if steps and steps < 5000:
            alerts.append(f"📍 Мало кроків: {steps} (мета 8000+)")
        
        # Чек вилік
        if weight and weight > 84:
            alerts.append(f"⚠️ Вага: {weight}kg (мета <82kg)")
        
        # Чек біу
        if runs == 0 and _get_hour() >= 18:
            alerts.append("🏃 Беж запланований? Поки не було сьогодні")
        
        if alerts:
            return (" / ".join(alerts), True)
        return ("✅ Здоров'я в нормі", False)
    
    def check_finance(self) -> Tuple[str, bool]:
        """
        💹 ФІНАНСИ: крипто (BTC/ETH/AVAX/ONDO), портфель, витрати
        """
        crypto = self.finance.get("prices", {})
        
        alerts = []
        
        # Чек крипто
        for coin in ["BTC", "ETH", "AVAX", "ONDO"]:
            change = crypto.get(f"{coin}_24h_change", 0)
            if abs(change) > 5:
                direction = "📈" if change > 0 else "📉"
                alerts.append(f"{direction} {coin}: {change:+.1f}%")
        
        if alerts:
            return (" | ".join(alerts), True)
        return ("💰 Крипто стабільна", False)
    
    def check_work(self) -> Tuple[str, bool]:
        """
        💼 РОБОТА: змішення (ранна/нічна), дедлайни, продуктивність
        """
        today = datetime.now().strftime("%Y-%m-%d")
        schedule = self.work.get(today, {})
        shift = schedule.get("shift")  # "early" або "night"
        hour = _get_hour()
        
        alerts = []
        
        if shift == "early" and hour >= 17:
            alerts.append("🌅 Рання зміна закінчується о 18:00")
        elif shift == "night" and hour >= 4:
            alerts.append("🌙 Нічна зміна близько до кінця (06:00)")
        
        if alerts:
            return (" / ".join(alerts), True)
        return ("✅ Робота за графіком", False)
    
    def check_learning(self) -> Tuple[str, bool]:
        """
        📚 НАВЧАННЯ: курси, книги, нові скілли (інвестиції)
        """
        learning = self.learning.get("courses", [])
        
        alerts = []
        
        # Якщо сьогодні мало часу вивчав
        today = datetime.now().strftime("%Y-%m-%d")
        if len(learning) > 0 and _get_hour() in [7, 12, 18]:  # Пік часу для навчання
            alerts.append("📖 Час для навчання про інвестиції?")
        
        return (" / ".join(alerts) if alerts else "✅ Навчання на плані", len(alerts) > 0)
    
    def check_relations(self) -> Tuple[str, bool]:
        """
        🤝 СТОСУНКИ: VIP контакти (boss, investors, близькі)
        """
        relations = self.relations.get("vip_contacts", {})
        
        alerts = []
        for contact, last_contact_date in relations.items():
            if not last_contact_date:
                alerts.append(f"📞 Давно не писав {contact}")
        
        return (" / ".join(alerts) if alerts else "✅ Контакти в курсі", len(alerts) > 0)
    
    def check_goals(self) -> Tuple[str, bool]:
        """
        🎯 ЦІЛІ: фінансова незалежність, схуднення, нова робота
        """
        goals = self.goals.get("active", [])
        
        alerts = []
        
        # Чек прогресу
        for goal in goals:
            progress = goal.get("progress", 0)
            target = goal.get("target", 100)
            if progress < target * 0.3:  # Менше 30%
                alerts.append(f"⚠️ {goal.get('name')}: {progress}/{target}")
        
        return (" / ".join(alerts) if alerts else "✅ Цілі на плані", len(alerts) > 0)
    
    def check_astro(self) -> Tuple[str, bool]:
        """
        🌙 АСТРО & ЕНЕРГІЯ: натальна карта, транзити, період
        """
        astro = self.astro.get("today", {})
        
        aspects = astro.get("major_aspects", [])
        
        alerts = []
        if aspects:
            alerts.append(f"🌙 {', '.join(aspects[:2])}")
        
        return (" / ".join(alerts) if alerts else "✨ Енергія в нормі", len(alerts) > 0)
    
    def check_time_schedule(self) -> Tuple[str, bool]:
        """
        ⏰ ЧАС & РОЗКЛАД: оптимальні вікна, смени, найбільш продуктивні години
        """
        hour = _get_hour()
        
        optimal_windows = {
            "6-9": "🌅 Ранок — навчання, планування",
            "10-12": "💼 Продуктивність на максимумі",
            "12-14": "🍴 Обід, релакс",
            "15-17": "⚡ Другий пік енергії",
            "18-19": "🏃 Спорт, свіжість",
            "20-22": "🌙 Релаксація, анаміз дня",
            "23-5": "😴 Сон (дно енергії)",
        }
        
        for window, desc in optimal_windows.items():
            start = int(window.split("-")[0])
            if hour == start:
                return (f"{desc}", True)
        
        return ("✅ Час на добротам", False)
    
    def check_home_chores(self) -> Tuple[str, bool]:
        """
        🏠 ДІМ & СПРАВИ: покупки, рослини, чистота
        """
        home = self.home.get("tasks", [])
        
        alerts = []
        
        for task in home:
            if task.get("overdue"):
                alerts.append(f"⚠️ {task.get('name')} прострочено")
        
        return (" / ".join(alerts) if alerts else "✅ Дім в порядку", len(alerts) > 0)
    
    def check_energy_motivation(self) -> Tuple[str, bool]:
        """
        ⚡ ЕНЕРГІЯ & МОТИВАЦІЯ: масштаб енергії, баланс, мотивація
        """
        energy_log = self.energy.get("today", {})
        energy_level = energy_log.get("level", 5)  # 1-10
        mood = energy_log.get("mood")
        
        alerts = []
        
        if energy_level < 3:
            alerts.append("⚠️ Енергія низька — потребує відновлення")
        elif energy_level >= 8:
            alerts.append("🔥 Енергія на максимумі — час для великих справ!")
        
        if mood:
            alerts.append(f"💭 Настрій: {mood}")
        
        return (" / ".join(alerts) if alerts else "✅ Енергія в нормі", len(alerts) > 0)
    
    def get_life_status(self) -> Dict[str, Tuple[str, bool]]:
        """Повертає статус всіх 10 сфер"""
        return {
            "health": self.check_health(),
            "finance": self.check_finance(),
            "work": self.check_work(),
            "learning": self.check_learning(),
            "relations": self.check_relations(),
            "goals": self.check_goals(),
            "astro": self.check_astro(),
            "time": self.check_time_schedule(),
            "home": self.check_home_chores(),
            "energy": self.check_energy_motivation(),
        }
    
    def get_critical_alerts(self) -> List[Tuple[str, str]]:
        """Повертає тільки критичні, потребуючі дії"""
        status = self.get_life_status()
        critical = [(sphere, msg) for sphere, (msg, should_alert) in status.items() if should_alert]
        return critical

# Test
if __name__ == "__main__":
    tracker = LifeOSTracker()
    status = tracker.get_life_status()
    
    print("\n=== LIFE OS STATUS ===\n")
    emojis = {
        "health": "🏥",
        "finance": "💹",
        "work": "💼",
        "learning": "📚",
        "relations": "🤝",
        "goals": "🎯",
        "astro": "🌙",
        "time": "⏰",
        "home": "🏠",
        "energy": "⚡",
    }
    
    for sphere, (msg, alert) in status.items():
        emoji = emojis.get(sphere, "📌")
        marker = "⚠️" if alert else "✅"
        print(f"{marker} {emoji} {sphere.upper():10} — {msg}")
    
    print("\n=== CRITICAL ALERTS ===\n")
    critical = tracker.get_critical_alerts()
    if critical:
        for sphere, msg in critical:
            print(f"🚨 {sphere.upper()}: {msg}")
    else:
        print("✅ No critical alerts")
