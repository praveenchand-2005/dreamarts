from flask import Flask,request,jsonify,send_from_directory
from werkzeug.utils import secure_filename
import os,json,uuid,time

BASE=os.path.dirname(__file__)
DATA=os.path.join(BASE,"data"); UP=os.path.join(BASE,"uploads")
for d in(DATA,UP): os.makedirs(d,exist_ok=True)
app=Flask(__name__,static_folder="static",static_url_path="")
app.config["MAX_CONTENT_LENGTH"]=15*1024*1024
ALLOWED={"jpg","jpeg","png","webp"}
FLOW=["NEW REQUEST","PHOTO REVIEW","QUOTE SENT","AWAITING APPROVAL","PAYMENT PENDING","PAID","IN PRODUCTION","QUALITY CHECK","SHIPPED","DELIVERED","CANCELLED"]

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