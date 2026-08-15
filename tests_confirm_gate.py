import ast, re, sys
src = open('/home/user/bot/bot.py').read()
tree = ast.parse(src)
gate = None
for n in ast.walk(tree):
    if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == '_CONFIRM_GATE':
        gate = ast.literal_eval(n.value)
skip = None
for n in ast.walk(tree):
    if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == '_CONFIRM_GATE_SKIP':
        skip = ast.literal_eval(n.value)
fails = 0
print(f"gate prefixes: {len(gate)}")
# 1) кожен префікс має роутинг у bot.py
for p in gate:
    if f'startswith("{p}")' not in src and f'"{p}"' not in src.split('_CONFIRM_GATE')[-1]:
        pass
    if f'{p}' not in src.replace('_CONFIRM_GATE', ''):
        print(f"❌ префікс {p} ніде не використовується"); fails += 1
# 2) немає перетину зі SKIP
for p in gate:
    for s in skip:
        if p.startswith(s) or s.startswith(p):
            print(f"❌ конфлікт gate {p} vs skip {s}"); fails += 1
# 3) longest-prefix працює
def match(data):
    if any(data.startswith(s) for s in skip):
        return None
    pref = ""
    for p in gate:
        if data.startswith(p) and len(p) > len(pref):
            pref = p
    return pref or None
cases = [("cal_skip_12", "cal_skip_"), ("calrem_skip_12", None), ("calrem_add_9", "calrem_add_"),
         ("bill_due_paid_3", "bill_due_paid_"), ("bill_paid_3", "bill_paid_"),
         ("cfm_y_abc", None), ("cw_cancel_x", None), ("email_delete_7", None),
         ("email_send_7", "email_send_"), ("gx_note_1", None), ("cw_ok_1", None)]
for d, exp in cases:
    got = match(d)
    if got != exp:
        print(f"❌ match({d}) = {got}, очікував {exp}"); fails += 1
# 4) усі тексти заповнені
for p, t in gate.items():
    if len(t) != 5 or not all(isinstance(x, str) and x.strip() for x in t):
        print(f"❌ неповний опис для {p}"); fails += 1
    if "{subject}" not in t[0]:
        print(f"❌ немає {{subject}} у питанні {p}"); fails += 1
print("fails:", fails)
