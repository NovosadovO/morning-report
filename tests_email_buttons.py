"""Перевірка: під AI-аналізом пошти формуються РОБОЧІ кнопки відповіді.

Раніше блок [email_ai] надсилався чистим текстом через send_telegram() —
жодної клавіші. Перевіряємо, що:
 1) функція аналізу віддає uid-и проаналізованих листів;
 2) кнопки будуються з цих uid-ів у форматі email_reply_<uid>;
 3) префікс email_reply_ реально обробляється роутером у bot.py.
"""
import re
import sys

sys.path.insert(0, "/home/user/bot")

FAIL = []


def ck(cond, name):
    print(("✅ " if cond else "❌ ") + name)
    if not cond:
        FAIL.append(name)


src = open("/home/user/bot/monitor.py").read()

print("=== 1. функція віддає uid-и ===")
ck("_get_email_ai_analysis_for_report.last_uids = _analyzed_uids" in src,
   "last_uids записується після успішного аналізу")
ck("_get_email_ai_analysis_for_report.last_uids = []" in src,
   "last_uids скидається у except (не тягне старі листи)")
ck("to_analyze.append((subject, sender, full_text, uid))" in src,
   "uid збирається разом із текстом листа")
ck("for subject, sender, full_text, _uid_a in to_analyze:" in src,
   "цикл розпаковує 4 значення (без ValueError)")

print("\n=== 2. кнопки будуються ===")
block = src[src.index("[email_ai] sending email AI"):]
block = block[:2500]
ck('f"email_reply_{_u_btn}"' in block, "callback_data = email_reply_<uid>")
ck("_send_telegram_text_with_keyboard(" in block, "надсилається З клавіатурою")
ck("inline_keyboard" in block, "клавіатура у форматі inline_keyboard")
ck("if not _ok_kb:" in block, "фолбек на звичайний текст, якщо клавіатура впала")
ck("_ai_uids[:5]" in block, "не більше 5 кнопок (ліміт Telegram/читабельність)")

print("\n=== 3. кнопка не мертва — bot.py її обробляє ===")
bot = open("/home/user/bot/bot.py").read()
ck('data.startswith("email_reply_")' in bot, "email_reply_ є в роутері bot.py")
ck('elif data.startswith("email_reply_"):' in bot, "є хендлер email_reply_")
ck('data.startswith("email_describe_")' in bot or "email_describe_" in bot,
   "email_describe_ теж обробляється")
ck("email_star_" in bot, "email_star_ теж обробляється")

print("\n=== 4. симуляція побудови кнопок ===")
uids = [("77391", "Rakúsko a Slovensko", '"Michaela Kovacova" <m@k.sk>'),
        ("77392", "Invoice 2026", "billing@firma.sk")]
rows = []
for u, s_, snd in uids[:5]:
    who = snd.split("<")[0].strip().strip('"')
    if not who or "@" in who:
        who = snd.split("@")[0].split("<")[-1].strip()
    who = who[:18] or "лист"
    rows.append([{"text": f"🤖✍️ Відповісти: {who}",
                  "callback_data": f"email_reply_{u}"}])
ck(len(rows) == 2, "2 листи -> 2 кнопки")
ck(rows[0][0]["callback_data"] == "email_reply_77391", "перший uid у callback")
ck("Michaela" in rows[0][0]["text"], "ім'я відправника видно на кнопці")
ck(rows[1][0]["text"].endswith("billing"), "для адреси без імені беремо логін")
ck(all(len(r[0]["callback_data"]) <= 64 for r in rows),
   "callback_data в межах ліміту Telegram (64 байти)")

print("\nПОМИЛОК:", len(FAIL))
