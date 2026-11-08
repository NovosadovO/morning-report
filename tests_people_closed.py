import sys, types, tempfile, os
sys.path.insert(0,'/home/user/repo')
import people_memory as pm, ai_kit as K
store={}
class FS:
    d={}
    def put(self,p): self.d['1']=p; return '1'
    def get(self,i): return self.d.get(i)
    def drop(self,i): self.d.pop(i,None)
pm._store=FS()
import ai_brain; ai_brain.remember_answer=lambda *a,**k: print('remembered',a[1])

K.load=lambda f,default=None: store.get(f, default)
def uk(f,k,v): store.setdefault(f,{})[k]=v; return True
K.update_key=uk
K.rate_ok=lambda *a,**k: True
K.rate_mark=lambda *a,**k: None
rec={"name":"Veronika","relation":"робота","vip":False,"topics":["весілля"],
 "my_promises":["написати на WhatsApp"],"their_promises":["прийти"],
 "open_question":"О котрій годині прийдуть прикрашати зал","next_step":"Написати Веронікі у WhatsApp","ping_in_days":0,"last_in":"2026-10-01","last_out":"","pinged_at":""}
store["people.json"]={"v@x.sk":rec}
print("before", [e for _,e,_ in pm.needs_attention()])
pid=pm._store.put({"email":"v@x.sk","name":"Veronika",**{k:rec[k] for k in("my_promises","their_promises","open_question","next_step")}})
pm.do_skip(pid)
print("after skip", pm.needs_attention())
# snooze expired, AI rephrases same items
store["people_closed.json"]["v@x.sk"]["until"]="2020-01-01"
rec2=dict(rec); rec2["open_question"]="Коли саме прийдуть прикрашати зал?"; rec2["my_promises"]=["Написати у WhatsApp Веронікі"]
store["people.json"]["v@x.sk"]=rec2
rec2["pinged_at"]=""; print("expired+rephrased", pm.needs_attention())
rec3=dict(rec2); rec3["pinged_at"]=""; rec3["open_question"]="Чи підтвердили меню на суботу?"
store["people.json"]["v@x.sk"]=rec3
print("new question", [(s,e) for s,e,_ in pm.needs_attention()])
