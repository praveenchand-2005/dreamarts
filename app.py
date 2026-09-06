from flask import Flask,request,jsonify,send_from_directory
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from werkzeug.utils import secure_filename
import os,json,uuid,time,hmac,hashlib,base64,html,urllib.parse,datetime
from dreamarts_engine import generate as generate_string_art, benchmark as benchmark_string_art

BASE=os.path.dirname(__file__)
DATA=os.path.join(BASE,"data"); UP=os.path.join(BASE,"uploads")
for d in(DATA,UP): os.makedirs(d,exist_ok=True)
app=Flask(__name__,static_folder="static",static_url_path="")
app.config["MAX_CONTENT_LENGTH"]=15*1024*1024
ALLOWED={"jpg","jpeg","png","webp"}
FLOW=["NEW REQUEST","PHOTO REVIEW","QUOTE SENT","AWAITING APPROVAL","PAYMENT PENDING","PAID","IN PRODUCTION","QUALITY CHECK","SHIPPED","DELIVERED","CANCELLED"]

def supabase_request(path,method="GET",body=None,token=None,prefer=None):
 url=os.environ.get("SUPABASE_URL","").rstrip("/")+"/rest/v1/"+path
 key=os.environ.get("SUPABASE_PUBLISHABLE_KEY","");headers={"apikey":key,"Content-Type":"application/json"}
 if token: headers["Authorization"]="Bearer "+token
 if prefer: headers["Prefer"]=prefer
 req=Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
 try:
  with urlopen(req,timeout=12) as r:return json.loads(r.read().decode() or "[]")
 except HTTPError as e:return {"_error":e.read().decode()}

def razorpay_request(path,body=None):
 key=os.environ.get("RAZORPAY_KEY_ID","");secret=os.environ.get("RAZORPAY_KEY_SECRET","")
 if not key or not secret: raise RuntimeError("Razorpay is not configured")
 auth=base64.b64encode((key+":"+secret).encode()).decode()
 req=Request("https://api.razorpay.com/v1/"+path,data=json.dumps(body).encode() if body else None,headers={"Authorization":"Basic "+auth,"Content-Type":"application/json"},method="POST" if body else "GET")
 try:
  with urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
 except HTTPError as e: raise RuntimeError(e.read().decode())

def save_json(path,obj):
 with open(path,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False,indent=2)
def load_orders():
 out=[]
 for fn in os.listdir(DATA):
  if fn.startswith("order_") and fn.endswith(".json"):
   try: out.append(json.load(open(os.path.join(DATA,fn),encoding="utf-8")))
   except: pass
 return sorted(out,key=lambda x:x.get("createdAt",""),reverse=True)
def get_order(oid):
 p=os.path.join(DATA,"order_"+oid+".json")
 return (p,json.load(open(p,encoding="utf-8"))) if os.path.exists(p) else (None,None)

@app.get("/")
def home(): return send_from_directory("static","index.html")
@app.get("/login")
def login_page(): return send_from_directory("static","login.html")
@app.get("/dashboard")
def dashboard_page(): return send_from_directory("static","dashboard.html")
@app.get("/studio")
def studio_page(): return send_from_directory("static","studio.html")

@app.get("/admin")
def admin(): return send_from_directory("static","admin.html")
@app.get("/track")
def track_page(): return send_from_directory("static","track.html")
@app.get("/uploads/<path:name>")
def uploads(name): return send_from_directory(UP,name)

@app.post("/api/custom-orders")
def custom_order():
 try:
  f=request.files.get("photo")
  if not f or not f.filename: return jsonify(error="Please upload a reference photo."),400
  ext=f.filename.rsplit(".",1)[-1].lower() if "." in f.filename else ""
  if ext not in ALLOWED: return jsonify(error="Use JPG, PNG or WEBP."),400
  name=request.form.get("name","").strip(); phone=request.form.get("phone","").strip()
  if not name or not phone: return jsonify(error="Name and phone are required."),400
  oid="DA-"+time.strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:6].upper()
  filename=oid+"-"+secure_filename(f.filename); f.save(os.path.join(UP,filename))
  now=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
  order={"orderNumber":oid,"createdAt":now,"updatedAt":now,"status":"NEW REQUEST",
   "customer":{"name":name,"phone":phone,"email":request.form.get("email","").strip()},
   "artwork":{"size":request.form.get("size","Let Dreamarts recommend"),"shape":request.form.get("shape","Let Dreamarts recommend"),"notes":request.form.get("notes","").strip(),"photo":"/uploads/"+filename},
   "history":[{"status":"NEW REQUEST","at":now,"note":"Custom artwork request received."}],
   "shipping":{"carrier":"","trackingNumber":"","trackingUrl":""}}
  save_json(os.path.join(DATA,"order_"+oid+".json"),order)
  return jsonify(ok=True,orderNumber=oid,status=order["status"],trackUrl="/track?order="+oid)
 except Exception as e: return jsonify(error="Could not create request: "+str(e)),500

@app.get("/api/profile")
def get_profile():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 return jsonify(supabase_request("profiles?select=*&limit=1",token=token))

@app.patch("/api/profile")
def update_profile():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 allowed={"full_name","phone","address_line1","address_line2","city","state","postal_code","country"}
 patch={k:v for k,v in b.items() if k in allowed}
 r=supabase_request("profiles",method="PATCH",body=patch,token=token,prefer="return=representation")
 if isinstance(r,dict) and "_error" in r:return jsonify(error="Could not update profile.",details=r["_error"]),400
 return jsonify(ok=True,profile=r[0] if isinstance(r,list) and r else None)

@app.get("/api/my-notifications")
def my_notifications():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 return jsonify(supabase_request("notifications?select=*&order=created_at.desc&limit=30",token=token))

def send_whatsapp_notification(phone,message):
 token=os.environ.get("WHATSAPP_ACCESS_TOKEN","");phone_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID","")
 if not token or not phone_id or not phone:return False
 payload={"messaging_product":"whatsapp","to":phone,"type":"text","text":{"body":message}}
 try:
  req=Request("https://graph.facebook.com/v22.0/"+phone_id+"/messages",data=json.dumps(payload).encode(),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},method="POST")
  with urlopen(req,timeout=15) as r:return 200<=r.status<300
 except Exception:return False

