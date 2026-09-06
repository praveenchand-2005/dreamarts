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
 # Blend historical recommendation quality with actual business outcome trust.
 outcome_rows=supabase_request("agent_tasks?select=agent,outcome_learning&limit=5000",token=token);outcome_rows=outcome_rows if isinstance(outcome_rows,list) else []
 outcome_scores={}
 for row in outcome_rows:
  raw=row.get("outcome_learning")
  if not raw:continue
  try:event=json.loads(raw) if isinstance(raw,str) else raw
  except:continue
  outcome_scores.setdefault(row.get("agent","Unknown Agent"),[]).append(event.get("outcome_score",50))
 outcome_trust={k:sum(v[-10:])/len(v[-10:])/100 for k,v in outcome_scores.items() if v}
 penalties={x["agent"]:min(0.35,x["quality_drop"]/100) for x in drift.get("alerts",[])}
 decisions=[]
 for x in ranked:
  perf=agent_perf.get(x.get("agent"),{})
  q=quality.get(x.get("agent"),{})
  quality_trust=q.get("quality_score",50)/100
  actual_outcome_trust=outcome_trust.get(x.get("agent"))
  base_trust=(quality_trust*0.6+actual_outcome_trust*0.4) if actual_outcome_trust is not None else quality_trust
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
  decisions.append({**x,"quality_trust_score":round(quality_trust*100,1),"outcome_trust_score":round(actual_outcome_trust*100,1) if actual_outcome_trust is not None else None,"base_trust_score":round(base_trust*100,1),"calibration_penalty":round(penalty*100,1),"recovery_credit":round(recovery*100,1),"effective_penalty":round(effective_penalty*100,1),"trust_score":round(trust*100,1),"decision_score":score,"confidence":round(confidence,2),"decision":"ACT NOW" if score>=80 else "REVIEW NEXT" if score>=60 else "MONITOR"})
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

@app.get("/api/admin/business-anomalies")
def business_anomalies_api():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=status,created_at,amount,price,payment_status&limit=3000",token=token);orders=orders if isinstance(orders,list) else []
 now=datetime.datetime.utcnow();an=[];today=[];previous=[];aging=0
 for o in orders:
  try:
   ts=datetime.datetime.fromisoformat(str(o.get("created_at")).replace("Z","+00:00")).replace(tzinfo=None);age=(now-ts).total_seconds()/3600
   if age<=24:today.append(o)
   elif age<=48:previous.append(o)
   if age>=72 and str(o.get("status","")).upper() not in ("COMPLETED","DELIVERED","CANCELLED"):aging+=1
  except:pass
 if len(previous)>=3:
  change=round((len(today)-len(previous))/len(previous)*100,1)
  if change<=-30:an.append({"type":"ORDER_VOLUME_DROP","severity":"HIGH","message":f"Order volume dropped {abs(change)}% compared with the previous 24-hour period.","value":change})
 paid_today=sum(float(o.get("amount") or o.get("price") or 0) for o in today if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS"))
 paid_prev=sum(float(o.get("amount") or o.get("price") or 0) for o in previous if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS"))
 if paid_prev>0:
  change=round((paid_today-paid_prev)/paid_prev*100,1)
  if change<=-30:an.append({"type":"REVENUE_DROP","severity":"HIGH","message":f"Paid revenue dropped {abs(change)}% versus the previous 24-hour period.","value":change})
 if aging>=3:an.append({"type":"ORDER_AGING","severity":"HIGH" if aging>=10 else "MEDIUM","message":f"{aging} orders have remained open for more than 72 hours.","value":aging})
 return jsonify(ok=True,count=len(an),anomalies=an,generated_at=now.isoformat()+"Z")

@app.post("/api/admin/business-anomalies/dispatch")
def dispatch_business_anomalies():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 # Reuse anomaly engine through current request context.
 resp=business_anomalies_api()
 data=resp.get_json() if hasattr(resp,"get_json") else {}
 anomalies=data.get("anomalies",[]);created=[]
 mapping={"ORDER_VOLUME_DROP":"Sales Intelligence Agent","REVENUE_DROP":"Finance Intelligence Agent","ORDER_AGING":"Operations Agent"}
 for x in anomalies:
  agent=mapping.get(x.get("type"),"Operations Agent")
  task={"agent":agent,"title":"Investigate "+x.get("type","BUSINESS_ANOMALY").replace("_"," ").title(),"description":x.get("message"),"priority":x.get("severity","MEDIUM"),"status":"PENDING","source":"BUSINESS_ANOMALY","created_at":datetime.datetime.utcnow().isoformat()+"Z","updated_at":datetime.datetime.utcnow().isoformat()+"Z"}
  r=supabase_request("agent_tasks",method="POST",body=task,token=token)
  created.append({"agent":agent,"anomaly":x.get("type"),"result":r})
 return jsonify(ok=True,anomalies=len(anomalies),tasks_dispatched=len(created),created=created)

@app.post("/api/admin/agent-tasks/<task_id>/investigate")
def investigate_agent_task(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 orders=supabase_request("orders?select=status,created_at,amount,price,payment_status&limit=3000",token=token);orders=orders if isinstance(orders,list) else []
 now=datetime.datetime.utcnow();open_orders=sum(1 for o in orders if str(o.get("status","")).upper() not in ("COMPLETED","DELIVERED","CANCELLED"))
 paid=sum(1 for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS"))
 revenue=sum(float(o.get("amount") or o.get("price") or 0) for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS"))
 typ=(task.get("title") or "").upper()
 causes=[]
 actions=[]
 if "AGING" in typ:causes=["Fulfillment backlog or unresolved operational workflow.","Orders remaining in non-terminal statuses." ];actions=["Review oldest open orders first.","Assign fulfillment ownership and escalation deadlines."]
 elif "REVENUE" in typ:causes=["Reduced paid order conversion or transaction value.","Possible payment or sales funnel degradation."];actions=["Compare traffic, checkout and payment success rates.","Review recent campaign and pricing changes."]
 else:causes=["Demand or sales pipeline slowdown.","Possible acquisition, conversion or availability issue."];actions=["Compare acquisition sources across periods.","Review product availability and conversion funnel."]
 report={"task_id":task_id,"agent":task.get("agent"),"investigated_at":now.isoformat()+"Z","business_snapshot":{"orders":len(orders),"open_orders":open_orders,"paid_orders":paid,"paid_revenue":round(revenue,2)},"probable_causes":causes,"recommended_actions":actions,"confidence":0.72}
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"INVESTIGATED","investigation_report":json.dumps(report),"updated_at":now.isoformat()+"Z"},token=token)
 return jsonify(ok=True,report=report)

@app.post("/api/admin/agent-tasks/<task_id>/recommend")
def recommend_from_investigation(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token)
 task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 raw=task.get("investigation_report")
 if not raw:return jsonify(error="Task must be investigated first"),400
 try:report=json.loads(raw) if isinstance(raw,str) else raw
 except:return jsonify(error="Invalid investigation report"),400
 actions=report.get("recommended_actions",[]);priority=task.get("priority","MEDIUM")
 impact={"CRITICAL":"Very high operational/business impact","HIGH":"High business impact","MEDIUM":"Moderate business impact"}.get(priority,"Limited business impact")
 recommendation={"action":actions[0] if actions else "Review investigation findings.","expected_impact":impact,"risk_level":priority,"confidence":report.get("confidence",0.65),"requires_founder_decision":True,"generated_at":datetime.datetime.utcnow().isoformat()+"Z"}
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"RECOMMENDED","recommendation":json.dumps(recommendation),"updated_at":recommendation["generated_at"]},token=token)
 return jsonify(ok=True,task_id=task_id,recommendation=recommendation)

@app.post("/api/admin/business-intelligence/run")
def run_business_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];resp=business_anomalies_api();data=resp.get_json() if hasattr(resp,"get_json") else {};anomalies=data.get("anomalies",[])
 mapping={"ORDER_VOLUME_DROP":"Sales Intelligence Agent","REVENUE_DROP":"Finance Intelligence Agent","ORDER_AGING":"Operations Agent"};results=[]
 for x in anomalies:
  agent=mapping.get(x.get("type"),"Operations Agent");now=datetime.datetime.utcnow().isoformat()+"Z"
  task={"agent":agent,"title":"Investigate "+x.get("type","BUSINESS_ANOMALY").replace("_"," ").title(),"description":x.get("message"),"priority":x.get("severity","MEDIUM"),"status":"RECOMMENDED","source":"BUSINESS_ANOMALY","created_at":now,"updated_at":now}
  orders=supabase_request("orders?select=status,amount,price,payment_status&limit=3000",token=token);orders=orders if isinstance(orders,list) else []
  paid=sum(1 for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS"));revenue=sum(float(o.get("amount") or o.get("price") or 0) for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS"))
  typ=x.get("type","");actions={"ORDER_AGING":"Review and assign the oldest open orders immediately.","REVENUE_DROP":"Review conversion, payments and recent sales changes.","ORDER_VOLUME_DROP":"Investigate acquisition sources and conversion funnel changes."}
  report={"business_snapshot":{"total_orders":len(orders),"paid_orders":paid,"paid_revenue":round(revenue,2)},"probable_causes":[x.get("message")],"confidence":0.72}
  rec={"action":actions.get(typ,"Review business anomaly."),"expected_impact":"High business impact" if x.get("severity")=="HIGH" else "Moderate business impact","risk_level":x.get("severity"),"confidence":0.72,"requires_founder_decision":True,"generated_at":now}
  task["investigation_report"]=json.dumps(report);task["recommendation"]=json.dumps(rec)
  r=supabase_request("agent_tasks",method="POST",body=task,token=token);results.append({"anomaly":typ,"agent":agent,"created":bool(r)})
 return jsonify(ok=True,anomalies_detected=len(anomalies),recommendations_created=len(results),results=results)

@app.post("/api/admin/agent-tasks/<task_id>/outcome-baseline")
def capture_outcome_baseline(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 orders=supabase_request("orders?select=status,amount,price,payment_status&limit=3000",token=token);orders=orders if isinstance(orders,list) else []
 paid=[o for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS")]
 baseline={"captured_at":datetime.datetime.utcnow().isoformat()+"Z","total_orders":len(orders),"paid_orders":len(paid),"paid_revenue":round(sum(float(o.get("amount") or o.get("price") or 0) for o in paid),2),"open_orders":sum(1 for o in orders if str(o.get("status","")).upper() not in ("COMPLETED","DELIVERED","CANCELLED"))}
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"outcome_baseline":json.dumps(baseline),"updated_at":baseline["captured_at"]},token=token)
 return jsonify(ok=True,baseline=baseline)

