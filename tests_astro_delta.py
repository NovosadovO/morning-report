import sys; sys.path.insert(0,'/home/user/repo')
import astro_delta as D, ai_kit as K
T="""🔴 ♄ Сатурн Квадрат □ натальний ♃ Юпітер  <i>(0.1°)</i>
    <i>↳ Напруга.</i>
🟢 ⛢ Уран Трин △ натальний ☿ Меркурій  <i>(0.8°)</i>
🟢 ♆ Нептун Трин △ натальний Асцендент (ASC)  <i>(0.8°)</i>
🔴 🌙 Місяць Квадрат □ натальний ☀️ Сонце  <i>(1.1°)</i>"""
print(D.parse(T))
st={}
print(D.block(T))
for day in ("2026-11-10","2026-11-11"):
    for a in D.parse(T):
        r=st.setdefault(a['key'],{}); r['m']=r.get('m',[])+[day]
print("---- після 2 днів розборів")
print(D.block.__wrapped__(T) if hasattr(D.block,'__wrapped__') else D.classify(T,st,"2026-11-12"))