def send_transactional_email(to_email,subject,message):
 api_key=os.environ.get("RESEND_API_KEY","");sender=os.environ.get("RESEND_FROM_EMAIL","")
 if not api_key or not sender or not to_email:return False
 body={"from":sender,"to":[to_email],"subject":subject,"html":"<h2>Dreamarts</h2><p>"+html.escape(message)+"</p>"}
 try:
  req=Request("https://api.resend.com/emails",data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json"},method="POST")
  with urlopen(req,timeout=15) as r:return 200<=r.status<300
 except Exception:return False

def create_order_notification(token,order_number,title,message,kind="order_update"):
 rows=supabase_request("orders?order_number=eq."+order_number+"&select=user_id",token=token)
 if isinstance(rows,list) and rows and rows[0].get("user_id"):
  return supabase_request("notifications",method="POST",body={"user_id":rows[0]["user_id"],"order_number":order_number,"title":title,"message":message,"type":kind},token=token,prefer="return=representation")

def generate_agent_tasks(token):
 orders=supabase_request("orders?select=order_number,status,created_at",token=token);orders=orders if isinstance(orders,list) else []
 qc=supabase_request("order_qc?select=order_number,qc_status",token=token);qc=qc if isinstance(qc,list) else []
 reviews=supabase_request("order_reviews?select=order_number,rating",token=token);reviews=reviews if isinstance(reviews,list) else []
 existing=supabase_request("agent_tasks?status=eq.PENDING&select=agent,order_number,title",token=token);existing=existing if isinstance(existing,list) else []
 keys={(x.get("agent"),x.get("order_number"),x.get("title")) for x in existing}
 now=datetime.datetime.utcnow();created=[]
 candidates=[]
 for o in orders:
  if str(o.get("status","")).upper() not in ("DELIVERED","CANCELLED"):
   try:age=(now-datetime.datetime.fromisoformat(str(o["created_at"]).replace("Z","+00:00")).replace(tzinfo=None)).days
   except Exception:age=0
   if age>=3:candidates.append(("Production Agent",o["order_number"],"Order aging risk","HIGH" if age>=7 else "MEDIUM",f"Investigate order status; order open for {age} days."))
   if str(o.get("status","")).upper() in ("PAID","IN PRODUCTION") and age>=2:candidates.append(("Operations Agent",o["order_number"],"Workflow bottleneck check","MEDIUM","Check assignment, production capacity, and next operational action."))
 for q in qc:
  if q.get("qc_status")=="PENDING":candidates.append(("Quality Agent",q["order_number"],"Pending QC inspection","MEDIUM","Assign or complete quality inspection."))
 for r in reviews:
  if float(r.get("rating") or 5)<=2:candidates.append(("Customer Success Agent",r.get("order_number"),"Low customer rating","HIGH","Review feedback and prepare service-recovery response."))
 for agent,order,title,priority,action in candidates:
  key=(agent,order,title)
  if key not in keys:
   row={"agent":agent,"order_number":order,"title":title,"priority":priority,"status":"PENDING","requires_approval":True,"proposed_action":action}
   result=supabase_request("agent_tasks",method="POST",body=row,token=token,prefer="return=representation")
   if isinstance(result,list):created.append(result[0])
 return created

def agent_risk_score(priority,age=0,confidence=0.75):
 base={"LOW":25,"MEDIUM":50,"HIGH":75,"CRITICAL":95}.get(priority,40)
 return min(100,round(base*0.65+min(age*4,20)+confidence*15))

def build_agent_recommendation(agent,title,priority,action,age=0):
 score=agent_risk_score(priority,age)
 return {"agent":agent,"signal":title,"priority":priority,"risk_score":score,"confidence":0.82 if score>=70 else 0.72,"recommended_action":action,"requires_approval":True}

@app.get("/api/admin/agent-intelligence")
def agent_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 rows=supabase_request("agent_tasks?status=eq.PENDING&select=*&order=created_at.desc&limit=100",token=token)
 rows=rows if isinstance(rows,list) else []
 intelligence=[]
 for t in rows:
  rec=build_agent_recommendation(t.get("agent"),t.get("title"),t.get("priority"),t.get("proposed_action") or "")
  rec["task_id"]=t.get("id");rec["order_number"]=t.get("order_number");intelligence.append(rec)
 intelligence.sort(key=lambda x:x["risk_score"],reverse=True)
 summary={"total_signals":len(intelligence),"critical":sum(1 for x in intelligence if x["risk_score"]>=90),"high_risk":sum(1 for x in intelligence if x["risk_score"]>=70)}
 return jsonify(ok=True,summary=summary,recommendations=intelligence)

def agent_memory_summary(token):
 rows=supabase_request("agent_tasks?select=agent,status,priority,created_at&order=created_at.desc&limit=500",token=token)
 rows=rows if isinstance(rows,list) else []
 memory={}
 for row in rows:
  agent=row.get("agent","Unknown Agent");m=memory.setdefault(agent,{"total":0,"pending":0,"executed":0,"high_priority":0})
  m["total"]+=1
  if row.get("status")=="PENDING":m["pending"]+=1
  if row.get("status")=="EXECUTED":m["executed"]+=1
  if row.get("priority") in ("HIGH","CRITICAL"):m["high_priority"]+=1
 return memory

def orchestrate_agents(token):
 memory=agent_memory_summary(token);handoffs=[]
 for agent,m in memory.items():
  if m["pending"]>=3:
   handoffs.append({"from":agent,"to":"Founder Agent","reason":f"{m['pending']} unresolved tasks require prioritization.","priority":"HIGH"})
 return {"memory":memory,"handoffs":handoffs,"generated_at":datetime.datetime.utcnow().isoformat()+"Z"}

@app.get("/api/admin/agent-orchestration")
def agent_orchestration():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,orchestration=orchestrate_agents(auth.split(" ",1)[1]))

def kpi_anomalies(token):
 orders=supabase_request("orders?select=status,created_at,total_amount&limit=1000",token=token);orders=orders if isinstance(orders,list) else []
 now=datetime.datetime.utcnow();recent=0;previous=0;stuck=0
 for o in orders:
  try: age=(now-datetime.datetime.fromisoformat(str(o.get("created_at","")).replace("Z","+00:00")).replace(tzinfo=None)).days
  except: continue
  if age<7:recent+=1
  elif age<14:previous+=1
  if str(o.get("status","")).upper() not in ("DELIVERED","CANCELLED") and age>=5:stuck+=1
 alerts=[]
 if previous>0 and recent<previous*0.6:alerts.append({"kpi":"Order volume","severity":"HIGH","change_pct":round((recent/previous-1)*100),"finding":"Recent order volume materially below previous period."})
 if stuck>=3:alerts.append({"kpi":"Fulfillment backlog","severity":"HIGH","value":stuck,"finding":"Multiple orders are aging beyond operational threshold."})
 return {"recent_orders":recent,"previous_period_orders":previous,"stuck_orders":stuck,"alerts":alerts}

def autonomous_investigation(token):
 anomalies=kpi_anomalies(token);chains=[]
 for alert in anomalies["alerts"]:
  chains.append({"trigger":alert["kpi"],"severity":alert["severity"],"steps":[{"agent":"Operations Agent","action":"Validate source data and isolate affected workflow."},{"agent":"Production Agent","action":"Check capacity, queue, and fulfillment constraints."},{"agent":"Founder Agent","action":"Review root-cause summary and approve corrective action."}]})
 return {"anomalies":anomalies,"investigation_chains":chains,"generated_at":datetime.datetime.utcnow().isoformat()+"Z"}

@app.get("/api/admin/business-intelligence")
def business_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,intelligence=autonomous_investigation(auth.split(" ",1)[1]))

def founder_briefing(token):
 tasks=supabase_request("agent_tasks?select=agent,title,priority,status,proposed_action,created_at&order=created_at.desc&limit=200",token=token);tasks=tasks if isinstance(tasks,list) else []
 pending=[t for t in tasks if t.get("status")=="PENDING"];critical=[t for t in pending if t.get("priority") in ("CRITICAL","HIGH")]
 anomalies=kpi_anomalies(token).get("alerts",[])
 actions=[]
 for t in critical[:5]:actions.append({"priority":t.get("priority"),"owner":t.get("agent"),"issue":t.get("title"),"action":t.get("proposed_action")})
 return {"generated_at":datetime.datetime.utcnow().isoformat()+"Z","headline":f"{len(critical)} high-priority items require attention.","metrics":{"pending_tasks":len(pending),"high_priority":len(critical),"kpi_alerts":len(anomalies)},"top_actions":actions,"kpi_alerts":anomalies,"message":"Focus on the highest-impact unresolved signals first; approve only actions aligned with current business priorities."}

@app.get("/api/admin/founder-briefing")
def founder_briefing_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,briefing=founder_briefing(auth.split(" ",1)[1]))

def decision_learning(token):
 rows=supabase_request("agent_tasks?select=agent,priority,status,title,proposed_action,updated_at&limit=1000",token=token);rows=rows if isinstance(rows,list) else []
 agents={}
 for x in rows:
  name=x.get("agent","Unknown Agent");m=agents.setdefault(name,{"total":0,"executed":0,"pending":0,"high_priority":0})
  m["total"]+=1;m["executed"]+=x.get("status")=="EXECUTED";m["pending"]+=x.get("status")=="PENDING";m["high_priority"]+=x.get("priority") in ("HIGH","CRITICAL")
 for m in agents.values():m["execution_rate"]=round(m["executed"]/m["total"]*100,1) if m["total"] else 0
 return agents