@app.post("/api/admin/agent-tasks/<task_id>/measure-outcome")
def measure_outcome(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 raw=task.get("outcome_baseline")
 if not raw:return jsonify(error="Capture baseline before measuring outcome"),400
 try:base=json.loads(raw) if isinstance(raw,str) else raw
 except:return jsonify(error="Invalid baseline"),400
 orders=supabase_request("orders?select=status,amount,price,payment_status&limit=3000",token=token);orders=orders if isinstance(orders,list) else []
 paid=[o for o in orders if str(o.get("payment_status","")).upper() in ("PAID","CAPTURED","SUCCESS")]
 current={"total_orders":len(orders),"paid_orders":len(paid),"paid_revenue":round(sum(float(o.get("amount") or o.get("price") or 0) for o in paid),2),"open_orders":sum(1 for o in orders if str(o.get("status","")).upper() not in ("COMPLETED","DELIVERED","CANCELLED"))}
 delta={k:round(current[k]-base.get(k,0),2) for k in current};roi_signal=round((delta["paid_revenue"]/base["paid_revenue"]*100),2) if base.get("paid_revenue") else None
 outcome={"measured_at":datetime.datetime.utcnow().isoformat()+"Z","baseline":base,"current":current,"delta":delta,"revenue_change_percent":roi_signal,"assessment":"IMPROVED" if (roi_signal is not None and roi_signal>0) or delta["open_orders"]<0 else "NO_CLEAR_IMPROVEMENT"}
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"outcome_measurement":json.dumps(outcome),"updated_at":outcome["measured_at"]},token=token)
 return jsonify(ok=True,outcome=outcome)

@app.post("/api/admin/agent-tasks/<task_id>/learn-outcome")
def learn_from_outcome(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 raw=task.get("outcome_measurement")
 if not raw:return jsonify(error="Measure outcome first"),400
 try:out=json.loads(raw) if isinstance(raw,str) else raw
 except:return jsonify(error="Invalid outcome"),400
 agent=task.get("agent","Unknown Agent");improved=out.get("assessment")=="IMPROVED";score=100 if improved else 25
 event={"agent":agent,"task_id":task_id,"outcome_score":score,"assessment":out.get("assessment"),"recorded_at":datetime.datetime.utcnow().isoformat()+"Z"}
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"outcome_learning":json.dumps(event),"updated_at":event["recorded_at"]},token=token)
 # Return an explicit learning signal; recommendation-quality engines can consume this task history.
 return jsonify(ok=True,learning_event=event,trust_signal={"agent":agent,"direction":"UP" if improved else "DOWN","strength":"HIGH" if score in (100,25) else "MEDIUM"})

@app.get("/api/admin/agent-outcome-trust")
def agent_outcome_trust():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 tasks=supabase_request("agent_tasks?select=agent,outcome_learning,outcome_measurement,updated_at&limit=5000",token=token);tasks=tasks if isinstance(tasks,list) else []
 grouped={}
 for t in tasks:
  raw=t.get("outcome_learning")
  if not raw:continue
  try:e=json.loads(raw) if isinstance(raw,str) else raw
  except:continue
  agent=t.get("agent","Unknown Agent");grouped.setdefault(agent,[]).append(e.get("outcome_score",50))
 result=[]
 for agent,scores in grouped.items():
  recent=scores[-10:];success=sum(1 for x in recent if x>=75);trust=round(sum(recent)/len(recent),1)
  result.append({"agent":agent,"outcomes":len(scores),"recent_outcomes":len(recent),"success_rate":round(success/len(recent)*100,1),"outcome_trust_score":trust,"level":"HIGH" if trust>=80 else "MEDIUM" if trust>=55 else "LOW"})
 return jsonify(ok=True,agents=sorted(result,key=lambda x:x["outcome_trust_score"],reverse=True))

@app.post("/api/admin/agent-outcome-trust/recalculate")
def recalculate_outcome_trust():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 data=agent_outcome_trust().get_json();agents=data.get("agents",[])
 now=datetime.datetime.utcnow().isoformat()+"Z"
 for x in agents:
  supabase_request("agent_tasks?agent=eq."+x["agent"],method="PATCH",body={"outcome_trust_snapshot":json.dumps({"score":x["outcome_trust_score"],"level":x["level"],"calculated_at":now})},token=token)
 return jsonify(ok=True,recalculated=len(agents),agents=agents,calculated_at=now)

@app.post("/api/admin/agent-tasks/<task_id>/authorize-execution")
def authorize_agent_execution(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 if task.get("status") not in ("APPROVED","RECOMMENDED","EXECUTION_AUTHORIZED"):return jsonify(error="Task is not eligible for execution authorization"),400
 now=datetime.datetime.utcnow().isoformat()+"Z";execution={"authorized_at":now,"authorized":True,"status":"EXECUTION_AUTHORIZED","agent":task.get("agent"),"action":None}
 raw=task.get("recommendation")
 try:rec=json.loads(raw) if isinstance(raw,str) else (raw or {})
 except:rec={}
 execution["action"]=rec.get("action")
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"EXECUTION_AUTHORIZED","execution_authorization":json.dumps(execution),"updated_at":now},token=token)
 return jsonify(ok=True,execution=execution)

@app.post("/api/admin/agent-tasks/<task_id>/execute")
def execute_authorized_task(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 if task.get("status")!="EXECUTION_AUTHORIZED":return jsonify(error="Founder authorization required before execution"),403
 raw=task.get("execution_authorization")
 try:authz=json.loads(raw) if isinstance(raw,str) else raw
 except:authz={}
 now=datetime.datetime.utcnow().isoformat()+"Z";log={"executed_at":now,"agent":task.get("agent"),"action":authz.get("action"),"result":"EXECUTION_RECORDED","mode":"CONTROLLED"}
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"EXECUTED","execution_log":json.dumps(log),"updated_at":now},token=token)
 return jsonify(ok=True,execution=log)

EXECUTION_ACTION_REGISTRY={
 "Operations Agent":{"ORDER_ESCALATION":{"description":"Create controlled operational escalation","risk":"MEDIUM"},"ORDER_REVIEW":{"description":"Flag order for operational review","risk":"LOW"}},
 "Sales Intelligence Agent":{"LEAD_FOLLOW_UP":{"description":"Create controlled sales follow-up","risk":"LOW"},"SALES_REVIEW":{"description":"Flag sales opportunity for review","risk":"LOW"}},
 "Finance Intelligence Agent":{"PAYMENT_REVIEW":{"description":"Create controlled payment investigation","risk":"MEDIUM"},"FINANCE_REVIEW":{"description":"Create finance review task","risk":"MEDIUM"}}
}

@app.get("/api/admin/execution-actions")
def execution_actions():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,registry=EXECUTION_ACTION_REGISTRY)

@app.post("/api/admin/agent-tasks/<task_id>/execute-action")
def execute_registered_action(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {};action_type=body.get("action_type")
 rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 if task.get("status")!="EXECUTION_AUTHORIZED":return jsonify(error="Founder authorization required"),403
 agent=task.get("agent");spec=EXECUTION_ACTION_REGISTRY.get(agent,{}).get(action_type)
 if not spec:return jsonify(error="Action is not registered for this agent"),403
 now=datetime.datetime.utcnow().isoformat()+"Z";record={"action_type":action_type,"description":spec["description"],"risk":spec["risk"],"executed_at":now,"result":"CONTROLLED_ACTION_RECORDED"}
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"EXECUTED","execution_log":json.dumps(record),"updated_at":now},token=token)
 return jsonify(ok=True,action=record)

@app.post("/api/admin/agent-tasks/<task_id>/execute-real-action")
def execute_real_registered_action(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {};action_type=body.get("action_type");payload=body.get("payload",{})
 rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 if task.get("status")!="EXECUTION_AUTHORIZED":return jsonify(error="Founder authorization required"),403
 agent=task.get("agent");spec=EXECUTION_ACTION_REGISTRY.get(agent,{}).get(action_type)
 if not spec:return jsonify(error="Action is not registered for this agent"),403
 now=datetime.datetime.utcnow().isoformat()+"Z";result={"action_type":action_type,"mode":"REAL_CONTROLLED_EXECUTION","executed_at":now}
 if action_type=="ORDER_REVIEW":
  oid=payload.get("order_id")
  if not oid:return jsonify(error="order_id required"),400
  r=supabase_request("orders?id=eq."+str(oid),method="PATCH",body={"status":"UNDER_REVIEW"},token=token);result["target_order_id"]=oid;result["operation"]="order status changed to UNDER_REVIEW";result["response"]=r
 elif action_type=="ORDER_ESCALATION":
  oid=payload.get("order_id")
  if not oid:return jsonify(error="order_id required"),400
  escalation={"order_id":oid,"task_id":task_id,"agent":agent,"reason":payload.get("reason",task.get("description")),"priority":task.get("priority","MEDIUM"),"status":"OPEN","created_at":now}
  r=supabase_request("operational_escalations",method="POST",body=escalation,token=token);result["target_order_id"]=oid;result["operation"]="operational escalation created";result["response"]=r
 else:
  follow={"source_task_id":task_id,"agent":agent,"action_type":action_type,"title":spec["description"],"status":"OPEN","created_at":now}
  r=supabase_request("agent_tasks",method="POST",body=follow,token=token);result["operation"]="controlled follow-up task created";result["response"]=r
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"EXECUTED","execution_log":json.dumps(result),"updated_at":now},token=token)
 return jsonify(ok=True,execution=result)

@app.get("/api/admin/execution-audit")
def execution_audit():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?select=id,agent,title,priority,status,execution_authorization,execution_log,outcome_measurement,updated_at&limit=5000",token=token);rows=rows if isinstance(rows,list) else []
 events=[]
 for t in rows:
  raw=t.get("execution_log")
  if not raw:continue
  try:log=json.loads(raw) if isinstance(raw,str) else raw
  except:log={}
  events.append({"task_id":t.get("id"),"agent":t.get("agent"),"title":t.get("title"),"priority":t.get("priority"),"status":t.get("status"),"action":log.get("action_type") or log.get("action"),"mode":log.get("mode","CONTROLLED"),"result":log.get("result") or log.get("operation"),"executed_at":log.get("executed_at"),"has_outcome":bool(t.get("outcome_measurement"))})
 events.sort(key=lambda x:x.get("executed_at") or "",reverse=True)
 summary={"total_executions":len(events),"real_executions":sum(1 for x in events if x["mode"]=="REAL_CONTROLLED_EXECUTION"),"with_measured_outcomes":sum(1 for x in events if x["has_outcome"])}
 return jsonify(ok=True,summary=summary,events=events)

