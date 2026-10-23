import fitz,pathlib,math,collections
PT=72/25.4
def segments(page):
 out=[]
 for d in page.get_drawings():
  if 'die' not in d.get('layer','').lower():continue
  for it in d['items']:
   if it[0]=='l':out.append((it[1].x,it[1].y,it[2].x,it[2].y))
   if it[0]=='re':
    r=it[1];out.extend([(r.x0,r.y0,r.x1,r.y0),(r.x1,r.y0,r.x1,r.y1),(r.x1,r.y1,r.x0,r.y1),(r.x0,r.y1,r.x0,r.y0)])
 return out
def clusters(vals,tol=1.8):
 groups=[]
 for x in sorted(vals):
  if groups and x-groups[-1][-1]<tol:groups[-1].append(x)
  else:groups.append([x])
 return [sum(g)/len(g) for g in groups]
def detect(pg,size):
 """Fit the five fold lines, including origins inferred from internal folds.

 Long vector edges anchor candidates. Short dashed strokes contribute support,
 but tiny outlined text cannot shift a fold coordinate by averaging its x.
 Overlapping/duplicate PDF paths count once, and near-identical fits are one
 candidate rather than a false ambiguity.
 """
 w,d,h=[x*PT for x in size]
 ss=segments(pg)
 vs=[(x,min(y,Y),max(y,Y)) for x,y,X,Y in ss if abs(x-X)<.15 and abs(y-Y)>.3]
 hs=[(y,min(x,X),max(x,X)) for x,y,X,Y in ss if abs(y-Y)<.15 and abs(x-X)>.3]
 if not vs or not hs:return []
 offsets=[0,w,w+d,2*w+d,2*w+2*d]
 span=offsets[-1]
 # The net may have one solid outer cut and four dashed folds. Infer its
 # left origin from every observed long edge, not just the first fold.
 anchors=[x for x,a,b in vs if b-a>=min(25,h*.3)]
 if not anchors:return []
 xs=clusters([x-off for x in anchors for off in offsets
              if x-off>=0 and x-off+span<=pg.rect.width+3],tol=.30)
 ys=[Y for Y,a,b in hs if b-a>=min(20,w*.3)]
 for x,a,b in vs:
  if abs((b-a)-h)<3:
   ys.extend([a,b-h])
 ys=clusters([Y for Y in ys if Y>=0 and Y+h<=pg.rect.height+3],tol=.30)
 tolerance=3.10
 def support(items,coordinate,lo,hi):
  intervals=sorted((max(a,lo),min(b,hi)) for c,a,b in items
                   if abs(c-coordinate)<tolerance and b>lo and a<hi)
  total=0.;end=-1e9
  for a,b in intervals:
   if b>end:
    total+=b-max(a,end);end=b
  return total
 res=[]
 for y in ys:
  for x in xs:
   xx=[x+off for off in offsets]
   vert=[support(vs,X,y,y+h) for X in xx]
   # All five boundaries must exist: this rules out a one-panel shifted net.
   if min(vert)<h*.22:continue
   hori=[support(hs,Y,x,x+span) for Y in [y,y+h]]
   if min(hori)<span*.18:continue
   score=sum(t/h for t in vert)+sum(t/span for t in hori)
   res.append((score,x,y,vert,hori))
 # Equivalent fits within approximately 1 mm differ only by vector rounding,
 # bleed or parallel strokes. Compare genuinely different panel origins.
 distinct=[]
 for item in sorted(res,reverse=True):
  if not any(abs(item[1]-r[1])<3 and abs(item[2]-r[2])<3 for r in distinct):
   distinct.append(item)
   if len(distinct)==2:break
 return distinct
def contours(pg):
 out=[]
 for d in pg.get_drawings():
  if 'die' not in d.get('layer','').lower():continue
  curr=[]
  for it in d['items']:
   if it[0] not in ('l','c'):continue
   pts=it[1:];start=pts[0];end=pts[-1]
   if curr and math.hypot(curr[-1][0]-start.x,curr[-1][1]-start.y)>.3:
    out.append(curr);curr=[]
   if not curr:curr.append((start.x,start.y))
   if it[0]=='l':curr.append((end.x,end.y))
   else:
    for i in range(1,21):
     t=i/20;s=1-t;curr.append((s**3*pts[0].x+3*s*s*t*pts[1].x+3*s*t*t*pts[2].x+t**3*pts[3].x,s**3*pts[0].y+3*s*s*t*pts[1].y+3*s*t*t*pts[2].y+t**3*pts[3].y))
   if len(curr)>3 and math.hypot(curr[-1][0]-curr[0][0],curr[-1][1]-curr[0][1])<.3:out.append(curr);curr=[]
  if curr:out.append(curr)
 return out

def holes(pg,x,y,w):
 chains=[c for c in contours(pg) if min(p[0] for p in c)>x-1 and max(p[0] for p in c)<x+w+1 and min(p[1] for p in c)>y-95 and max(p[1] for p in c)<y-1]
 while True:
  found=False
  for i,a in enumerate(chains):
   for j in range(i+1,len(chains)):
    b=chains[j]
    for ar,br in [(False,False),(False,True),(True,False),(True,True)]:
     aa=a[::-1] if ar else a;bb=b[::-1] if br else b
     if math.dist(aa[-1],bb[0])<2.0:
      chains[i]=aa+bb;chains.pop(j);found=True;break
    if found:break
   if found:break
  if not found:break
 return [c for c in chains if len(c)>15 and math.dist(c[0],c[-1])<2.0 and max(p[0] for p in c)-min(p[0] for p in c)>w*.4 and max(p[1] for p in c)-min(p[1] for p in c)>10]