@app.get("/api/admin/decision-learning")
def decision_learning_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 data=decision_learning(auth.split(" ",1)[1])
 return jsonify(ok=True,learning=data,insight="Agent execution patterns are used to identify recurring workload and prioritization gaps.")

def outcome_tracking(token):
 rows=supabase_request("agent_tasks?select=id,agent,title,priority,status,created_at,updated_at&limit=1000",token=token);rows=rows if isinstance(rows,list) else []
 now=datetime.datetime.utcnow();by_agent={};executed=[]
 for x in rows:
  agent=x.get("agent","Unknown Agent");m=by_agent.setdefault(agent,{"created":0,"executed":0,"pending":0,"avg_resolution_hours":0,"_hours":[]})
  m["created"]+=1
  if x.get("status")=="EXECUTED":
   m["executed"]+=1;executed.append(x)
   try:
    c=datetime.datetime.fromisoformat(str(x.get("created_at")).replace("Z","+00:00")).replace(tzinfo=None);u=datetime.datetime.fromisoformat(str(x.get("updated_at") or x.get("created_at")).replace("Z","+00:00")).replace(tzinfo=None);m["_hours"].append(round((u-c).total_seconds()/3600,2))
   except: pass
  elif x.get("status")=="PENDING":m["pending"]+=1
 for m in by_agent.values():
  h=m.pop("_hours");m["avg_resolution_hours"]=round(sum(h)/len(h),2) if h else 0;m["execution_rate"]=round(m["executed"]/m["created"]*100,1) if m["created"] else 0
 return {"total_tasks":len(rows),"executed_tasks":len(executed),"overall_execution_rate":round(len(executed)/len(rows)*100,1) if rows else 0,"agents":by_agent}

@app.get("/api/admin/outcomes")
def outcomes_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,outcomes=outcome_tracking(auth.split(" ",1)[1]))

def autonomous_priorities(token):
 rows=supabase_request("agent_tasks?status=eq.PENDING&select=id,agent,title,priority,order_number,proposed_action,created_at&limit=500",token=token);rows=rows if isinstance(rows,list) else []
 base={"LOW":20,"MEDIUM":45,"HIGH":70,"CRITICAL":90};now=datetime.datetime.utcnow();ranked=[]
 for x in rows:
  try: age=max(0,(now-datetime.datetime.fromisoformat(str(x.get("created_at")).replace("Z","+00:00")).replace(tzinfo=None)).total_seconds()/3600)
  except: age=0
  urgency=min(20,age/24*5);score=min(100,round(base.get(x.get("priority"),40)+urgency))
  ranked.append({"task_id":x.get("id"),"agent":x.get("agent"),"title":x.get("title"),"priority":x.get("priority"),"impact_score":score,"urgency_score":round(urgency,1),"recommended_action":x.get("proposed_action")})
 return sorted(ranked,key=lambda x:x["impact_score"],reverse=True)

@app.get("/api/admin/autonomous-priorities")
def autonomous_priorities_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 ranked=autonomous_priorities(auth.split(" ",1)[1])
 return jsonify(ok=True,count=len(ranked),priorities=ranked)

def executive_decision_queue(token):
 ranked=autonomous_priorities(token)
 outcomes=outcome_tracking(token)
 agent_perf=outcomes.get("agents",{})
 quality=recommendation_quality(token)
 drift=recommendation_drift(token)
 penalties={x["agent"]:min(0.35,x["quality_drop"]/100) for x in drift.get("alerts",[])}
 decisions=[]
 for x in ranked:
  perf=agent_perf.get(x.get("agent"),{})
  q=quality.get(x.get("agent"),{})
  base_trust=q.get("quality_score",50)/100
  penalty=penalties.get(x.get("agent"),0)
  recovery=0
  # Sustained strong recommendation quality restores part of calibration penalties.
  if penalty and q.get("high_priority_quality",0)>=80 and q.get("quality_score",0)>=75:
   recovery=min(penalty*0.5,(q.get("quality_score",0)-70)/100)
  effective_penalty=max(0,penalty-recovery)
  trust=max(0.1,base_trust-effective_penalty)
  execution=perf.get("execution_rate",0)/100
  confidence=min(0.97,0.35+execution*0.25+trust*0.4)
  score=round(x.get("impact_score",0)*0.65+confidence*35)
  decisions.append({**x,"base_trust_score":round(base_trust*100,1),"calibration_penalty":round(penalty*100,1),"recovery_credit":round(recovery*100,1),"effective_penalty":round(effective_penalty*100,1),"trust_score":round(trust*100,1),"decision_score":score,"confidence":round(confidence,2),"decision":"ACT NOW" if score>=80 else "REVIEW NEXT" if score>=60 else "MONITOR"})
 return sorted(decisions,key=lambda x:x["decision_score"],reverse=True)

@app.get("/api/admin/executive-decisions")
def executive_decisions_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 q=executive_decision_queue(auth.split(" ",1)[1])
 return jsonify(ok=True,summary={"act_now":sum(1 for x in q if x["decision"]=="ACT NOW"),"review_next":sum(1 for x in q if x["decision"]=="REVIEW NEXT"),"monitor":sum(1 for x in q if x["decision"]=="MONITOR")},decisions=q)

@app.post("/api/admin/agents/run")
def run_agents_now():
 auth=request.headers.get("Authorization","")
 agent_key=os.environ.get("DREAMARTS_AGENT_KEY","")
 if agent_key and auth=="Bearer "+agent_key:
  token=os.environ.get("SUPABASE_PUBLISHABLE_KEY","")
  if not token:return jsonify(error="Supabase API key is not configured."),503
 elif auth.startswith("Bearer "):
  token=auth.split(" ",1)[1]
 else:return jsonify(error="Unauthorized"),401
 created=generate_agent_tasks(token)
 return jsonify(ok=True,created=len(created),tasks=created,ran_at=datetime.datetime.utcnow().isoformat()+"Z")

@app.get("/api/admin/agent-tasks")
def agent_tasks():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 generate_agent_tasks(token)
 rows=supabase_request("agent_tasks?select=*&order=created_at.desc&limit=100",token=token)
 return jsonify(rows if isinstance(rows,list) else [])

@app.post("/api/admin/agent-tasks")
def create_agent_task():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 required=["agent","title","priority"]
 if any(not b.get(x) for x in required):return jsonify(error="Missing task fields."),400
 row={"agent":b["agent"],"title":str(b["title"])[:500],"priority":b["priority"],"order_number":b.get("order_number"),"status":"PENDING","requires_approval":bool(b.get("requires_approval",True)),"proposed_action":b.get("proposed_action")}
 r=supabase_request("agent_tasks",method="POST",body=row,token=token,prefer="return=representation")
 return jsonify(r[0] if isinstance(r,list) and r else {"ok":True})

@app.post("/api/admin/agent-tasks/<task_id>/approve")
def approve_agent_task(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 task=supabase_request("agent_tasks?id=eq."+task_id+"&select=*&limit=1",token=token)
 if not isinstance(task,list) or not task:return jsonify(error="Task not found."),404
 t=task[0]
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"EXECUTED","approved_at":datetime.datetime.utcnow().isoformat()+"Z"},token=token)
 return jsonify(ok=True,task_id=task_id,status="EXECUTED")

@app.post("/api/admin/agent-tasks/<task_id>/reject")
def reject_agent_task(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];reason=(request.get_json(silent=True) or {}).get("reason","Founder rejected recommendation.")
 r=supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"REJECTED","rejection_reason":reason,"updated_at":datetime.datetime.utcnow().isoformat()+"Z"},token=token)
 return jsonify(ok=True,task_id=task_id,status="REJECTED")

@app.post("/api/admin/agent-tasks/<task_id>/delegate")
def delegate_agent_task(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {};owner=b.get("owner","Founder")
 r=supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"DELEGATED","delegated_to":owner,"updated_at":datetime.datetime.utcnow().isoformat()+"Z"},token=token)
 return jsonify(ok=True,task_id=task_id,status="DELEGATED",delegated_to=owner)