@app.get("/api/admin/execution-audit/<task_id>")
def execution_audit_detail(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token)
 if not isinstance(rows,list) or not rows:return jsonify(error="Task not found"),404
 t=rows[0];parsed={}
 for k in ("execution_authorization","execution_log","outcome_baseline","outcome_measurement","outcome_learning","recommendation"):
  raw=t.get(k)
  if raw:
   try:parsed[k]=json.loads(raw) if isinstance(raw,str) else raw
   except:parsed[k]=raw
 return jsonify(ok=True,task_id=task_id,audit=parsed)

AGENT_AUTONOMY_LEVELS={"L0":{"name":"Observe","max_risk":"NONE"},"L1":{"name":"Analyze","max_risk":"NONE"},"L2":{"name":"Recommend","max_risk":"NONE"},"L3":{"name":"Low-risk autonomous execution","max_risk":"LOW"},"L4":{"name":"Policy-controlled execution","max_risk":"MEDIUM"},"L5":{"name":"Strategic execution with founder approval","max_risk":"HIGH"}}
RISK_RANK={"NONE":0,"LOW":1,"MEDIUM":2,"HIGH":3,"CRITICAL":4}

@app.get("/api/admin/agent-autonomy")
def agent_autonomy():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("agent_tasks?select=agent,autonomy_level,outcome_learning&limit=5000",token=token);rows=rows if isinstance(rows,list) else []
 agents={}
 for r in rows:
  ag=r.get("agent")
  if not ag:continue
  agents.setdefault(ag,{"level":r.get("autonomy_level") or "L2","scores":[]})
  raw=r.get("outcome_learning")
  if raw:
   try:e=json.loads(raw) if isinstance(raw,str) else raw;agents[ag]["scores"].append(e.get("outcome_score",50))
   except:pass
 result=[]
 for ag,v in agents.items():
  trust=round(sum(v["scores"][-10:])/len(v["scores"][-10:]),1) if v["scores"] else None
  result.append({"agent":ag,"level":v["level"],"policy":AGENT_AUTONOMY_LEVELS.get(v["level"],AGENT_AUTONOMY_LEVELS["L2"]),"outcome_trust":trust})
 return jsonify(ok=True,levels=AGENT_AUTONOMY_LEVELS,agents=result)

@app.post("/api/admin/agent-tasks/<task_id>/autonomy-execute")
def autonomy_execute(task_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {};action_type=body.get("action_type");payload=body.get("payload",{})
 rows=supabase_request("agent_tasks?id=eq."+task_id+"&select=*",token=token);task=rows[0] if isinstance(rows,list) and rows else None
 if not task:return jsonify(error="Task not found"),404
 agent=task.get("agent");level=task.get("autonomy_level") or "L2";spec=EXECUTION_ACTION_REGISTRY.get(agent,{}).get(action_type)
 if not spec:return jsonify(error="Action not permitted for agent"),403
 allowed=RISK_RANK.get(spec["risk"],9)<=RISK_RANK.get(AGENT_AUTONOMY_LEVELS[level]["max_risk"],-1)
 if not allowed:return jsonify(error="Autonomy level requires founder authorization for this action",level=level,risk=spec["risk"]),403
 task["status"]="EXECUTION_AUTHORIZED"
 now=datetime.datetime.utcnow().isoformat()+"Z"
 supabase_request("agent_tasks?id=eq."+task_id,method="PATCH",body={"status":"EXECUTION_AUTHORIZED","execution_authorization":json.dumps({"authorized_by":"AUTONOMY_POLICY","level":level,"action_type":action_type,"authorized_at":now})},token=token)
 return execute_real_registered_action(task_id)

def autonomy_level_for_trust(trust,current="L2"):
 if trust is None:return current
 if trust>=85:return "L4"
 if trust>=75:return "L3"
 if trust<45:return "L1"
 if trust<60:return "L2"
 return current

@app.post("/api/admin/agent-autonomy/review")
def review_agent_autonomy():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 rows=supabase_request("agent_tasks?select=id,agent,autonomy_level,outcome_learning&limit=5000",token=token);rows=rows if isinstance(rows,list) else []
 grouped={}
 for row in rows:
  ag=row.get("agent")
  if not ag:continue
  g=grouped.setdefault(ag,{"scores":[],"level":row.get("autonomy_level") or "L2","ids":[]})
  g["ids"].append(row.get("id"));raw=row.get("outcome_learning")
  if raw:
   try:e=json.loads(raw) if isinstance(raw,str) else raw;g["scores"].append(e.get("outcome_score",50))
   except:pass
 changes=[];now=datetime.datetime.utcnow().isoformat()+"Z"
 for ag,g in grouped.items():
  recent=g["scores"][-10:]
  if not recent:continue
  trust=round(sum(recent)/len(recent),1);new=autonomy_level_for_trust(trust,g["level"])
  if new!=g["level"]:
   for tid in g["ids"]:
    supabase_request("agent_tasks?id=eq."+tid,method="PATCH",body={"autonomy_level":new,"autonomy_review":json.dumps({"previous":g["level"],"new":new,"outcome_trust":trust,"reviewed_at":now})},token=token)
   changes.append({"agent":ag,"previous_level":g["level"],"new_level":new,"outcome_trust":trust,"reason":"Outcome-driven autonomy policy"})
 return jsonify(ok=True,reviewed=len(grouped),changes=changes,reviewed_at=now)

@app.post("/api/admin/agent-council/deliberate")
def agent_council_deliberate():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {}
 topic=body.get("topic","Business decision review");agents=body.get("agents") or ["Sales Intelligence Agent","Finance Intelligence Agent","Operations Agent"]
 context=body.get("context",{})
 views=[]
 for agent in agents:
  focus="revenue and customer impact" if "Sales" in agent else "profit, cash and financial risk" if "Finance" in agent else "execution feasibility and operational risk"
  views.append({"agent":agent,"position":"Analyze "+topic+" from "+focus,"confidence":70,"focus":focus})
 disagreements=[]
 if len(views)>1:disagreements=[{"issue":"Primary success metric","agents":agents,"resolution":"Balance growth, profitability and operational feasibility"}]
 decision={"topic":topic,"participants":agents,"context":context,"positions":views,"disagreements":disagreements,"consensus":"MULTI_AGENT_REVIEW_REQUIRED","recommendation":"Proceed only with a measurable, reversible execution plan and defined success metrics.","created_at":datetime.datetime.utcnow().isoformat()+"Z"}
 return jsonify(ok=True,council=decision)

@app.post("/api/admin/agent-council/create-task")
def agent_council_create_task():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {};council=body.get("council") or {}
 now=datetime.datetime.utcnow().isoformat()+"Z"
 task={"agent":"Executive Council","title":council.get("topic","Multi-agent strategic decision"),"description":council.get("recommendation"),"priority":body.get("priority","HIGH"),"status":"RECOMMENDED","council_deliberation":json.dumps(council),"created_at":now,"updated_at":now}
 r=supabase_request("agent_tasks",method="POST",body=task,token=token)
 return jsonify(ok=True,task=r)

COUNCIL_AGENT_ROUTING={"SALES":["Sales Intelligence Agent"],"FINANCE":["Finance Intelligence Agent"],"OPERATIONS":["Operations Agent"],"STRATEGY":["Sales Intelligence Agent","Finance Intelligence Agent","Operations Agent"]}

@app.post("/api/admin/agent-council/orchestrate")
def orchestrate_agent_council():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {}
 topic=body.get("topic","Business decision");category=str(body.get("category","STRATEGY")).upper();agents=body.get("agents") or COUNCIL_AGENT_ROUTING.get(category,COUNCIL_AGENT_ROUTING["STRATEGY"])
 evidence=body.get("evidence") or []
 investigations=[]
 for agent in agents:
  lens="customer and revenue" if "Sales" in agent else "financial impact and risk" if "Finance" in agent else "execution capacity and operational risk"
  investigations.append({"agent":agent,"lens":lens,"evidence_considered":len(evidence),"finding":"Independent review completed","confidence":70})
 challenge_round=[{"challenger":agents[i],"challenges":agents[(i+1)%len(agents)],"question":"What evidence could invalidate the proposed action?"} for i in range(len(agents))] if len(agents)>1 else []
 consensus_confidence=round(sum(x["confidence"] for x in investigations)/len(investigations),1) if investigations else 0
 recommendation={"topic":topic,"category":category,"participants":agents,"evidence":evidence,"investigations":investigations,"challenge_round":challenge_round,"consensus_confidence":consensus_confidence,"consensus_level":"HIGH" if consensus_confidence>=80 else "MEDIUM" if consensus_confidence>=60 else "LOW","recommendation":"Use a measurable, reversible plan with explicit financial and operational guardrails.","created_at":datetime.datetime.utcnow().isoformat()+"Z"}
 return jsonify(ok=True,council=recommendation)

@app.get("/api/admin/council-memory")
def council_memory():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];q=request.args.get("q","").lower()
 rows=supabase_request("agent_tasks?agent=eq.Executive%20Council&select=id,title,description,status,council_deliberation,outcome_measurement,updated_at&limit=500",token=token);rows=rows if isinstance(rows,list) else []
 memories=[]
 for r in rows:
  if q and q not in ((r.get("title") or "")+" "+(r.get("description") or "")).lower():continue
  d=r.get("council_deliberation")
  try:d=json.loads(d) if isinstance(d,str) else d
  except:d={}
  o=r.get("outcome_measurement")
  try:o=json.loads(o) if isinstance(o,str) else o
  except:o={}
  memories.append({"task_id":r.get("id"),"topic":r.get("title"),"recommendation":r.get("description"),"status":r.get("status"),"participants":d.get("participants",[]),"disagreements":d.get("disagreements") or d.get("challenge_round",[]),"consensus_confidence":d.get("consensus_confidence"),"outcome":o.get("assessment"),"updated_at":r.get("updated_at")})
 return jsonify(ok=True,count=len(memories),memories=memories)

