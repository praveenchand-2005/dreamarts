from flask import Flask,request,jsonify,send_from_directory
from PIL import Image,ImageOps,ImageEnhance,ImageFilter,ImageDraw
import os,math,uuid,json,time
BASE=os.path.dirname(__file__); DATA=os.path.join(BASE,"data"); UP=os.path.join(BASE,"uploads"); GEN=os.path.join(BASE,"generated")
for d in(DATA,UP,GEN):os.makedirs(d,exist_ok=True)
app=Flask(__name__,static_folder="static",static_url_path="")
def prep(path,s=96):
 im=Image.open(path).convert("L"); im=ImageOps.fit(im,(s,s)); im=ImageEnhance.Contrast(im).enhance(1.8); im=im.filter(ImageFilter.GaussianBlur(.35)); v=list(im.getdata()); return [[v[y*s+x]/255 for x in range(s)] for y in range(s)]
def plan(gray,w,h,n,l):
 s=len(gray); c=(s-1)/2; r=s*.49; target=[[0]*s for _ in range(s)]
 for y in range(s):
  for x in range(s):
   d=math.hypot(x-c,y-c); mask=1 if d<=r*.92 else max(0,min(1,(r-d)/(r*.08))); target[y][x]=(1-gray[y][x])*mask
 pts=[(.5+.47*math.cos(2*math.pi*i/n),.5+.47*math.sin(2*math.pi*i/n)) for i in range(n)]
 def px(a,b):
  out=[];seen=set()
  for k in range(52):
   t=k/51;x=round((a[0]+(b[0]-a[0])*t)*(s-1));y=round((a[1]+(b[1]-a[1])*t)*(s-1))
   if 0<=x<s and 0<=y<s and (x,y) not in seen:out.append((x,y));seen.add((x,y))
  return out
 rendered=[[0]*s for _ in range(s)]; route=[];cur=0;recent=[]
 for _ in range(l):
  best=None;bs=-1;bp=None
  for j in range(n):
   if j==cur or j in recent:continue
   gap=abs(j-cur)
   if gap<8 or gap>n-8:continue
   p=px(pts[cur],pts[j]);score=sum(max(0,target[y][x]-rendered[y][x]) for x,y in p)/len(p)
   if score>bs:bs=score;best=j;bp=p
  if best is None:break
  route.append((cur+1,best+1))
  for x,y in bp:rendered[y][x]=min(1,rendered[y][x]+.055)
  recent=(recent+[cur])[-10:];cur=best
 pid=str(uuid.uuid4());name=pid+".png";S=700;can=Image.new("RGB",(S,S),(245,240,232));dr=ImageDraw.Draw(can);rp=[(S/2+S*.445*math.cos(2*math.pi*i/n),S/2+S*.445*math.sin(2*math.pi*i/n)) for i in range(n)]
 for a,b in route:dr.line((*rp[a-1],*rp[b-1]),fill=(20,20,20),width=1)
 for x,y in rp:dr.ellipse((x-1,y-1,x+1,y+1),fill=(15,15,15))
 can.save(os.path.join(GEN,name));coords=[{"nail":i+1,"x_mm":round(w/2+(w/2-12)*math.cos(2*math.pi*i/n),3),"y_mm":round(h/2+(h/2-12)*math.sin(2*math.pi*i/n),3)} for i in range(n)]
 thread=sum(math.hypot(coords[b-1]["x_mm"]-coords[a-1]["x_mm"],coords[b-1]["y_mm"]-coords[a-1]["y_mm"]) for a,b in route)
 return {"planId":pid,"generatorVersion":"dreamarts-residual-v3","board":{"widthMm":w,"heightMm":h},"anchorNails":n,"stringLines":len(route),"thread":"black","estimatedThreadMeters":round(thread/1000,2),"preview":"/generated/"+name,"sequence":route,"nailCoordinates":coords}
@app.get("/")
def home():return send_from_directory("static","index.html")
@app.get("/generated/<path:name>")
def generated(name):return send_from_directory(GEN,name)
@app.post("/api/generate")
def generate():
 f=request.files.get("photo")
 if not f:return jsonify(error="Photo required"),400
 w=float(request.form.get("widthMm",500));h=float(request.form.get("heightMm",500));n=int(request.form.get("anchorNails",600));l=int(request.form.get("stringLines",4000))
 if not(100<=w<=2000 and 100<=h<=2000 and 100<=n<=1200 and 100<=l<=10000):return jsonify(error="Invalid settings"),400
 path=os.path.join(UP,str(uuid.uuid4())+"_"+f.filename);f.save(path);p=plan(prep(path),w,h,n,l);json.dump(p,open(os.path.join(DATA,p["planId"]+".json"),"w"));return jsonify(p)
@app.post("/api/orders")
def order():
 b=request.get_json();pid=b.get("planId");path=os.path.join(DATA,str(pid)+".json")
 if not pid or not os.path.exists(path):return jsonify(error="Unknown plan"),404
 p=json.load(open(path));oid="DA-"+time.strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:6].upper();o={"orderNumber":oid,"customer":b.get("customer",{}),"plan":p,"status":"New"};json.dump(o,open(os.path.join(DATA,"order_"+oid+".json"),"w"));return jsonify(orderNumber=oid,status="New")
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))