def decision_audit(token):
 rows=supabase_request("agent_tasks?select=id,agent,title,priority,status,order_number,created_at,updated_at,approved_at,delegated_to,rejection_reason&order=updated_at.desc&limit=500",token=token)
 rows=rows if isinstance(rows,list) else []
 audit=[]
 for x in rows:
  if x.get("status") in ("EXECUTED","REJECTED","DELEGATED"):
   audit.append({"task_id":x.get("id"),"agent":x.get("agent"),"decision":"APPROVED" if x.get("status")=="EXECUTED" else x.get("status"),"title":x.get("title"),"priority":x.get("priority"),"timestamp":x.get("updated_at") or x.get("approved_at") or x.get("created_at"),"delegate":x.get("delegated_to"),"reason":x.get("rejection_reason")})
 return audit

@app.get("/api/admin/decision-audit")
def decision_audit_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 audit=decision_audit(auth.split(" ",1)[1])
 return jsonify(ok=True,count=len(audit),audit=audit)

@app.get("/api/admin/decision-analytics")
def decision_analytics_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 audit=decision_audit(auth.split(" ",1)[1]);total=len(audit)
 approved=sum(1 for x in audit if x.get("decision")=="APPROVED");rejected=sum(1 for x in audit if x.get("decision")=="REJECTED");delegated=sum(1 for x in audit if x.get("decision")=="DELEGATED")
 agents={}
 for x in audit:
  n=x.get("agent","Unknown Agent");m=agents.setdefault(n,{"total":0,"approved":0,"rejected":0,"delegated":0});m["total"]+=1
  d=x.get("decision","").lower();m[d]=m.get(d,0)+1
 for m in agents.values():m["approval_rate"]=round(m["approved"]/m["total"]*100,1) if m["total"] else 0
 return jsonify(ok=True,summary={"total_decisions":total,"approved":approved,"rejected":rejected,"delegated":delegated,"approval_rate":round(approved/total*100,1) if total else 0},agents=agents)

def recommendation_quality(token):
 audit=decision_audit(token);groups={}
 for x in audit:
  key=x.get("agent","Unknown Agent");m=groups.setdefault(key,{"total":0,"approved":0,"rejected":0,"delegated":0,"high":0,"high_approved":0})
  m["total"]+=1
  if x.get("decision")=="APPROVED":m["approved"]+=1
  if x.get("decision")=="REJECTED":m["rejected"]+=1
  if x.get("decision")=="DELEGATED":m["delegated"]+=1
  if x.get("priority") in ("HIGH","CRITICAL"):
   m["high"]+=1
   if x.get("decision")=="APPROVED":m["high_approved"]+=1
 for m in groups.values():
  m["quality_score"]=round((m["approved"]/m["total"]*100) if m["total"] else 0,1)
  m["high_priority_quality"]=round((m["high_approved"]/m["high"]*100) if m["high"] else 0,1)
 return groups

@app.get("/api/admin/recommendation-quality")
def recommendation_quality_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 q=recommendation_quality(auth.split(" ",1)[1])
 return jsonify(ok=True,quality=q)

def recommendation_drift(token):
 audit=decision_audit(token);now=datetime.datetime.utcnow();groups={}
 for x in audit:
  try: ts=datetime.datetime.fromisoformat(str(x.get("timestamp")).replace("Z","+00:00")).replace(tzinfo=None)
  except: continue
  n=x.get("agent","Unknown Agent");g=groups.setdefault(n,{"recent":[],"historical":[]})
  approved=1 if x.get("decision")=="APPROVED" else 0
  (g["recent"] if (now-ts).days<=30 else g["historical"]).append(approved)
 alerts=[]
 for n,g in groups.items():
  if len(g["recent"])>=3 and len(g["historical"])>=3:
   recent=sum(g["recent"])/len(g["recent"])*100;hist=sum(g["historical"])/len(g["historical"])*100;drop=round(hist-recent,1)
   if drop>=20:alerts.append({"agent":n,"historical_quality":round(hist,1),"recent_quality":round(recent,1),"quality_drop":drop,"severity":"HIGH" if drop>=35 else "MEDIUM","action":"Review recent recommendations and temporarily reduce trust weighting."})
 return {"alerts":alerts,"agents_checked":len(groups)}

@app.get("/api/admin/recommendation-drift")
def recommendation_drift_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,drift=recommendation_drift(auth.split(" ",1)[1]))