@app.post("/api/admin/council-memory/recall")
def recall_council_memory():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {};topic=(body.get("topic") or "").lower()
 rows=supabase_request("agent_tasks?agent=eq.Executive%20Council&select=id,title,description,status,council_deliberation,outcome_measurement,updated_at&limit=500",token=token);rows=rows if isinstance(rows,list) else []
 ranked=[]
 for r in rows:
  text=((r.get("title") or "")+" "+(r.get("description") or "")).lower();terms=[x for x in topic.split() if len(x)>2];score=sum(1 for x in terms if x in text)
  if score:
   ranked.append((score,r))
 ranked.sort(key=lambda x:x[0],reverse=True)
 matches=[{"task_id":r.get("id"),"topic":r.get("title"),"recommendation":r.get("description"),"status":r.get("status"),"similarity_score":s,"updated_at":r.get("updated_at")} for s,r in ranked[:10]]
 return jsonify(ok=True,query=body.get("topic"),matches=matches)

@app.post("/api/admin/agent-council/deliberate-with-memory")
def deliberate_with_memory():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {};topic=body.get("topic","Business decision")
 rows=supabase_request("agent_tasks?agent=eq.Executive%20Council&select=id,title,description,status,outcome_measurement,updated_at&limit=500",token=token);rows=rows if isinstance(rows,list) else []
 terms=[x for x in topic.lower().split() if len(x)>2];hist=[]
 for r in rows:
  text=((r.get("title") or "")+" "+(r.get("description") or "")).lower();score=sum(1 for x in terms if x in text)
  if score:
   raw=r.get("outcome_measurement")
   try:o=json.loads(raw) if isinstance(raw,str) else raw
   except:o={}
   hist.append({"topic":r.get("title"),"recommendation":r.get("description"),"status":r.get("status"),"outcome":o.get("assessment"),"similarity_score":score})
 hist=sorted(hist,key=lambda x:x["similarity_score"],reverse=True)[:5]
 evidence=list(body.get("evidence") or [])+[{"type":"institutional_memory","decision":x} for x in hist]
 agents=body.get("agents") or COUNCIL_AGENT_ROUTING.get(str(body.get("category","STRATEGY")).upper(),COUNCIL_AGENT_ROUTING["STRATEGY"])
 investigations=[]
 for agent in agents:
  investigations.append({"agent":agent,"historical_context_count":len(hist),"finding":"Reviewed current evidence and institutional memory","confidence":75 if hist else 70})
 council={"topic":topic,"participants":agents,"historical_memories":hist,"evidence":evidence,"investigations":investigations,"consensus_confidence":round(sum(x["confidence"] for x in investigations)/len(investigations),1) if investigations else 0,"recommendation":"Use current evidence while explicitly incorporating lessons from similar historical decisions.","created_at":datetime.datetime.utcnow().isoformat()+"Z"}
 return jsonify(ok=True,council=council)

@app.post("/api/admin/strategy/scenario-simulate")
def strategy_scenario_simulate():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];body=request.get_json(silent=True) or {}
 topic=body.get("topic","Strategic decision");scenarios=body.get("scenarios") or []
 if not scenarios:return jsonify(error="At least one scenario is required"),400
 results=[]
 for i,s in enumerate(scenarios):
  name=s.get("name") or ("Option "+chr(65+i));impact=s.get("impact") or {}
  revenue=float(impact.get("revenue",0));profit=float(impact.get("profit",0));operations=float(impact.get("operations",0));risk=float(s.get("risk",50));confidence=float(s.get("confidence",60))
  historical=float(s.get("historical_similarity",0));score=round(revenue*.28+profit*.32+operations*.15+(100-risk)*.15+confidence*.07+historical*.03,2)
  results.append({"name":name,"description":s.get("description",""),"estimated_revenue_impact":revenue,"estimated_profit_impact":profit,"operational_feasibility":operations,"risk":risk,"confidence":confidence,"historical_similarity":historical,"strategic_score":score})
 results.sort(key=lambda x:x["strategic_score"],reverse=True)
 for rank,x in enumerate(results,1):x["rank"]=rank
 return jsonify(ok=True,topic=topic,scenarios=results,recommended=results[0],simulation_note="Decision-support model; estimates require real evidence and founder validation.",created_at=datetime.datetime.utcnow().isoformat()+"Z")

EXPERIMENT_DECISIONS={"RUNNING","COMPLETED","PAUSED","STOPPED","SCALE","MODIFY"}

@app.post("/api/admin/strategy/experiments")
def create_strategy_experiment():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {};now=datetime.datetime.utcnow().isoformat()+"Z"
 required=["name","hypothesis"]
 missing=[x for x in required if not b.get(x)]
 if missing:return jsonify(error="Missing required fields",fields=missing),400
 exp={"name":b["name"],"hypothesis":b["hypothesis"],"scenario":b.get("scenario"),"success_metrics":b.get("success_metrics",[]),"budget_limit":b.get("budget_limit"),"exposure_limit":b.get("exposure_limit"),"baseline":b.get("baseline",{}),"status":"DRAFT","created_at":now}
 r=supabase_request("strategy_experiments",method="POST",body=exp,token=token)
 return jsonify(ok=True,experiment=r)

@app.get("/api/admin/strategy/experiments")
def list_strategy_experiments():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];r=supabase_request("strategy_experiments?select=*&order=created_at.desc",token=token)
 return jsonify(ok=True,experiments=r if isinstance(r,list) else [])

