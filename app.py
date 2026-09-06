from flask import Flask,request,jsonify,send_from_directory
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from werkzeug.utils import secure_filename
import os,json,uuid,time,hmac,hashlib,base64,html,urllib.parse
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