@app.get("/api/admin/business-pulse")
def business_pulse_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=order_number,status,created_at,amount,price,payment_status&limit=2000",token=token);orders=orders if isinstance(orders,list) else []
 now=datetime.datetime.utcnow();paid=[o for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS")]
 revenue=sum(float(o.get("amount") or o.get("price") or 0) for o in paid)
 recent=[];aging=[]
 for o in orders:
  try:
   ts=datetime.datetime.fromisoformat(str(o.get("created_at")).replace("Z","+00:00")).replace(tzinfo=None);age=(now-ts).total_seconds()/3600
   if age<=24:recent.append(o)
   if age>=72 and str(o.get("status","")).upper() not in ("COMPLETED","DELIVERED","CANCELLED"):aging.append(o)
  except:pass
 return jsonify(ok=True,pulse={"total_orders":len(orders),"paid_orders":len(paid),"paid_revenue":round(revenue,2),"orders_last_24h":len(recent),"aging_orders":len(aging),"average_order_value":round(revenue/len(paid),2) if paid else 0,"generated_at":now.isoformat()+"Z"},alerts=[{"severity":"HIGH","area":"OPERATIONS","message":f"{len(aging)} orders have been open for 72+ hours."}] if aging else [])

@app.get("/api/admin/ai-employees")
def ai_employees():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=order_number,status,created_at",token=token);orders=orders if isinstance(orders,list) else []
 qc=supabase_request("order_qc?select=order_number,qc_status",token=token);qc=qc if isinstance(qc,list) else []
 reviews=supabase_request("order_reviews?select=rating,created_at",token=token);reviews=reviews if isinstance(reviews,list) else []
 pending_qc=sum(1 for x in qc if x.get("qc_status")=="PENDING")
 open_orders=sum(1 for x in orders if str(x.get("status","")).upper() not in ("DELIVERED","CANCELLED"))
 low_reviews=sum(1 for x in reviews if float(x.get("rating") or 5)<=2)
 return jsonify({"employees":[
 {"id":"production","name":"Production Agent","status":"ACTIVE","mission":"Monitor production queue and delivery risk.","workload":open_orders,"signal":"Review capacity" if open_orders>10 else "Queue healthy"},
 {"id":"quality","name":"Quality Agent","status":"ACTIVE","mission":"Monitor QC outcomes and unresolved inspections.","workload":pending_qc,"signal":"QC follow-up required" if pending_qc else "Quality queue clear"},
 {"id":"customer_success","name":"Customer Success Agent","status":"ACTIVE","mission":"Monitor customer satisfaction and service recovery.","workload":low_reviews,"signal":"Low-rating reviews require attention" if low_reviews else "Customer satisfaction stable"},
 {"id":"growth","name":"Growth Agent","status":"ACTIVE","mission":"Monitor referrals, reviews and gallery growth.","workload":len(reviews),"signal":"Continue social-proof collection"}
 ]})

@app.get("/api/admin/operations-agent")
def operations_agent():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=order_number,status,created_at",token=token);orders=orders if isinstance(orders,list) else []
 now=datetime.datetime.utcnow();actions=[]
 for o in orders:
  if str(o.get("status","")).upper() in ("DELIVERED","CANCELLED"):continue
  try:age=(now-datetime.datetime.fromisoformat(str(o["created_at"]).replace("Z","+00:00")).replace(tzinfo=None)).days
  except Exception:age=0
  if age>=5:actions.append({"type":"DELAY_ALERT","priority":"HIGH","order_number":o["order_number"],"title":"Potentially delayed order","summary":f"Order has been open for {age} days.","proposed_action":"Review production status and prepare customer update.","requires_approval":True})
 qc=supabase_request("order_qc?qc_status=eq.PENDING&select=order_number,created_at",token=token);qc=qc if isinstance(qc,list) else []
 for q in qc:actions.append({"type":"QC_FOLLOWUP","priority":"MEDIUM","order_number":q["order_number"],"title":"QC follow-up needed","summary":"Quality inspection remains pending.","proposed_action":"Assign QC inspection to production team.","requires_approval":True})
 return jsonify({"actions":actions[:30],"count":len(actions)})

@app.post("/api/admin/operations-agent/approve")
def approve_agent_action():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {};order_number=b.get("order_number");action_type=b.get("type")
 if not order_number or not action_type:return jsonify(error="Missing action data."),400
 # Execute only bounded, reversible workflow actions after approval.
 if action_type=="QC_FOLLOWUP":
  supabase_request("order_qc?order_number=eq."+order_number,method="PATCH",body={"qc_status":"PENDING"},token=token)
  create_order_notification(token,order_number,"QC follow-up approved","Our operations team has been assigned a quality-control follow-up.")
 elif action_type=="DELAY_ALERT":
  create_order_notification(token,order_number,"Production update","Your order is receiving an operations review. We will keep you updated on progress.")
  supabase_request("agent_action_log",method="POST",body={"order_number":order_number,"action_type":action_type,"status":"EXECUTED"},token=token)
 else:return jsonify(error="Unsupported action type."),400
 return jsonify(ok=True,message="Approved action executed successfully.",order_number=order_number,type=action_type,status="EXECUTED")

@app.get("/api/admin/recommendations")
def admin_recommendations():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=*",token=token);orders=orders if isinstance(orders,list) else []
 paid=[o for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS")]
 engines={}
 for o in paid:
  try:
   e=json.loads(o.get("notes") or "{}").get("selected_engine")
   if e:engines.setdefault(e,{"orders":0,"revenue":0});engines[e]["orders"]+=1;engines[e]["revenue"]+=float(o.get("amount") or o.get("price") or 0)
  except Exception:pass
 qc=supabase_request("order_qc?select=qc_status",token=token);qc=qc if isinstance(qc,list) else []
 open_orders=[o for o in orders if str(o.get("status","")).upper() not in ("DELIVERED","CANCELLED")]
 rec=[]
 if open_orders:rec.append({"priority":"HIGH" if len(open_orders)>10 else "MEDIUM","area":"OPERATIONS","title":"Review production capacity","reason":f"{len(open_orders)} orders are currently open.","action":"Review staffing and production queue."})
 if qc and sum(1 for q in qc if q.get("qc_status")!="APPROVED")/len(qc)>.2:rec.append({"priority":"HIGH","area":"QUALITY","title":"Investigate QC failures","reason":"More than 20% of QC records are not approved.","action":"Review recurring production defects."})
 if engines:
  best=max(engines,key=lambda x:engines[x]["revenue"]);rec.append({"priority":"MEDIUM","area":"PRODUCT","title":"Promote the strongest engine","reason":f"{best} currently generates the highest recorded paid revenue.","action":"Feature it more prominently in the Studio."})
 refs=supabase_request("referrals?select=successful_referrals,reward_balance",token=token);refs=refs if isinstance(refs,list) else []
 successes=sum(int(x.get("successful_referrals") or 0) for x in refs)
 rec.append({"priority":"LOW" if successes else "MEDIUM","area":"GROWTH","title":"Review referral performance","reason":f"{successes} successful referral conversions recorded.","action":"Increase referral visibility if conversions remain low."})
 if not rec:rec.append({"priority":"LOW","area":"DATA","title":"Collect more operating data","reason":"The recommendation engine needs more order history.","action":"Continue normal operations and revisit after additional orders."})
 return jsonify({"recommendations":rec,"generated_at":datetime.datetime.utcnow().isoformat()+"Z"})

@app.get("/api/admin/insights")
def admin_insights():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=*",token=token);orders=orders if isinstance(orders,list) else []
 now=datetime.datetime.utcnow();days={}
 engines={};shapes={};widths=[];pipeline_age=[]
 for o in orders:
  created=str(o.get("created_at") or "")[:10]
  if created:days[created]=days.get(created,0)+1
  try:
   n=json.loads(o.get("notes") or "{}");e=n.get("selected_engine")
   if e:engines[e]=engines.get(e,0)+1
  except Exception:pass
  if o.get("artwork_shape"):shapes[o["artwork_shape"]]=shapes.get(o["artwork_shape"],0)+1
  if o.get("artwork_width_mm"):widths.append(float(o["artwork_width_mm"]))
  if str(o.get("status","")).upper() not in ("DELIVERED","CANCELLED"):
   try:pipeline_age.append((now-datetime.datetime.fromisoformat(str(o["created_at"]).replace("Z","+00:00")).replace(tzinfo=None)).days)
   except Exception:pass
 recent=sum(v for k,v in days.items() if k>=str((now-datetime.timedelta(days=7)).date()))
 paid=[o for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS")]
 revenue=sum(float(o.get("amount") or o.get("price") or 0) for o in paid)
 return jsonify({"forecast":{"next_7_days_orders":round(recent*1.0),"signal":"baseline based on last 7 days"},"demand":{"top_engines":sorted(engines.items(),key=lambda x:x[1],reverse=True)[:5],"top_shapes":sorted(shapes.items(),key=lambda x:x[1],reverse=True)[:5],"average_width_mm":round(sum(widths)/len(widths),1) if widths else 0},"operations":{"open_orders":len(pipeline_age),"average_open_order_age_days":round(sum(pipeline_age)/len(pipeline_age),1) if pipeline_age else 0},"finance":{"paid_revenue":revenue,"paid_order_count":len(paid),"average_paid_order_value":round(revenue/len(paid),2) if paid else 0},"founder_signals":[("Demand is building" if recent>=3 else "Demand sample still small"),("Production attention needed" if pipeline_age and sum(pipeline_age)/len(pipeline_age)>5 else "Production pipeline healthy"),("Best-selling engine: "+sorted(engines,key=engines.get,reverse=True)[0] if engines else "Collect more engine selection data")]})

@app.get("/api/admin/analytics")
def admin_analytics():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=*",token=token)
 orders=orders if isinstance(orders,list) else []
 paid=[o for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS")]
 revenue=sum(float(o.get("amount") or o.get("price") or 0) for o in paid)
 status={}
 for o in orders:status[o.get("status","UNKNOWN")]=status.get(o.get("status","UNKNOWN"),0)+1
 qc=supabase_request("order_qc?select=qc_status",token=token);qc=qc if isinstance(qc,list) else []
 approved=sum(1 for x in qc if x.get("qc_status")=="APPROVED")
 reviews=supabase_request("order_reviews?select=rating",token=token);reviews=reviews if isinstance(reviews,list) else []
 avg_rating=round(sum(float(x["rating"]) for x in reviews)/len(reviews),2) if reviews else 0
 refs=supabase_request("referrals?select=successful_referrals,reward_balance",token=token);refs=refs if isinstance(refs,list) else []
 return jsonify({"orders_total":len(orders),"paid_orders":len(paid),"revenue":revenue,"order_status":status,"qc_total":len(qc),"qc_approved":approved,"qc_pass_rate":round(approved*100/len(qc),1) if qc else 0,"reviews_total":len(reviews),"average_rating":avg_rating,"referral_customers":len(refs),"successful_referrals":sum(int(x.get("successful_referrals") or 0) for x in refs),"reward_liability":sum(float(x.get("reward_balance") or 0) for x in refs)})

@app.get("/api/admin/orders/<order_number>/qc")
def get_qc(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(supabase_request("order_qc?order_number=eq."+order_number+"&select=*&limit=1",token=auth.split(" ",1)[1]))

@app.put("/api/admin/orders/<order_number>/qc")
def save_qc(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 fields={"order_number":order_number,"nail_alignment":bool(b.get("nail_alignment")),"string_tension":bool(b.get("string_tension")),"design_match":bool(b.get("design_match")),"frame_condition":bool(b.get("frame_condition")),"final_photo_url":b.get("final_photo_url"),"notes":b.get("notes"),"qc_status":b.get("qc_status","PENDING")}
 existing=supabase_request("order_qc?order_number=eq."+order_number+"&select=id",token=token)
 if isinstance(existing,list) and existing:
  r=supabase_request("order_qc?order_number=eq."+order_number,method="PATCH",body=fields,token=token,prefer="return=representation")
 else:r=supabase_request("order_qc",method="POST",body=fields,token=token,prefer="return=representation")
 if isinstance(r,dict) and "_error" in r:return jsonify(error="Could not save QC.",details=r["_error"]),400
 if fields["qc_status"]=="APPROVED":
  supabase_request("orders?order_number=eq."+order_number,method="PATCH",body={"status":"SHIPPED"},token=token)
  create_order_notification(token,order_number,"Quality inspection passed","Your artwork passed final quality inspection and is ready for shipping.","quality")
 return jsonify(ok=True)

@app.get("/api/admin/orders/<order_number>/specification")
def order_specification(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 rows=supabase_request("orders?order_number=eq."+order_number+"&select=*",token=token)
 if not isinstance(rows,list) or not rows:return jsonify(error="Order not found."),404
 o=rows[0]
 try:o["production_notes"]=json.loads(o.get("notes") or "{}")
 except Exception:o["production_notes"]={}
 return jsonify(o)

@app.get("/api/admin/production")
def production_queue():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(supabase_request("orders?select=*&status=in.(PAID,IN_PRODUCTION,QUALITY_CHECK,SHIPPED)&order=created_at.asc",token=auth.split(" ",1)[1]))

@app.patch("/api/admin/production/<order_number>")
def production_update(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {}
 allowed={"status","tracking_number","courier","admin_notes"}
 patch={k:v for k,v in b.items() if k in allowed}
 if not patch:return jsonify(error="Nothing to update."),400
 r=supabase_request("orders?order_number=eq."+order_number,method="PATCH",body=patch,token=auth.split(" ",1)[1],prefer="return=representation")
 if isinstance(r,dict) and "_error" in r:return jsonify(error="Production update failed.",details=r["_error"]),400
 status=patch.get("status")
 notices={"IN_PRODUCTION":("Your artwork is now in production","Our makers have started creating your Dreamarts artwork."),"QUALITY_CHECK":("Your artwork is in quality inspection","Your finished artwork is being checked before shipping."),"SHIPPED":("Your order has been shipped","Your Dreamarts artwork is with the courier."),"DELIVERED":("Your order has been delivered","We hope you love your Dreamarts artwork!")}
 if status in notices:
  title,msg=notices[status]
  if status=="SHIPPED" and patch.get("tracking_number"):msg+=" Tracking number: "+patch["tracking_number"]
  create_order_notification(token,order_number,title,msg,"shipping" if status=="SHIPPED" else "production")
 return jsonify(ok=True)

@app.get("/api/admin/orders")
def admin_orders():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 return jsonify(supabase_request("orders?select=*&order=created_at.desc",token=token))

@app.patch("/api/admin/orders/<order_number>")
def admin_update_order(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 allowed={"status","price","shipping_cost","admin_notes"}
 patch={k:v for k,v in b.items() if k in allowed}
 if not patch:return jsonify(error="Nothing to update."),400
 result=supabase_request("orders?order_number=eq."+order_number,method="PATCH",body=patch,token=token,prefer="return=representation")
 if isinstance(result,dict) and "_error" in result:return jsonify(error="Could not update order.",details=result["_error"]),400
 return jsonify(ok=True,order=result[0] if isinstance(result,list) and result else None)

@app.get("/api/orders")
def orders(): return jsonify(load_orders())

@app.get("/api/orders/<order_id>")
def order_detail(order_id):
 _,o=get_order(order_id)
 if not o:return jsonify(error="Order not found"),404
 return jsonify(o)

@app.get("/api/track/<order_id>")
def track_order(order_id):
 _,o=get_order(order_id)
 if not o:return jsonify(error="Order not found"),404
 return jsonify(
  orderNumber=o["orderNumber"],
  status=o["status"],
  createdAt=o["createdAt"],
  artwork={"size":o["artwork"]["size"],"shape":o["artwork"]["shape"]},
  history=o.get("history",[]),
  shipping=o.get("shipping",{})
 )

@app.patch("/api/orders/<order_id>")
def update_order(order_id):
 p,o=get_order(order_id)
 if not o:return jsonify(error="Order not found"),404
 body=request.get_json(silent=True) or {}; now=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
 status=body.get("status")
 if status:
  if status not in FLOW:return jsonify(error="Invalid order status"),400
  if status!=o["status"]:
   o["status"]=status;o.setdefault("history",[]).append({"status":status,"at":now,"note":body.get("note","")})
 if "shipping" in body and isinstance(body["shipping"],dict):
  o["shipping"].update({k:v for k,v in body["shipping"].items() if k in {"carrier","trackingNumber","trackingUrl"}})
 o["updatedAt"]=now;save_json(p,o);return jsonify(o)

def order_timeline(status):
 stages=["AWAITING_APPROVAL","QUOTE_SENT","PAYMENT_PENDING","PAID","IN_PRODUCTION","QUALITY_CHECK","SHIPPED","DELIVERED"]
 labels={"AWAITING_APPROVAL":"Design submitted","QUOTE_SENT":"Quote ready","PAYMENT_PENDING":"Awaiting payment","PAID":"Payment confirmed","IN_PRODUCTION":"Artwork in production","QUALITY_CHECK":"Quality inspection","SHIPPED":"Handed to courier","DELIVERED":"Delivered"}
 if status=="CANCELLED":return [{"status":"CANCELLED","label":"Order cancelled","done":True}]
 idx=stages.index(status) if status in stages else 0
 return [{"status":s,"label":labels[s],"done":i<=idx,"current":i==idx} for i,s in enumerate(stages)]

@app.post("/api/referral/attribute")
def attribute_referral():
 b=request.get_json(silent=True) or {};code=str(b.get("code","")).upper();customer=b.get("customer_id")
 if not code or not customer:return jsonify(error="Missing referral code or customer."),400
 refs=supabase_request("referrals?code=eq."+code+"&select=referrer_id")
 if not isinstance(refs,list) or not refs:return jsonify(error="Invalid referral code."),404
 if refs[0]["referrer_id"]==customer:return jsonify(error="Self referral is not allowed."),400
 existing=supabase_request("referral_attributions?referred_customer_id=eq."+customer+"&select=id")
 if existing:return jsonify(ok=True,already_attributed=True)
 r=supabase_request("referral_attributions",method="POST",body={"referral_code":code,"referrer_id":refs[0]["referrer_id"],"referred_customer_id":customer,"status":"ATTRIBUTED"})
 if isinstance(r,dict) and "_error" in r:return jsonify(error="Could not attribute referral."),400
 return jsonify(ok=True)

def qualify_referral_for_order(token,user_id,order_number):
 attrs=supabase_request("referral_attributions?referred_customer_id=eq."+user_id+"&status=eq.ATTRIBUTED&select=*&limit=1",token=token)
 if not isinstance(attrs,list) or not attrs:return
 x=attrs[0];reward=float(os.environ.get("REFERRAL_REWARD_AMOUNT","100"))
 supabase_request("referral_attributions?id=eq."+x["id"],method="PATCH",body={"status":"QUALIFIED","qualified_order_number":order_number,"reward_amount":reward},token=token)
 refs=supabase_request("referrals?referrer_id=eq."+x["referrer_id"]+"&select=reward_balance,successful_referrals&limit=1",token=token)
 if isinstance(refs,list) and refs:
  supabase_request("referrals?referrer_id=eq."+x["referrer_id"],method="PATCH",body={"reward_balance":float(refs[0].get("reward_balance") or 0)+reward,"successful_referrals":int(refs[0].get("successful_referrals") or 0)+1},token=token)

@app.get("/api/my-referral")
def my_referral():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 uid=get_user_id(token)
 if not uid:return jsonify(error="Unauthorized"),401
 rows=supabase_request("referrals?referrer_id=eq."+uid+"&select=*&limit=1",token=token)
 if not rows:
  code="DREAM"+uuid.uuid4().hex[:8].upper()
  r=supabase_request("referrals",method="POST",body={"referrer_id":uid,"code":code},token=token,prefer="return=representation")
  rows=r if isinstance(r,list) else []
 return jsonify(rows[0] if rows else {"code":None})

@app.get("/api/referral/<code>")
def referral_lookup(code):
 rows=supabase_request("referrals?code=eq."+code.upper()+"&select=code,referrer_id")
 return jsonify(valid=bool(rows),code=code.upper())

@app.get("/api/gallery")
def public_gallery():
 rows=supabase_request("order_reviews?gallery_permission=eq.true&select=order_number,rating,review,created_at&order=created_at.desc&limit=100")
 items=[]
 for r in rows if isinstance(rows,list) else []:
  qc=supabase_request("order_qc?order_number=eq."+r["order_number"]+"&qc_status=eq.APPROVED&select=final_photo_url&limit=1")
  if isinstance(qc,list) and qc and qc[0].get("final_photo_url"):
   items.append({"order_number":r["order_number"],"rating":r["rating"],"review":r.get("review"),"created_at":r.get("created_at"),"photo_url":qc[0]["final_photo_url"]})
 return jsonify(items)

@app.post("/api/my-orders/<order_number>/review")
def submit_review(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 rating=int(b.get("rating",0))
 if rating<1 or rating>5:return jsonify(error="Rating must be 1 to 5."),400
 row={"order_number":order_number,"rating":rating,"review":str(b.get("review",""))[:2000],"gallery_permission":bool(b.get("gallery_permission"))}
 existing=supabase_request("order_reviews?order_number=eq."+order_number+"&select=id",token=token)
 r=supabase_request("order_reviews?order_number=eq."+order_number,method="PATCH",body=row,token=token,prefer="return=representation") if isinstance(existing,list) and existing else supabase_request("order_reviews",method="POST",body=row,token=token,prefer="return=representation")
 if isinstance(r,dict) and "_error" in r:return jsonify(error="Could not save review.",details=r["_error"]),400
 return jsonify(ok=True)

@app.get("/api/my-orders/<order_number>/review")
def get_review(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 return jsonify(supabase_request("order_reviews?order_number=eq."+order_number+"&select=rating,review,gallery_permission,created_at&limit=1",token=token))

@app.get("/api/my-orders/<order_number>/completion-proof")
def completion_proof(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 qc=supabase_request("order_qc?order_number=eq."+order_number+"&select=final_photo_url,qc_status,notes,updated_at&limit=1",token=token)
 if not isinstance(qc,list) or not qc:return jsonify(available=False)
 q=qc[0]
 return jsonify(available=bool(q.get("final_photo_url") and q.get("qc_status")=="APPROVED"),photo_url=q.get("final_photo_url"),qc_status=q.get("qc_status"),completed_at=q.get("updated_at"))

@app.get("/api/my-orders/<order_number>/tracking")
def customer_tracking(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 rows=supabase_request("orders?order_number=eq."+order_number+"&select=*",token=auth.split(" ",1)[1])
 if not isinstance(rows,list) or not rows:return jsonify(error="Order not found."),404
 o=rows[0]
 return jsonify(order_number=o.get("order_number"),status=o.get("status"),courier=o.get("courier"),tracking_number=o.get("tracking_number"),timeline=order_timeline(o.get("status")))

@app.get("/api/my-orders")
def my_orders():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(supabase_request("orders?select=*&order=created_at.desc",token=auth.split(" ",1)[1]))

@app.post("/api/my-orders")
def create_my_order():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "): return jsonify(error="Please login before creating an order."),401
 token=auth.split(" ",1)[1]
 b=request.get_json(silent=True) or {}
 uid=b.get("user_id")
 if not uid: return jsonify(error="Missing customer account."),400
 oid="DA-"+time.strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:6].upper()
 order={"order_number":oid,"user_id":uid,"status":"NEW_REQUEST","artwork_shape":b.get("shape") or "Custom","artwork_width_mm":b.get("width_mm"),"artwork_height_mm":b.get("height_mm"),"anchor_nails":b.get("anchor_nails"),"string_lines":b.get("string_lines"),"notes":b.get("notes","")}
 result=supabase_request("orders",method="POST",body=order,token=token)
 if isinstance(result,dict) and "_error" in result: return jsonify(error="Could not save order.",details=result["_error"]),400
 return jsonify(ok=True,orderNumber=oid,status="NEW_REQUEST")

@app.post("/api/my-orders-with-photo")
def create_my_order_with_photo():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "): return jsonify(error="Please login before creating an order."),401
 token=auth.split(" ",1)[1]
 f=request.files.get("photo")
 if not f or not f.filename: return jsonify(error="A reference photo is required."),400
 ext=f.filename.rsplit(".",1)[-1].lower() if "." in f.filename else ""
 if ext not in ALLOWED: return jsonify(error="Use JPG, PNG or WEBP."),400
 uid=request.form.get("user_id","").strip()
 if not uid: return jsonify(error="Missing customer account."),400
 shape=request.form.get("shape","Custom"); width=request.form.get("width_mm",type=int); height=request.form.get("height_mm",type=int)
 if not width or not height: return jsonify(error="Valid dimensions are required."),400
 oid="DA-"+time.strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:6].upper()
 safe=secure_filename(f.filename)
 storage_path=f"{uid}/{oid}/{uuid.uuid4().hex}.{ext}"
 raw=f.read()
 base=os.environ.get("SUPABASE_URL","").rstrip("/")
 key=os.environ.get("SUPABASE_PUBLISHABLE_KEY","")
 headers={"apikey":key,"Authorization":"Bearer "+token,"Content-Type":f.mimetype or "application/octet-stream","x-upsert":"false"}
 try:
  req=Request(base+"/storage/v1/object/artwork-uploads/"+storage_path,data=raw,headers=headers,method="POST")
  with urlopen(req,timeout=30): pass
 except HTTPError as e:
  return jsonify(error="Photo upload failed.",details=e.read().decode()),400
 except Exception as e:
  return jsonify(error="Photo upload failed: "+str(e)),500
 order={"order_number":oid,"user_id":uid,"status":"NEW_REQUEST","artwork_shape":shape,"artwork_width_mm":width,"artwork_height_mm":height,"anchor_nails":request.form.get("anchor_nails",type=int),"string_lines":request.form.get("string_lines",type=int),"notes":request.form.get("notes","")}
 result=supabase_request("orders",method="POST",body=order,token=token,prefer="return=representation")
 if isinstance(result,dict) and "_error" in result: return jsonify(error="Could not save order.",details=result["_error"]),400
 order_row=result[0] if isinstance(result,list) and result else None
 if not order_row: return jsonify(error="Order saved but could not link photo."),500
 file_row={"order_id":order_row["id"],"file_type":"original","storage_path":storage_path}
 file_result=supabase_request("artwork_files",method="POST",body=file_row,token=token)
 if isinstance(file_result,dict) and "_error" in file_result:
  return jsonify(error="Order created but photo metadata could not be linked.",details=file_result["_error"]),400
 return jsonify(ok=True,orderNumber=oid,status="NEW_REQUEST",photoPath=storage_path)

@app.post("/api/payments/create-order")
def create_payment_order():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Please login before payment."),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {};order_no=b.get("order_number","")
 rows=supabase_request("orders?order_number=eq."+order_no+"&select=*",token=token)
 if not isinstance(rows,list) or not rows:return jsonify(error="Order not found."),404
 o=rows[0]
 if o.get("status")!="PAYMENT_PENDING":return jsonify(error="This order is not ready for payment."),400
 total=float(o.get("price") or 0)+float(o.get("shipping_cost") or 0)
 if total<=0:return jsonify(error="Quote amount has not been set yet."),400
 rp=razorpay_request("orders",{"amount":round(total*100),"currency":"INR","receipt":order_no,"notes":{"dreamarts_order":order_no}})
 return jsonify(ok=True,key_id=os.environ.get("RAZORPAY_KEY_ID"),razorpay_order_id=rp["id"],amount=round(total*100),currency="INR",order_number=order_no)

@app.post("/api/payments/verify")
def verify_payment():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 payload=b.get("razorpay_order_id","")+"|"+b.get("razorpay_payment_id","")
 expected=hmac.new(os.environ.get("RAZORPAY_KEY_SECRET","").encode(),payload.encode(),hashlib.sha256).hexdigest()
 if not hmac.compare_digest(expected,b.get("razorpay_signature","")):return jsonify(error="Payment verification failed."),400
 order_no=b.get("order_number","")
 result=supabase_request("orders?order_number=eq."+order_no,method="PATCH",body={"status":"PAID","admin_notes":"Razorpay payment verified: "+b.get("razorpay_payment_id","")},token=token,prefer="return=representation")
 if isinstance(result,dict) and "_error" in result:return jsonify(error="Payment recorded but order update failed."),500
 return jsonify(ok=True,status="PAID")

@app.post("/api/payments/webhook")
def razorpay_webhook():
 secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET","")
 raw=request.get_data();sig=request.headers.get("X-Razorpay-Signature","")
 if secret and not hmac.compare_digest(hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest(),sig):return "Invalid signature",400
 try:data=json.loads(raw.decode())
 except:return "Bad payload",400
 if data.get("event")=="payment.captured":
  p=data.get("payload",{}).get("payment",{}).get("entity",{});order_no=(p.get("notes") or {}).get("dreamarts_order")
  if order_no:supabase_request("orders?order_number=eq."+order_no,method="PATCH",body={"status":"PAID","admin_notes":"Razorpay webhook captured: "+p.get("id","")},prefer="return=representation")
 return "ok",200

@app.patch("/api/my-orders/<order_id>/customer-approve")
def customer_approve_order(order_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 result=supabase_request("orders?order_number=eq."+order_id,method="PATCH",body={"status":"PAYMENT_PENDING"},token=token,prefer="return=representation")
 if isinstance(result,dict) and "_error" in result:return jsonify(error="Could not approve order.",details=result["_error"]),400
 return jsonify(ok=True,status="PAYMENT_PENDING")

@app.post("/api/studio/checkout")
def studio_checkout():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Please login before checkout."),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 uid=b.get("user_id");sid=b.get("session_id")
 if not uid or not sid:return jsonify(error="Missing artwork session."),400
 session=supabase_request("studio_sessions?id=eq."+sid+"&select=*",token=token)
 if not isinstance(session,list) or not session:return jsonify(error="Saved artwork session not found."),404
 s=session[0];c=s.get("config") or {};vs=s.get("variants") or [];idx=int(s.get("selected_variant") or 0);chosen=vs[idx] if idx<len(vs) else {}
 oid="DA-"+time.strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:6].upper()
 profiles=supabase_request("profiles?id=eq."+uid+"&select=full_name,phone,address_line1,address_line2,city,state,postal_code,country",token=token)
 p=profiles[0] if isinstance(profiles,list) and profiles else {}
 shipping={"full_name":p.get("full_name"),"phone":p.get("phone"),"address_line1":p.get("address_line1"),"address_line2":p.get("address_line2"),"city":p.get("city"),"state":p.get("state"),"postal_code":p.get("postal_code"),"country":p.get("country")}
 order={"order_number":oid,"user_id":uid,"status":"AWAITING_APPROVAL","artwork_shape":c.get("shape","Custom"),"artwork_width_mm":c.get("width_mm"),"artwork_height_mm":c.get("height_mm"),"anchor_nails":c.get("anchor_nails") or c.get("nails"),"string_lines":c.get("string_lines") or c.get("lines"),"shipping_address":shipping,"notes":json.dumps({"studio_session_id":sid,"selected_engine":chosen.get("engine") or c.get("selected_engine"),"selected_variant":chosen.get("name") or c.get("selected_variant"),"artwork_focus":c.get("artwork_focus") or c.get("focus"),"thread_tone":c.get("thread_tone") or c.get("tone")})}
 result=supabase_request("orders",method="POST",body=order,token=token,prefer="return=representation")
 if isinstance(result,dict) and "_error" in result:return jsonify(error="Could not create production order.",details=result["_error"]),400
 return jsonify(ok=True,orderNumber=oid,status="AWAITING_APPROVAL")

@app.post("/api/studio/sessions")
def save_studio_session():
 try:
  b=request.get_json(silent=True) or {}
  sid=b.get("session_id") or str(uuid.uuid4())
  payload={"id":sid,"user_id":b.get("user_id"),"config":b.get("config",{}),"variants":b.get("variants",[]),"selected_variant":b.get("selected_variant",0),"updated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
  result=supabase_request("studio_sessions",method="POST",body=payload,prefer="resolution=merge-duplicates,return=representation")
  if isinstance(result,dict) and "_error" in result:return jsonify(error="Could not persist studio session.",details=result["_error"]),400
  return jsonify(ok=True,session_id=sid)
 except Exception as e:return jsonify(error=str(e)),500

@app.get("/api/my-studio-sessions")
def my_studio_sessions():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 return jsonify(supabase_request("studio_sessions?select=*&order=updated_at.desc",token=token))

@app.get("/api/studio/sessions/<session_id>")
def get_studio_session(session_id):
 result=supabase_request("studio_sessions?id=eq."+session_id+"&select=*")
 if isinstance(result,dict) and "_error" in result:return jsonify(error="Could not load studio session."),404
 if not result:return jsonify(error="Studio session not found."),404
 return jsonify(ok=True,session=result[0])

@app.post("/api/studio/generate")
def studio_generate():
 try:
  b=request.get_json(silent=True) or {}
  image=b.get("image")
  if not image: return jsonify(error="Studio image is required."),400
  focus=b.get("focus","Auto Select")
  engine_mode=b.get("engine","auto")
  if focus=="Full Image": engine_mode="dreamarts_adaptive"
  elif focus=="Portrait Focus": engine_mode="portrait_aware"
  elif focus=="Subject Only": engine_mode="subject_focus"
  if engine_mode=="auto":
   result, benchmark_results=benchmark_string_art(image, b.get("shape","Circle"), b.get("nails",600), b.get("lines",4000), float(b.get("contrast",90))/100.0, b.get("tone","black"))
   result["benchmark"]=benchmark_results
  else:
   result=generate_string_art(
   image_data=image,
   shape=b.get("shape","Circle"),
   nails=b.get("nails",600),
   lines=b.get("lines",4000),
   contrast=float(b.get("contrast",90))/100.0,
   tone=b.get("tone","black"),
   preview=True, engine=engine_mode
   )
  return jsonify(ok=True,**result)
 except Exception as e:
  return jsonify(error="Preview generation failed: "+str(e)),500

@app.get("/api/public-config")
def public_config():
 return jsonify(
  supabaseUrl=os.environ.get("SUPABASE_URL",""),
  supabasePublishableKey=os.environ.get("SUPABASE_PUBLISHABLE_KEY","")
 )

@app.get("/health")
def health(): return jsonify(ok=True,service="dreamarts")

@app.get("/api/dashboard")
def dashboard():
 orders=load_orders();counts={}
 for o in orders:counts[o.get("status","UNKNOWN")]=counts.get(o.get("status","UNKNOWN"),0)+1
 return jsonify(total=len(orders),counts=counts,recent=orders[:20],flow=FLOW)

if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