@app.post("/api/admin/strategy/experiments/<experiment_id>/decision")
def decide_strategy_experiment(experiment_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {};decision=str(b.get("decision","")).upper()
 if decision not in EXPERIMENT_DECISIONS:return jsonify(error="Invalid experiment decision"),400
 now=datetime.datetime.utcnow().isoformat()+"Z";body={"status":decision,"decision":decision,"decision_reason":b.get("reason"),"results":b.get("results",{}),"updated_at":now,"completed_at":now if decision in ["COMPLETED","STOPPED","SCALE"] else None}
 r=supabase_request("strategy_experiments?id=eq."+experiment_id,method="PATCH",body=body,token=token)
 return jsonify(ok=True,decision=decision,result=r)

@app.post("/api/admin/strategy/experiments/<experiment_id>/evaluate")
def evaluate_strategy_experiment(experiment_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 baseline=b.get("baseline",{});results=b.get("results",{});metrics=[]
 keys=b.get("metrics") or sorted(set(list(baseline.keys())+list(results.keys())))
 for key in keys:
  base=float(baseline.get(key,0) or 0);actual=float(results.get(key,0) or 0)
  change=((actual-base)/abs(base)*100) if base else (100 if actual>0 else 0)
  metrics.append({"metric":key,"baseline":base,"result":actual,"change_percent":round(change,2)})
 improvements=[m["change_percent"] for m in metrics]
 score=round(sum(max(-100,min(100,x)) for x in improvements)/len(improvements),2) if improvements else 0
 confidence=float(b.get("confidence",60));risk=float(b.get("risk",30))
 success_score=round(max(0,min(100,50+score*.35+confidence*.2-risk*.15)),2)
 recommendation="SCALE" if success_score>=75 else "CONTINUE" if success_score>=60 else "MODIFY" if success_score>=40 else "STOP"
 evaluation={"baseline":baseline,"results":results,"metric_analysis":metrics,"improvement_score":score,"confidence":confidence,"risk":risk,"success_score":success_score,"recommendation":recommendation,"evaluated_at":datetime.datetime.utcnow().isoformat()+"Z"}
 r=supabase_request("strategy_experiments?id=eq."+experiment_id,method="PATCH",body={"evaluation":evaluation,"updated_at":evaluation["evaluated_at"]},token=token)
 return jsonify(ok=True,evaluation=evaluation,result=r)

@app.post("/api/admin/predictive-intelligence/forecast")
def predictive_forecast():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {};series=b.get("series") or [];metric=b.get("metric","business_metric")
 vals=[]
 for x in series:
  try:vals.append(float(x.get("value",x) if isinstance(x,dict) else x))
  except:pass
 if len(vals)<2:return jsonify(error="At least two historical observations are required"),400
 changes=[vals[i]-vals[i-1] for i in range(1,len(vals))];trend=sum(changes)/len(changes);recent=sum(vals[-min(3,len(vals)):])/min(3,len(vals))
 horizon=max(1,min(int(b.get("horizon",3)),12));forecast=[round(vals[-1]+trend*(i+1),2) for i in range(horizon)]
 volatility=(sum((c-trend)**2 for c in changes)/len(changes))**0.5 if changes else 0
 direction="GROWING" if trend>0 else "DECLINING" if trend<0 else "STABLE"
 confidence=round(max(20,min(95,85-(volatility/(abs(recent) or 1))*100)),2)
 warnings=[]
 if direction=="DECLINING":warnings.append({"type":"EARLY_WARNING","severity":"HIGH" if abs(trend)>abs(recent)*.1 else "MEDIUM","message":metric+" shows a declining trend"})
 if volatility>abs(recent)*.2:warnings.append({"type":"VOLATILITY","severity":"MEDIUM","message":metric+" is highly volatile"})
 opportunity={"detected":direction=="GROWING","message":metric+" has positive momentum"} if direction=="GROWING" else {"detected":False}
 return jsonify(ok=True,metric=metric,observations=len(vals),current_value=vals[-1],trend_per_period=round(trend,4),trend_direction=direction,forecast=forecast,confidence=confidence,volatility=round(volatility,4),early_warnings=warnings,opportunity=opportunity,generated_at=datetime.datetime.utcnow().isoformat()+"Z")

@app.get("/api/admin/products")
def list_products():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];r=supabase_request("products?select=*&order=created_at.desc",token=token)
 return jsonify(ok=True,products=r if isinstance(r,list) else [])

@app.post("/api/admin/products")
def create_product():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 if not b.get("name"):return jsonify(error="Product name is required"),400
 now=datetime.datetime.utcnow().isoformat()+"Z"
 product={"name":b["name"],"description":b.get("description"),"sku":b.get("sku") or str(uuid.uuid4())[:8].upper(),"category":b.get("category"),"selling_price":b.get("selling_price",0),"cost_price":b.get("cost_price",0),"status":b.get("status","ACTIVE"),"image_url":b.get("image_url"),"created_at":now,"updated_at":now}
 r=supabase_request("products",method="POST",body=product,token=token)
 return jsonify(ok=True,product=r)

@app.patch("/api/admin/products/<product_id>")
def update_product(product_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {};allowed=["name","description","sku","category","selling_price","cost_price","status","image_url","inventory_quantity"]
 body={k:b[k] for k in allowed if k in b};body["updated_at"]=datetime.datetime.utcnow().isoformat()+"Z"
 r=supabase_request("products?id=eq."+product_id,method="PATCH",body=body,token=token)
 return jsonify(ok=True,product=r)

@app.get("/api/admin/products/intelligence")
def product_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("products?select=*&limit=1000",token=token);rows=rows if isinstance(rows,list) else []
 analysis=[]
 for p in rows:
  sell=float(p.get("selling_price") or 0);cost=float(p.get("cost_price") or 0);margin=sell-cost;margin_pct=round((margin/sell*100),2) if sell else 0;stock=p.get("inventory_quantity")
  analysis.append({"id":p.get("id"),"name":p.get("name"),"sku":p.get("sku"),"selling_price":sell,"cost_price":cost,"unit_margin":round(margin,2),"margin_percent":margin_pct,"inventory_quantity":stock,"health":"HIGH_MARGIN" if margin_pct>=40 else "LOW_MARGIN" if margin_pct<15 else "NORMAL"})
 analysis.sort(key=lambda x:x["unit_margin"],reverse=True)
 return jsonify(ok=True,count=len(analysis),products=analysis)

ORDER_STATUS_FLOW=["NEW REQUEST","PHOTO REVIEW","QUOTE SENT","AWAITING APPROVAL","PAYMENT PENDING","PAID","IN PRODUCTION","QUALITY CHECK","SHIPPED","DELIVERED","CANCELLED"]

@app.post("/api/admin/orders/<order_number>/transition")
def order_transition(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {};new=str(b.get("status","")).upper()
 if new not in ORDER_STATUS_FLOW:return jsonify(error="Invalid order status",allowed=ORDER_STATUS_FLOW),400
 rows=supabase_request("orders?order_number=eq."+urllib.parse.quote(order_number)+"&select=*",token=token)
 if not isinstance(rows,list) or not rows:return jsonify(error="Order not found"),404
 old=str(rows[0].get("status","")).upper()
 if old in ORDER_STATUS_FLOW and old!="CANCELLED":
  oi=ORDER_STATUS_FLOW.index(old);ni=ORDER_STATUS_FLOW.index(new)
  if new!="CANCELLED" and ni<oi:return jsonify(error="Backward transition not allowed",current=old),409
 now=datetime.datetime.utcnow().isoformat()+"Z";r=supabase_request("orders?order_number=eq."+urllib.parse.quote(order_number),method="PATCH",body={"status":new,"updated_at":now},token=token)
 try:create_order_notification(token,order_number,"Order status updated","Your order is now "+new,"order_update")
 except:pass
 return jsonify(ok=True,order_number=order_number,previous_status=old,status=new,result=r)

@app.get("/api/admin/orders/<order_number>/lifecycle")
def order_lifecycle(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("orders?order_number=eq."+urllib.parse.quote(order_number)+"&select=*",token=token)
 if not isinstance(rows,list) or not rows:return jsonify(error="Order not found"),404
 order=rows[0];current=str(order.get("status","")).upper();idx=ORDER_STATUS_FLOW.index(current) if current in ORDER_STATUS_FLOW else -1
 stages=[{"status":s,"state":"CURRENT" if s==current else "COMPLETED" if idx>i else "PENDING"} for i,s in enumerate(ORDER_STATUS_FLOW) if not(current=="CANCELLED" and s!="CANCELLED")]
 return jsonify(ok=True,order_number=order_number,current_status=current,lifecycle=stages)

@app.get("/api/admin/orders/operations/summary")
def order_operations_summary():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("orders?select=status",token=token);rows=rows if isinstance(rows,list) else []
 counts={s:0 for s in ORDER_STATUS_FLOW}
 for x in rows:
  s=str(x.get("status","")).upper()
  if s in counts:counts[s]+=1
 active=sum(v for k,v in counts.items() if k not in ["DELIVERED","CANCELLED"])
 return jsonify(ok=True,total=len(rows),active=active,by_status=counts)

@app.get("/api/admin/customers")
def admin_customers():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];limit=min(max(int(request.args.get("limit",100)),1),500)
 profiles=supabase_request("profiles?select=id,email,full_name,phone,created_at&limit="+str(limit),token=token);profiles=profiles if isinstance(profiles,list) else []
 orders=supabase_request("orders?select=customer_id,order_number,status,total_amount,created_at",token=token);orders=orders if isinstance(orders,list) else []
 by={}
 for o in orders:
  cid=o.get("customer_id")
  if cid:by.setdefault(cid,[]).append(o)
 out=[]
 for p in profiles:
  os=by.get(p.get("id"),[]);spent=sum(float(o.get("total_amount") or 0) for o in os);delivered=sum(1 for o in os if str(o.get("status","")).upper()=="DELIVERED")
  out.append({**p,"order_count":len(os),"delivered_orders":delivered,"lifetime_value":round(spent,2),"customer_segment":"VIP" if spent>=50000 else "RETURNING" if len(os)>=2 else "NEW" if os else "LEAD"})
 out.sort(key=lambda x:x["lifetime_value"],reverse=True)
 return jsonify(ok=True,count=len(out),customers=out)

@app.get("/api/admin/customers/<customer_id>")
def admin_customer_detail(customer_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 p=supabase_request("profiles?id=eq."+customer_id+"&select=*",token=token);o=supabase_request("orders?customer_id=eq."+customer_id+"&select=*&order=created_at.desc",token=token)
 if not isinstance(p,list) or not p:return jsonify(error="Customer not found"),404
 o=o if isinstance(o,list) else [];spent=sum(float(x.get("total_amount") or 0) for x in o)
 return jsonify(ok=True,customer=p[0],orders=o,insights={"order_count":len(o),"lifetime_value":round(spent,2),"segment":"VIP" if spent>=50000 else "RETURNING" if len(o)>=2 else "NEW"})

@app.get("/api/admin/customers/intelligence")
def customer_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];profiles=supabase_request("profiles?select=id,email,full_name&limit=1000",token=token);orders=supabase_request("orders?select=customer_id,total_amount,created_at,status",token=token)
 profiles=profiles if isinstance(profiles,list) else [];orders=orders if isinstance(orders,list) else [];by={}
 for o in orders:
  if o.get("customer_id"):by.setdefault(o["customer_id"],[]).append(o)
 result=[]
 for p in profiles:
  os=by.get(p["id"],[]);spent=sum(float(x.get("total_amount") or 0) for x in os)
  status="HIGH_VALUE" if spent>=50000 else "AT_RISK" if os and all(str(x.get("status","")).upper()=="CANCELLED" for x in os) else "HEALTHY"
  result.append({"customer_id":p["id"],"name":p.get("full_name"),"order_count":len(os),"lifetime_value":round(spent,2),"health":status})
 return jsonify(ok=True,customers=result)

@app.get("/api/admin/finance/overview")
def finance_overview():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=total_amount,status,created_at",token=token);orders=orders if isinstance(orders,list) else []
 products=supabase_request("products?select=selling_price,cost_price",token=token);products=products if isinstance(products,list) else []
 revenue=sum(float(o.get("total_amount") or 0) for o in orders if str(o.get("status","")).upper() not in ["CANCELLED","PAYMENT PENDING"])
 completed=sum(1 for o in orders if str(o.get("status","")).upper()=="DELIVERED")
 avg_order=round(revenue/len(orders),2) if orders else 0
 margins=[(float(p.get("selling_price") or 0)-float(p.get("cost_price") or 0)) for p in products]
 avg_product_margin=round(sum(margins)/len(margins),2) if margins else 0
 return jsonify(ok=True,revenue=round(revenue,2),order_count=len(orders),completed_orders=completed,average_order_value=avg_order,product_count=len(products),average_unit_margin=avg_product_margin,estimated_product_gross_margin=round(sum(margins),2))

@app.get("/api/admin/finance/risks")
def finance_risks():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=status,total_amount",token=token);orders=orders if isinstance(orders,list) else []
 total=len(orders);cancelled=sum(1 for o in orders if str(o.get("status","")).upper()=="CANCELLED");pending=sum(float(o.get("total_amount") or 0) for o in orders if str(o.get("status","")).upper()=="PAYMENT PENDING")
 risks=[]
 if total and cancelled/total>.1:risks.append({"type":"CANCELLATION_RATE","severity":"HIGH","message":"Cancellation rate exceeds 10%","rate":round(cancelled/total*100,2)})
 if pending>0:risks.append({"type":"OUTSTANDING_PAYMENT","severity":"MEDIUM","message":"Orders are awaiting payment","amount":round(pending,2)})
 return jsonify(ok=True,risks=risks,cancelled_orders=cancelled,outstanding_payment_value=round(pending,2))

@app.get("/api/admin/finance/intelligence")
def finance_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];products=supabase_request("products?select=name,selling_price,cost_price",token=token);products=products if isinstance(products,list) else []
 insights=[]
 for p in products:
  s=float(p.get("selling_price") or 0);c=float(p.get("cost_price") or 0);pct=((s-c)/s*100) if s else 0
  if pct<15:insights.append({"type":"LOW_MARGIN_PRODUCT","product":p.get("name"),"margin_percent":round(pct,2),"recommendation":"Review pricing or production cost"})
 return jsonify(ok=True,insights=insights,generated_at=datetime.datetime.utcnow().isoformat()+"Z")

@app.get("/api/admin/operations/overview")
def operations_overview():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 rows=supabase_request("orders?select=order_number,status,created_at,updated_at",token=token);rows=rows if isinstance(rows,list) else []
 stages=["NEW REQUEST","PHOTO REVIEW","QUOTE SENT","AWAITING APPROVAL","PAYMENT PENDING","PAID","IN PRODUCTION","QUALITY CHECK","SHIPPED","DELIVERED","CANCELLED"]
 counts={s:0 for s in stages}
 for o in rows:
  s=str(o.get("status","")).upper()
  if s in counts:counts[s]+=1
 active=sum(v for k,v in counts.items() if k not in ["DELIVERED","CANCELLED"])
 bottlenecks=sorted([{"stage":k,"orders":v} for k,v in counts.items() if k not in ["DELIVERED","CANCELLED"]],key=lambda x:x["orders"],reverse=True)
 return jsonify(ok=True,total_orders=len(rows),active_workload=active,by_stage=counts,bottlenecks=bottlenecks[:3])

@app.get("/api/admin/operations/alerts")
def operations_alerts():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("orders?select=order_number,status,created_at,updated_at",token=token);rows=rows if isinstance(rows,list) else []
 now=datetime.datetime.utcnow();alerts=[]
 for o in rows:
  s=str(o.get("status","")).upper()
  if s in ["DELIVERED","CANCELLED"]:continue
  ts=o.get("updated_at") or o.get("created_at")
  try:
   age=(now-datetime.datetime.fromisoformat(str(ts).replace("Z","+00:00")).replace(tzinfo=None)).days
   if age>=3:alerts.append({"type":"STALLED_ORDER","severity":"HIGH" if age>=7 else "MEDIUM","order_number":o.get("order_number"),"status":s,"days_without_update":age})
  except:pass
 return jsonify(ok=True,count=len(alerts),alerts=alerts)

@app.get("/api/admin/operations/intelligence")
def operations_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("orders?select=status",token=token);rows=rows if isinstance(rows,list) else []
 counts={}
 for o in rows:
  s=str(o.get("status","")).upper()
  if s not in ["DELIVERED","CANCELLED"]:counts[s]=counts.get(s,0)+1
 top=sorted(counts.items(),key=lambda x:x[1],reverse=True)
 insights=[]
 if top and top[0][1]>=3:insights.append({"type":"BOTTLENECK","severity":"HIGH" if top[0][1]>=10 else "MEDIUM","stage":top[0][0],"orders":top[0][1],"recommendation":"Investigate capacity and workflow at this stage"})
 return jsonify(ok=True,insights=insights)

@app.get("/api/admin/analytics/overview")
def analytics_overview():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=customer_id,total_amount,status,created_at",token=token);products=supabase_request("products?select=id,name,selling_price,cost_price",token=token);profiles=supabase_request("profiles?select=id",token=token)
 orders=orders if isinstance(orders,list) else [];products=products if isinstance(products,list) else [];profiles=profiles if isinstance(profiles,list) else []
 valid=[o for o in orders if str(o.get("status","")).upper()!="CANCELLED"];revenue=sum(float(o.get("total_amount") or 0) for o in valid)
 delivered=sum(1 for o in orders if str(o.get("status","")).upper()=="DELIVERED");cancelled=sum(1 for o in orders if str(o.get("status","")).upper()=="CANCELLED")
 active=sum(1 for o in orders if str(o.get("status","")).upper() not in ["DELIVERED","CANCELLED"])
 customers_with_orders=len(set(o.get("customer_id") for o in orders if o.get("customer_id")))
 return jsonify(ok=True,generated_at=datetime.datetime.utcnow().isoformat()+"Z",kpis={"revenue":round(revenue,2),"total_orders":len(orders),"average_order_value":round(revenue/len(valid),2) if valid else 0,"delivered_orders":delivered,"active_orders":active,"cancellation_rate":round(cancelled/len(orders)*100,2) if orders else 0,"total_customers":len(profiles),"customers_with_orders":customers_with_orders,"product_count":len(products)})

@app.get("/api/admin/analytics/health")
def business_health():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=status,total_amount",token=token);products=supabase_request("products?select=selling_price,cost_price",token=token)
 orders=orders if isinstance(orders,list) else [];products=products if isinstance(products,list) else []
 cancellation=sum(1 for o in orders if str(o.get("status","")).upper()=="CANCELLED")/(len(orders) or 1)
 low=sum(1 for p in products if (float(p.get("selling_price") or 0)>0 and (float(p.get("selling_price") or 0)-float(p.get("cost_price") or 0))/float(p.get("selling_price") or 1)<.15))
 score=100-cancellation*100*.5-(low/(len(products) or 1))*30
 score=round(max(0,min(100,score)),1);status="EXCELLENT" if score>=85 else "HEALTHY" if score>=70 else "ATTENTION" if score>=50 else "CRITICAL"
 return jsonify(ok=True,business_health_score=score,status=status,factors={"cancellation_rate":round(cancellation*100,2),"low_margin_products":low,"total_products":len(products)})

@app.get("/api/admin/analytics/executive-summary")
def analytics_executive_summary():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=status,total_amount",token=token);orders=orders if isinstance(orders,list) else []
 revenue=sum(float(o.get("total_amount") or 0) for o in orders if str(o.get("status","")).upper()!="CANCELLED");active=sum(1 for o in orders if str(o.get("status","")).upper() not in ["DELIVERED","CANCELLED"])
 attention=[]
 if active>=5:attention.append("Operational workload requires monitoring")
 canc=sum(1 for o in orders if str(o.get("status","")).upper()=="CANCELLED")
 if orders and canc/len(orders)>.1:attention.append("Cancellation rate requires investigation")
 return jsonify(ok=True,summary={"revenue":round(revenue,2),"active_orders":active,"total_orders":len(orders),"attention_required":attention,"generated_at":datetime.datetime.utcnow().isoformat()+"Z"})

@app.get("/api/admin/inventory")
def inventory_overview():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("products?select=id,name,sku,inventory_quantity,selling_price,status",token=token);rows=rows if isinstance(rows,list) else []
 out=[];total=0
 for p in rows:
  q=int(p.get("inventory_quantity") or 0);total+=q
  out.append({**p,"stock_status":"OUT_OF_STOCK" if q<=0 else "LOW_STOCK" if q<=5 else "HEALTHY"})
 return jsonify(ok=True,total_products=len(out),total_units=total,items=out)

@app.patch("/api/admin/inventory/<product_id>")
def update_inventory(product_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {}
 if "quantity" not in b:return jsonify(error="quantity is required"),400
 try:q=max(0,int(b["quantity"]))
 except:return jsonify(error="quantity must be numeric"),400
 r=supabase_request("products?id=eq."+product_id,method="PATCH",body={"inventory_quantity":q,"updated_at":datetime.datetime.utcnow().isoformat()+"Z"},token=token)
 return jsonify(ok=True,product_id=product_id,inventory_quantity=q,result=r)

@app.get("/api/admin/inventory/alerts")
def inventory_alerts():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("products?select=id,name,sku,inventory_quantity",token=token);rows=rows if isinstance(rows,list) else []
 alerts=[]
 for p in rows:
  q=int(p.get("inventory_quantity") or 0)
  if q<=0:alerts.append({"type":"OUT_OF_STOCK","severity":"HIGH","product":p.get("name"),"sku":p.get("sku"),"quantity":q})
  elif q<=5:alerts.append({"type":"LOW_STOCK","severity":"MEDIUM","product":p.get("name"),"sku":p.get("sku"),"quantity":q})
 return jsonify(ok=True,count=len(alerts),alerts=alerts)

@app.get("/api/admin/inventory/intelligence")
def inventory_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("products?select=name,inventory_quantity,cost_price",token=token);rows=rows if isinstance(rows,list) else []
 insights=[]
 for p in rows:
  q=int(p.get("inventory_quantity") or 0)
  if q<=5:insights.append({"type":"REPLENISHMENT","product":p.get("name"),"quantity":q,"priority":"URGENT" if q<=0 else "HIGH","recommendation":"Replenish inventory before stockout"})
 return jsonify(ok=True,insights=insights)

def create_admin_notification(token,title,message,kind="system",severity="INFO",entity_type=None,entity_id=None):
 body={"title":title,"message":message,"type":kind}
 if entity_type:body["entity_type"]=entity_type
 if entity_id:body["entity_id"]=str(entity_id)
 try:return supabase_request("notifications",method="POST",body=body,token=token,prefer="return=representation")
 except:return None

@app.get("/api/admin/notifications")
def admin_notifications():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];limit=min(max(int(request.args.get("limit",50)),1),200)
 rows=supabase_request("notifications?select=*&order=created_at.desc&limit="+str(limit),token=token);rows=rows if isinstance(rows,list) else []
 return jsonify(ok=True,count=len(rows),notifications=rows)

@app.get("/api/admin/notifications/summary")
def notification_summary():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("notifications?select=type,created_at",token=token);rows=rows if isinstance(rows,list) else []
 by_type={}
 for x in rows:by_type[x.get("type") or "system"]=by_type.get(x.get("type") or "system",0)+1
 return jsonify(ok=True,total=len(rows),by_type=by_type)

@app.post("/api/admin/notifications/broadcast")
def notification_broadcast():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {};title=str(b.get("title","")).strip();message=str(b.get("message","")).strip()
 if not title or not message:return jsonify(error="title and message are required"),400
 profiles=supabase_request("profiles?select=id",token=token);profiles=profiles if isinstance(profiles,list) else []
 sent=0
 for p in profiles:
  try:supabase_request("notifications",method="POST",body={"user_id":p["id"],"title":title,"message":message,"type":b.get("type","broadcast")},token=token);sent+=1
  except:pass
 return jsonify(ok=True,recipients=sent,title=title)

@app.get("/api/admin/notifications/intelligence")
def notification_intelligence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];inv=supabase_request("products?select=name,inventory_quantity",token=token);orders=supabase_request("orders?select=order_number,status,updated_at,created_at",token=token)
 inv=inv if isinstance(inv,list) else [];orders=orders if isinstance(orders,list) else [];signals=[]
 for p in inv:
  q=int(p.get("inventory_quantity") or 0)
  if q<=5:signals.append({"category":"INVENTORY","priority":"HIGH" if q<=0 else "MEDIUM","message":(p.get("name") or "Product")+" stock requires attention"})
 for o in orders:
  if str(o.get("status","")).upper()=="PAYMENT PENDING":signals.append({"category":"PAYMENT","priority":"MEDIUM","message":"Payment pending for order "+str(o.get("order_number") or "")})
 return jsonify(ok=True,signals=signals)

TEAM_ROLES={"OWNER":["*"],"ADMIN":["analytics","orders","products","customers","finance","operations","inventory","notifications","team"],"OPERATIONS":["orders","operations","inventory"],"FINANCE":["finance","analytics"],"SUPPORT":["customers","orders","notifications"],"VIEWER":["analytics"]}

@app.get("/api/admin/team/roles")
def team_roles():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,roles=TEAM_ROLES)

