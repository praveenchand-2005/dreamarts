from flask import Flask,request,jsonify,send_from_directory
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from werkzeug.utils import secure_filename
import os,json,uuid,time

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
