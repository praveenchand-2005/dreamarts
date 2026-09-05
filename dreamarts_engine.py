"""Dreamarts Engine V2 - adaptive string-art reconstruction."""
from io import BytesIO
from math import cos, sin, pi
import base64
import numpy as np
from PIL import Image, ImageDraw, ImageOps, ImageFilter

def _shape_points(shape, n):
    pts=[]
    shape=(shape or "Circle").lower()
    if shape=="circle":
        for i in range(n):
            t=2*pi*i/n
            pts.append((0.5+0.47*cos(t),0.5+0.47*sin(t)))
    elif shape=="heart":
        for i in range(n):
            t=2*pi*i/n
            xx=16*sin(t)**3
            yy=13*cos(t)-5*cos(2*t)-2*cos(3*t)-cos(4*t)
            pts.append((0.5+xx/38,0.47-yy/38))
    else:
        per=max(1,n//4)
        for i in range(n):
            q=i/n*4; side=int(q)%4; u=q-int(q)
            if side==0: pts.append((0.03+0.94*u,0.03))
            elif side==1: pts.append((0.97,0.03+0.94*u))
            elif side==2: pts.append((0.97-0.94*u,0.97))
            else: pts.append((0.03,0.97-0.94*u))
    return pts

def _mask(shape, size):
    yy,xx=np.mgrid[0:size,0:size]
    x=xx/(size-1); y=yy/(size-1)
    shape=(shape or "Circle").lower()
    if shape=="circle":
        return ((x-.5)**2+(y-.5)**2)<=.47**2
    if shape=="heart":
        X=(x-.5)*2; Y=(.5-y)*2
        return ((X*X+Y*Y-1)**3-X*X*Y**3)<=0
    return (x>.03)&(x<.97)&(y>.03)&(y<.97)

def _decode(data):
    if "," in data: data=data.split(",",1)[1]
    return Image.open(BytesIO(base64.b64decode(data))).convert("RGB")

def _prepare(data, size, shape, contrast):
    im=_decode(data)
    im=ImageOps.fit(im,(size,size),method=Image.Resampling.LANCZOS,centering=(.5,.5))
    g=np.asarray(ImageOps.grayscale(im),dtype=np.float32)/255.0
    # Local contrast: preserve facial midtones instead of crushing shadows.
    local=np.asarray(ImageOps.grayscale(im).filter(ImageFilter.GaussianBlur(5)),dtype=np.float32)/255.0
    enhanced=np.clip(g+(g-local)*0.55,0,1)
    dark=np.clip((1-enhanced)*contrast,0,1)
    # Sobel-like edge magnitude.
    gx=np.zeros_like(dark); gy=np.zeros_like(dark)
    gx[:,1:-1]=dark[:,2:]-dark[:,:-2]
    gy[1:-1,:]=dark[2:,:]-dark[:-2,:]
    edge=np.sqrt(gx*gx+gy*gy)
    if edge.max()>0: edge/=edge.max()
    mask=_mask(shape,size)
    target=np.where(mask,dark,0).astype(np.float32)
    importance=np.where(mask,0.72*target+0.28*edge,0).astype(np.float32)
    return target,importance,mask

def _line_pixels(a,b,size,mask):
    x0=int(a[0]*(size-1)); y0=int(a[1]*(size-1))
    x1=int(b[0]*(size-1)); y1=int(b[1]*(size-1))
    steps=max(abs(x1-x0),abs(y1-y0),1)
    xs=np.rint(np.linspace(x0,x1,steps+1)).astype(np.int32)
    ys=np.rint(np.linspace(y0,y1,steps+1)).astype(np.int32)
    valid=(xs>=0)&(xs<size)&(ys>=0)&(ys<size)
    xs=xs[valid]; ys=ys[valid]
    valid=mask[ys,xs]
    return ys[valid],xs[valid]

LINE_CACHE = {}

def _cached_line(a_idx,b_idx,pts,size,mask):
    key=(len(pts),size,a_idx,b_idx) if a_idx<b_idx else (len(pts),size,b_idx,a_idx)
    if key not in LINE_CACHE: LINE_CACHE[key]=_line_pixels(pts[a_idx],pts[b_idx],size,mask)
    return LINE_CACHE[key]

def generate(image_data, shape="Circle", nails=600, lines=4000, contrast=0.9, tone="black", preview=True, engine="dreamarts_adaptive"):
    """Engine adapter entrypoint. engine: dreamarts_adaptive, residual_greedy, edge_weighted."""
    size=180 if preview else 320
    nails=max(100,min(int(nails),1200))
    requested=max(500,min(int(lines),10000))
    # Preview remains bounded for Render Free, but optimizer reports actual optimum.
    max_lines=min(requested,3200 if preview else requested)
    target,importance,mask=_prepare(image_data,size,shape,float(contrast))
    if engine=="multiresolution":
        _,importance,mask,coarse,medium=_multires_target(image_data,shape,size,float(contrast))
        target=0.35*coarse+0.35*medium+0.30*target
    if engine=="public_precalc_greedy": importance=np.where(mask,0.65*target+0.35*importance,0).astype(np.float32)
    elif engine=="adaptive_coverage": importance=np.where(mask,0.55*target+0.45*importance,0).astype(np.float32)
    elif engine=="exploration_greedy": importance=np.where(mask,0.70*target+0.30*importance,0).astype(np.float32)
    if engine=="residual_greedy": importance=np.where(mask,target,0).astype(np.float32)
    elif engine=="edge_weighted": importance=np.where(mask,0.45*target+0.55*importance,0).astype(np.float32)
    pts=_shape_points(shape,nails)
    coverage=np.zeros_like(target)
    current=0; sequence=[]; cache={}
    rng=np.random.default_rng(42)
    no_gain=0
    alpha=0.018 if preview else 0.012
    for step in range(max_lines):
        # Hybrid candidate pool: evenly distributed + random exploration.
        gaps=np.linspace(max(2,nails//80),nails//2,70,dtype=int)
        random_gaps=rng.integers(max(2,nails//40),nails-2,30)
        candidates=np.unique((current+np.concatenate([gaps,random_gaps]))%nails)
        best=None; best_score=-1e9
        for cand in candidates:
            if cand==current: continue
            key=(current,int(cand))
            ys,xs=cache.get(key,(None,None))
            if ys is None:
                ys,xs=_cached_line(current,int(cand),pts,size,mask); cache[key]=(ys,xs)
            if len(xs)<4: continue
            residual=target[ys,xs]-coverage[ys,xs]
            gain=np.maximum(residual,0)*importance[ys,xs]
            over=np.maximum(coverage[ys,xs]-target[ys,xs],0)
            # Density governor + anti-black-spot + mild exploration.
            score=float(gain.mean()-1.8*over.mean()-0.22*coverage[ys,xs].mean())
            if engine=="adaptive_coverage": score-=0.40*float(np.maximum(coverage[ys,xs]-0.72,0).mean())
            if engine=="exploration_greedy": score+=0.018*abs(int(cand)-current)/max(1,nails//2)
            if len(sequence)>2 and cand==sequence[-2][0]: score-=0.12
            recent=[z for pair in sequence[-18:] for z in pair]
            if int(cand) in recent: score-=0.045
            # Public-engine-inspired recency buffer: discourage recently visited nails.
            recent=[z for pair in sequence[-18:] for z in pair]
            if int(cand) in recent: score-=0.045
            if score>best_score: best_score=score; best=(int(cand),ys,xs)
        if best is None or best_score<0.002:
            no_gain+=1
            current=(current+max(2,nails//3))%nails
            if no_gain>35: break
            continue
        no_gain=0
        cand,ys,xs=best
        # Soft physical accumulation. Never allow unlimited density.
        coverage[ys,xs]=np.minimum(1.0,coverage[ys,xs]+alpha*(1-0.35*coverage[ys,xs]))
        sequence.append((current,cand))
        current=cand
        if step>600 and step%120==0:
            err=float(np.mean((target[mask]-coverage[mask])**2))
            if err<0.0025: break
    # Render at presentation resolution.
    out=Image.new("RGB",(900,900),(239,233,223))
    draw=ImageDraw.Draw(out,"RGBA")
    rgb={"black":(20,18,16),"sepia":(78,48,29),"blue":(28,52,82),"red":(108,34,30)}.get(tone,(20,18,16))
    scale=900
    for a,b in sequence:
        p1=(int(pts[a][0]*(scale-1)),int(pts[a][1]*(scale-1)))
        p2=(int(pts[b][0]*(scale-1)),int(pts[b][1]*(scale-1)))
        draw.line([p1,p2],fill=rgb+(16,),width=1)
    # Boundary and tiny nail markers make the preview physically interpretable.
    if (shape or "").lower()=="circle":
        draw.ellipse((27,27,873,873),outline=(30,26,22,220),width=2)
    else:
        p=[(int(x*899),int(y*899)) for x,y in pts]
        draw.line(p+[p[0]],fill=(30,26,22,220),width=2)
    # encode
    bio=BytesIO(); out.save(bio,format="PNG",optimize=True)
    mse=float(np.mean((target[mask]-coverage[mask])**2))
    return {
        "engine":engine,
        "preview":"data:image/png;base64,"+base64.b64encode(bio.getvalue()).decode(),
        "sequence":sequence,
        "stats":{"requested_lines":requested,"generated_lines":len(sequence),"nails":nails,"mse":round(mse,5),"quality":"adaptive-density-governed"}
    }


def _multires_target(image_data, shape, size, contrast):
    target, importance, mask = _prepare(image_data,size,shape,float(contrast))
    # Coarse-to-fine pyramid: silhouette first, then detail.
    coarse=np.asarray(Image.fromarray((target*255).astype(np.uint8)).resize((max(24,size//4),max(24,size//4)),Image.Resampling.LANCZOS).resize((size,size),Image.Resampling.BILINEAR),dtype=np.float32)/255.0
    medium=np.asarray(Image.fromarray((target*255).astype(np.uint8)).resize((max(48,size//2),max(48,size//2)),Image.Resampling.LANCZOS).resize((size,size),Image.Resampling.BILINEAR),dtype=np.float32)/255.0
    return target, importance, mask, coarse, medium

def _perceptual_quality(result):
    st=result["stats"]
    mse=float(st.get("mse",1))
    used=max(1,int(st.get("generated_lines",1)))
    req=max(1,int(st.get("requested_lines",used)))
    # Proxy human-quality score: accuracy, useful-path efficiency and saturation safety.
    accuracy=max(0.0,1.0-min(1.0,mse*12.0))
    efficiency=min(1.0,used/req)
    density_safety=1.0-min(0.45,max(0.0,efficiency-0.85)*1.8)
    return round(0.68*accuracy+0.18*efficiency+0.14*density_safety,6)

def _quality_score(result):
    st=result["stats"]
    # Combined selector: reconstruction error + efficiency + density safety.
    mse=float(st.get("mse",1))
    used=max(1,int(st.get("generated_lines",1)))
    req=max(1,int(st.get("requested_lines",used)))
    efficiency=used/req
    return mse + 0.0015*max(0,efficiency-0.92) - 0.0008*_perceptual_quality(result)

def benchmark(image_data, shape="Circle", nails=600, lines=4000, contrast=0.9, tone="black"):
    """Run independent Dreamarts adapters and choose the lowest reconstruction error."""
    candidates=[]
    for name in ("dreamarts_adaptive","residual_greedy","edge_weighted","public_precalc_greedy","adaptive_coverage","exploration_greedy","multiresolution"):
        try:
            r=generate(image_data,shape,nails,lines,contrast,tone,True,name)
            candidates.append(r)
        except Exception:
            pass
    if not candidates: raise RuntimeError("No generation engine completed")
    # Lower residual MSE wins; future adapters can add perceptual scoring.
    scored=[{"engine":r["engine"],**r["stats"],"quality_score":_perceptual_quality(r)} for r in candidates]
    best=min(candidates,key=_quality_score)
    return best, sorted(scored,key=lambda x:x["quality_score"],reverse=True)