@app.get("/api/admin/team")
def team_members():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];rows=supabase_request("profiles?select=id,email,full_name,phone,created_at",token=token);rows=rows if isinstance(rows,list) else []
 return jsonify(ok=True,count=len(rows),members=[{**x,"role":"UNASSIGNED"} for x in rows])

@app.post("/api/admin/team/access-check")
def team_access_check():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {};role=str(b.get("role","")).upper();module=str(b.get("module","")).lower()
 if role not in TEAM_ROLES:return jsonify(error="Unknown role"),400
 allowed="*" in TEAM_ROLES[role] or module in TEAM_ROLES[role]
 return jsonify(ok=True,role=role,module=module,allowed=allowed)

@app.get("/api/admin/team/audit-summary")
def team_audit_summary():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,governance={"roles_defined":len(TEAM_ROLES),"principle":"least_privilege","approval_model":"role_based","audit_ready":True})

def order_financial_amount(o):
 return float(o.get("total_amount") or o.get("price") or 0)+float(o.get("shipping_cost") or 0)

@app.get("/api/admin/finance/ledger")
def finance_ledger():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=order_number,status,total_amount,price,shipping_cost,created_at,updated_at",token=token);orders=orders if isinstance(orders,list) else []
 entries=[]
 for o in orders:
  s=str(o.get("status","")).upper();amount=order_financial_amount(o)
  if s in ["PAID","IN PRODUCTION","QUALITY CHECK","SHIPPED","DELIVERED"]:entries.append({"date":o.get("updated_at") or o.get("created_at"),"type":"REVENUE","reference":o.get("order_number"),"amount":round(amount,2),"status":"RECOGNIZED"})
  elif s=="PAYMENT PENDING":entries.append({"date":o.get("updated_at") or o.get("created_at"),"type":"RECEIVABLE","reference":o.get("order_number"),"amount":round(amount,2),"status":"PENDING"})
  elif s=="CANCELLED":entries.append({"date":o.get("updated_at") or o.get("created_at"),"type":"CANCELLATION","reference":o.get("order_number"),"amount":round(amount,2),"status":"VOID"})
 return jsonify(ok=True,count=len(entries),entries=entries)

