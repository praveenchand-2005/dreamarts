from flask import Flask,request,jsonify,send_from_directory
from werkzeug.utils import secure_filename
import os,json,uuid,time

BASE=os.path.dirname(__file__)
DATA=os.path.join(BASE,"data"); UP=os.path.join(BASE,"uploads")
for d in(DATA,UP): os.makedirs(d,exist_ok=True)

app=Flask(__name__,static_folder="static",static_url_path="")
app.config["MAX_CONTENT_LENGTH"]=15*1024*1024
ALLOWED={"jpg","jpeg","png","webp"}

def save_json(path,obj):
 with open(path,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False,indent=2)

def load_orders():
 out=[]
 for fn in os.listdir(DATA):
  if fn.startswith("order_") and fn.endswith(".json"):
   try: out.append(json.load(open(os.path.join(DATA,fn),encoding="utf-8")))
   except: pass
 return sorted(out,key=lambda x:x.get("createdAt",""),reverse=True)

@app.get("/")
def home(): return send_from_directory("static","index.html")
@app.get("/admin")
def admin(): return send_from_directory("static","admin.html")
@app.get("/uploads/<path:name>")
def uploads(name): return send_from_directory(UP,name)

@app.post("/api/custom-orders")
def custom_order():
 try:
  f=request.files.get("photo")
  if not f or not f.filename: return jsonify(error="Please upload a reference photo."),400
  ext=f.filename.rsplit(".",1)[-1].lower() if "." in f.filename else ""
  if ext not in ALLOWED: return jsonify(error="Use JPG, PNG or WEBP."),400
  oid="DA-"+time.strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:6].upper()
  filename=oid+"-"+secure_filename(f.filename)
  f.save(os.path.join(UP,filename))
  order={
   "orderNumber":oid,"createdAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
   "status":"NEW REQUEST",
   "customer":{"name":request.form.get("name","").strip(),"phone":request.form.get("phone","").strip(),"email":request.form.get("email","").strip()},
   "artwork":{"size":request.form.get("size","Custom"),"shape":request.form.get("shape","Circle"),"notes":request.form.get("notes","").strip(),"photo":"/uploads/"+filename}
  }
  save_json(os.path.join(DATA,"order_"+oid+".json"),order)
  return jsonify(ok=True,orderNumber=oid)
 except Exception as e: return jsonify(error=str(e)),500

@app.get("/api/orders")
def orders():
 return jsonify(load_orders())

@app.patch("/api/orders/<order_id>")
def update_order(order_id):
 path=os.path.join(DATA,"order_"+order_id+".json")
 if not os.path.exists(path): return jsonify(error="Order not found"),404
 order=json.load(open(path,encoding="utf-8")); body=request.get_json(silent=True) or {}
 if "status" in body: order["status"]=body["status"]
 save_json(path,order); return jsonify(order)

@app.get("/api/dashboard")
def dashboard():
 orders=load_orders()
 counts={}
 for o in orders: counts[o.get("status","UNKNOWN")]=counts.get(o.get("status","UNKNOWN"),0)+1
 return jsonify(total=len(orders),counts=counts,recent=orders[:8])

if __name__=="__main__":
 app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))