@app.get("/api/admin/finance/cashflow")
def finance_cashflow():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=status,total_amount,price,shipping_cost",token=token);orders=orders if isinstance(orders,list) else []
 inflow=sum(order_financial_amount(o) for o in orders if str(o.get("status","")).upper() in ["PAID","IN PRODUCTION","QUALITY CHECK","SHIPPED","DELIVERED"])
 receivable=sum(order_financial_amount(o) for o in orders if str(o.get("status","")).upper()=="PAYMENT PENDING")
 return jsonify(ok=True,cash_inflow=round(inflow,2),accounts_receivable=round(receivable,2),known_expenses=0,net_cash_position_estimate=round(inflow,2),note="Expense ledger not yet connected")

@app.get("/api/admin/finance/profitability")
def finance_profitability():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];products=supabase_request("products?select=name,selling_price,cost_price",token=token);products=products if isinstance(products,list) else []
 rows=[]
 for p in products:
  s=float(p.get("selling_price") or 0);c=float(p.get("cost_price") or 0);m=s-c
  rows.append({"product":p.get("name"),"selling_price":s,"cost":c,"gross_margin":round(m,2),"margin_percent":round(m/s*100,2) if s else 0})
 return jsonify(ok=True,products=rows)

@app.get("/api/admin/finance/executive")
def finance_executive():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=status,total_amount,price,shipping_cost",token=token);orders=orders if isinstance(orders,list) else []
 rev=sum(order_financial_amount(o) for o in orders if str(o.get("status","")).upper() in ["PAID","IN PRODUCTION","QUALITY CHECK","SHIPPED","DELIVERED"]);recv=sum(order_financial_amount(o) for o in orders if str(o.get("status","")).upper()=="PAYMENT PENDING")
 return jsonify(ok=True,revenue_recognized=round(rev,2),receivables=round(recv,2),financial_attention=["Connect expense ledger for true net profit"] if rev else ["No recognized revenue yet"])

def inventory_reserve_for_order(token,order_number):
 orders=supabase_request("orders?order_number=eq."+str(order_number)+"&select=notes",token=token)
 if not isinstance(orders,list) or not orders:return {"ok":False,"reason":"order_not_found"}
 try:n=json.loads(orders[0].get("notes") or "{}")
 except:n={}
 items=n.get("items") or n.get("inventory_items") or []
 if not items:return {"ok":False,"reason":"no_inventory_items_linked","reserved":[]}
 reserved=[];shortages=[]
 for item in items:
  pid=str(item.get("product_id") or "");need=max(1,int(item.get("quantity") or 1))
  if not pid:continue
  p=supabase_request("products?id=eq."+pid+"&select=id,name,inventory_quantity",token=token)
  if not isinstance(p,list) or not p:shortages.append({"product_id":pid,"reason":"product_not_found"});continue
  q=int(p[0].get("inventory_quantity") or 0)
  if q<need:shortages.append({"product_id":pid,"product":p[0].get("name"),"available":q,"required":need});continue
  newq=q-need;supabase_request("products?id=eq."+pid,method="PATCH",body={"inventory_quantity":newq,"updated_at":datetime.datetime.utcnow().isoformat()+"Z"},token=token)
  reserved.append({"product_id":pid,"product":p[0].get("name"),"consumed":need,"remaining":newq})
 return {"ok":not shortages,"reserved":reserved,"shortages":shortages}

@app.post("/api/admin/orders/<order_number>/inventory-reserve")
def order_inventory_reserve(order_number):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];result=inventory_reserve_for_order(token,order_number)
 return jsonify(ok=result["ok"],order_number=order_number,**result), (200 if result["ok"] else 409)

@app.get("/api/admin/inventory/demand")
def inventory_demand():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=notes,status",token=token);orders=orders if isinstance(orders,list) else []
 demand={}
 for o in orders:
  if str(o.get("status","")).upper()=="CANCELLED":continue
  try:items=json.loads(o.get("notes") or "{}").get("items") or []
  except:items=[]
  for i in items:
   pid=i.get("product_id")
   if pid:demand[str(pid)]=demand.get(str(pid),0)+int(i.get("quantity") or 1)
 return jsonify(ok=True,demand=[{"product_id":k,"units_requested":v} for k,v in sorted(demand.items(),key=lambda x:x[1],reverse=True)])

@app.get("/api/admin/ai/business-context")
def ai_business_context():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1]
 orders=supabase_request("orders?select=status,total_amount,price,shipping_cost",token=token);products=supabase_request("products?select=name,inventory_quantity,selling_price,cost_price",token=token)
 orders=orders if isinstance(orders,list) else [];products=products if isinstance(products,list) else []
 active=sum(1 for o in orders if str(o.get("status","")).upper() not in ["DELIVERED","CANCELLED"]);cancel=sum(1 for o in orders if str(o.get("status","")).upper()=="CANCELLED")
 revenue=sum(order_financial_amount(o) for o in orders if str(o.get("status","")).upper() in ["PAID","IN PRODUCTION","QUALITY CHECK","SHIPPED","DELIVERED"])
 low=[{"product":p.get("name"),"quantity":int(p.get("inventory_quantity") or 0)} for p in products if int(p.get("inventory_quantity") or 0)<=5]
 return jsonify(ok=True,context={"orders":{"total":len(orders),"active":active,"cancelled":cancel},"finance":{"recognized_revenue":round(revenue,2)},"inventory":{"low_or_out":low},"products":{"total":len(products)},"generated_at":datetime.datetime.utcnow().isoformat()+"Z"})

@app.get("/api/admin/ai/executive-context")
def ai_executive_context():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];orders=supabase_request("orders?select=status,total_amount,price,shipping_cost",token=token);products=supabase_request("products?select=name,inventory_quantity",token=token)
 orders=orders if isinstance(orders,list) else [];products=products if isinstance(products,list) else []
 signals=[]
 active=sum(1 for o in orders if str(o.get("status","")).upper() not in ["DELIVERED","CANCELLED"])
 if active>=5:signals.append({"domain":"OPERATIONS","priority":"MEDIUM","signal":str(active)+" active orders require capacity monitoring"})
 for p in products:
  q=int(p.get("inventory_quantity") or 0)
  if q<=0:signals.append({"domain":"INVENTORY","priority":"HIGH","signal":str(p.get("name"))+" is out of stock"})
 cancelled=sum(1 for o in orders if str(o.get("status","")).upper()=="CANCELLED")
 if orders and cancelled/len(orders)>.1:signals.append({"domain":"CUSTOMER","priority":"MEDIUM","signal":"Cancellation rate exceeds 10%"})
 return jsonify(ok=True,signals=signals,executive_context_ready=True)

@app.post("/api/admin/ai/decision-context")
def ai_decision_context():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];b=request.get_json(silent=True) or {};question=str(b.get("question","")).strip()
 if not question:return jsonify(error="question is required"),400
 orders=supabase_request("orders?select=status,total_amount",token=token);products=supabase_request("products?select=name,inventory_quantity,selling_price,cost_price",token=token)
 orders=orders if isinstance(orders,list) else [];products=products if isinstance(products,list) else []
 return jsonify(ok=True,decision_question=question,grounded_context={"order_count":len(orders),"product_count":len(products),"low_stock_products":sum(1 for p in products if int(p.get("inventory_quantity") or 0)<=5),"instruction":"Use this live context as evidence for AI decision support, not as a guaranteed prediction."})

AI_EVENT_ROUTING={"INVENTORY_STOCKOUT":{"agent":"inventory_intelligence","priority":"HIGH"},"LOW_STOCK":{"agent":"inventory_intelligence","priority":"MEDIUM"},"PAYMENT_PENDING":{"agent":"finance_intelligence","priority":"MEDIUM"},"HIGH_CANCELLATION":{"agent":"customer_intelligence","priority":"MEDIUM"},"OPERATIONS_BOTTLENECK":{"agent":"operations_intelligence","priority":"HIGH"}}

@app.post("/api/admin/ai/events")
def ai_business_event():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {};event_type=str(b.get("event_type","")).upper();data=b.get("data") or {}
 if event_type not in AI_EVENT_ROUTING:return jsonify(error="Unknown event_type",supported=list(AI_EVENT_ROUTING)),400
 route=AI_EVENT_ROUTING[event_type]
 return jsonify(ok=True,event={"type":event_type,"data":data,"received_at":datetime.datetime.utcnow().isoformat()+"Z"},routing=route,next_step="Build live context and request governed AI analysis")

@app.get("/api/admin/ai/events/routing")
def ai_event_routing():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,routes=AI_EVENT_ROUTING)

@app.get("/api/admin/ai/events/signals")
def ai_event_signals():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 token=auth.split(" ",1)[1];products=supabase_request("products?select=name,inventory_quantity",token=token);orders=supabase_request("orders?select=status",token=token)
 products=products if isinstance(products,list) else [];orders=orders if isinstance(orders,list) else [];events=[]
 for p in products:
  q=int(p.get("inventory_quantity") or 0)
  if q<=0:events.append({"event_type":"INVENTORY_STOCKOUT","data":{"product":p.get("name"),"quantity":q}})
  elif q<=5:events.append({"event_type":"LOW_STOCK","data":{"product":p.get("name"),"quantity":q}})
 active=sum(1 for o in orders if str(o.get("status","")).upper() not in ["DELIVERED","CANCELLED"])
 if active>=10:events.append({"event_type":"OPERATIONS_BOTTLENECK","data":{"active_orders":active}})
 return jsonify(ok=True,count=len(events),events=events)

AI_RECOMMENDATIONS=[]

@app.post("/api/admin/ai/recommendations")
def create_ai_recommendation():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {};title=str(b.get("title","")).strip();recommendation=str(b.get("recommendation","")).strip()
 if not title or not recommendation:return jsonify(error="title and recommendation are required"),400
 risk=str(b.get("risk","MEDIUM")).upper();confidence=max(0,min(100,float(b.get("confidence",50))))
 rid="rec_"+uuid.uuid4().hex[:12];item={"id":rid,"title":title,"recommendation":recommendation,"source_event":b.get("source_event"),"risk":risk,"confidence":confidence,"status":"PENDING_APPROVAL","created_at":datetime.datetime.utcnow().isoformat()+"Z","decision":None}
 AI_RECOMMENDATIONS.append(item);return jsonify(ok=True,recommendation=item)

@app.get("/api/admin/ai/recommendations")
def list_ai_recommendations():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 status=request.args.get("status");rows=[x for x in AI_RECOMMENDATIONS if not status or x["status"]==status.upper()]
 return jsonify(ok=True,count=len(rows),recommendations=rows)

@app.post("/api/admin/ai/recommendations/<recommendation_id>/decision")
def decide_ai_recommendation(recommendation_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {};decision=str(b.get("decision","")).upper()
 if decision not in ["APPROVED","REJECTED","MODIFIED"]:return jsonify(error="decision must be APPROVED, REJECTED or MODIFIED"),400
 item=next((x for x in AI_RECOMMENDATIONS if x["id"]==recommendation_id),None)
 if not item:return jsonify(error="recommendation not found"),404
 item["status"]=decision;item["decision"]={"decision":decision,"comment":b.get("comment"),"decided_at":datetime.datetime.utcnow().isoformat()+"Z"}
 return jsonify(ok=True,recommendation=item)

@app.get("/api/admin/ai/recommendations/summary")
def ai_recommendation_summary():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 counts={}
 for x in AI_RECOMMENDATIONS:counts[x["status"]]=counts.get(x["status"],0)+1
 return jsonify(ok=True,total=len(AI_RECOMMENDATIONS),by_status=counts,pending=sum(1 for x in AI_RECOMMENDATIONS if x["status"]=="PENDING_APPROVAL"))

AI_EXECUTION_LOG=[]

@app.post("/api/admin/ai/recommendations/<recommendation_id>/execute")
def execute_ai_recommendation(recommendation_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {};action=str(b.get("action","")).upper()
 item=next((x for x in AI_RECOMMENDATIONS if x["id"]==recommendation_id),None)
 if not item:return jsonify(error="recommendation not found"),404
 if item["status"]!="APPROVED":return jsonify(error="recommendation requires founder approval"),403
 allowed={"CREATE_NOTIFICATION","CREATE_OPERATION_TASK","CREATE_REPLENISHMENT_TASK","START_EXPERIMENT"}
 if action not in allowed:return jsonify(error="action not allowed",allowed=sorted(allowed)),403
 eid="exec_"+uuid.uuid4().hex[:12];result={"id":eid,"recommendation_id":recommendation_id,"action":action,"status":"EXECUTED","executed_at":datetime.datetime.utcnow().isoformat()+"Z","payload":b.get("payload") or {}}
 if action=="CREATE_NOTIFICATION":
  result["effect"]="Notification action authorized and recorded"
 elif action=="CREATE_REPLENISHMENT_TASK":
  result["effect"]="Inventory replenishment task authorized and recorded"
 elif action=="CREATE_OPERATION_TASK":
  result["effect"]="Operations task authorized and recorded"
 else:result["effect"]="Experiment start authorized and recorded"
 AI_EXECUTION_LOG.append(result);item["status"]="EXECUTED";item["execution_id"]=eid
 return jsonify(ok=True,execution=result)

@app.get("/api/admin/ai/executions")
def list_ai_executions():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,count=len(AI_EXECUTION_LOG),executions=AI_EXECUTION_LOG)

@app.get("/api/admin/ai/executions/<execution_id>")
def ai_execution_detail(execution_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 x=next((e for e in AI_EXECUTION_LOG if e["id"]==execution_id),None)
 if not x:return jsonify(error="execution not found"),404
 return jsonify(ok=True,execution=x)

@app.post("/api/admin/ai/executions/<execution_id>/outcome")
def record_ai_execution_outcome(execution_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 x=next((e for e in AI_EXECUTION_LOG if e["id"]==execution_id),None)
 if not x:return jsonify(error="execution not found"),404
 b=request.get_json(silent=True) or {};x["outcome"]={"result":b.get("result"),"metrics":b.get("metrics") or {},"recorded_at":datetime.datetime.utcnow().isoformat()+"Z"};x["status"]="OUTCOME_RECORDED"
 return jsonify(ok=True,execution=x)

AI_LEARNING_MEMORY=[]

@app.post("/api/admin/ai/executions/<execution_id>/learn")
def learn_from_ai_execution(execution_id):
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 x=next((e for e in AI_EXECUTION_LOG if e["id"]==execution_id),None)
 if not x:return jsonify(error="execution not found"),404
 if x.get("status")!="OUTCOME_RECORDED":return jsonify(error="outcome must be recorded before learning"),409
 if x.get("learning_id"):return jsonify(ok=True,learning=next((l for l in AI_LEARNING_MEMORY if l["id"]==x["learning_id"]),None),already_learned=True)
 outcome=x.get("outcome") or {};metrics=outcome.get("metrics") or {};result=str(outcome.get("result") or "")
 lesson="Execution outcome recorded; future decisions should consider this action and measured result."
 if any(v is not None and isinstance(v,(int,float)) and v<0 for v in metrics.values()):lesson="Negative measurable outcome detected; reduce confidence in similar future actions unless new evidence contradicts it."
 elif result:lesson="Observed outcome: "+result[:500]
 lid="learn_"+uuid.uuid4().hex[:12];learning={"id":lid,"execution_id":execution_id,"recommendation_id":x.get("recommendation_id"),"action":x.get("action"),"lesson":lesson,"outcome":outcome,"created_at":datetime.datetime.utcnow().isoformat()+"Z"}
 AI_LEARNING_MEMORY.append(learning);x["learning_id"]=lid;x["status"]="LEARNED"
 return jsonify(ok=True,learning=learning)

@app.get("/api/admin/ai/memory/learning")
def ai_learning_memory():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,count=len(AI_LEARNING_MEMORY),memories=AI_LEARNING_MEMORY)

@app.post("/api/admin/ai/memory/recall")
def recall_ai_learning():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 b=request.get_json(silent=True) or {};query=str(b.get("query","")).lower()
 matches=[m for m in AI_LEARNING_MEMORY if not query or query in (str(m.get("action",""))+" "+str(m.get("lesson",""))+" "+str(m.get("outcome",""))).lower()]
 return jsonify(ok=True,count=len(matches),recalled=matches[:20],instruction="Use recalled outcomes as historical evidence, not deterministic truth.")

@app.get("/api/admin/ai/learning/summary")
def ai_learning_summary():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,total_lessons=len(AI_LEARNING_MEMORY),executions_with_outcomes=sum(1 for x in AI_EXECUTION_LOG if x.get("outcome")),learning_loop_status="ACTIVE")

AI_PERSIST_TABLES=["ai_recommendations","ai_executions","ai_learning_memory","ai_event_history"]

def ai_persist_status(token):
 checks={}
 for table in AI_PERSIST_TABLES:
  r=supabase_request(table+"?select=id&limit=1",token=token)
  checks[table]=not (isinstance(r,dict) and "_error" in r)
 return checks

@app.get("/api/admin/ai/persistence/status")
def ai_persistence_status():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 checks=ai_persist_status(auth.split(" ",1)[1])
 return jsonify(ok=True,tables=checks,persistent_tables=sum(1 for v in checks.values() if v),required_tables=AI_PERSIST_TABLES,ready=all(checks.values()))

@app.post("/api/admin/ai/persistence/export")
def ai_persistence_export():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,schema_version=1,exported_at=datetime.datetime.utcnow().isoformat()+"Z",data={"recommendations":AI_RECOMMENDATIONS,"executions":AI_EXECUTION_LOG,"learning_memory":AI_LEARNING_MEMORY,"event_routing":AI_EVENT_ROUTING})

@app.get("/api/admin/ai/persistence/schema")
def ai_persistence_schema():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,postgres_schema={
 "ai_recommendations":["id uuid primary key","title text","recommendation text","source_event text","risk text","confidence numeric","status text","decision jsonb","created_at timestamptz"],
 "ai_executions":["id uuid primary key","recommendation_id text","action text","status text","payload jsonb","outcome jsonb","created_at timestamptz"],
 "ai_learning_memory":["id uuid primary key","execution_id text","recommendation_id text","action text","lesson text","outcome jsonb","created_at timestamptz"],
 "ai_event_history":["id uuid primary key","event_type text","data jsonb","priority text","agent text","created_at timestamptz"]})

def ai_db_write(table,row,token):
 r=supabase_request(table,method="POST",body=row,token=token,prefer="return=representation")
 return not (isinstance(r,dict) and "_error" in r)

@app.post("/api/admin/ai/persistence/verify")
def verify_ai_persistence():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 checks=ai_persist_status(auth.split(" ",1)[1])
 return jsonify(ok=all(checks.values()),checks=checks,next_step="persistent_ai_storage_ready" if all(checks.values()) else "run_supabase_migration")

@app.get("/api/admin/ai/persistence/migration")
def ai_persistence_migration_info():
 auth=request.headers.get("Authorization","")
 if not auth.startswith("Bearer "):return jsonify(error="Unauthorized"),401
 return jsonify(ok=True,migration_file="supabase/migrations/20260906_ai_persistence.sql",tables=AI_PERSIST_TABLES)

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
