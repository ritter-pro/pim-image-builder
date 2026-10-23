"""Local PIM image builder. Input files are read-only; output is a new PIM tree."""
from pathlib import Path
import csv, hashlib, io, json, math, os, re, shutil, threading, zipfile
import xml.etree.ElementTree as ET
import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageChops, ImageOps, ImageStat
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, NameObject
from pim_geometry import detect, PT
ROOT=Path(__file__).resolve().parent
FONT=ROOT/'assets'/'DejaVuSans.ttf'
VERSION='PIM-1.0.3-portfolio'
N=3000
class Cancelled(Exception):pass

def read_rows(path,sheet_name='NO'):
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as z:
        shared=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            shared=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)]
        sheet=next((s for s in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns) if s.get('name')==sheet_name),None)
        if sheet is None:raise ValueError('В таблице нет листа '+sheet_name)
        rid=sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        target=next(r.get('Target') for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels')) if r.get('Id')==rid)
        target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
        out=[]
        for row in ET.fromstring(z.read(target)).findall('.//s:sheetData/s:row',ns):
            rec={}
            for c in row.findall('s:c',ns):
                col=re.sub(r'\d','',c.get('r'));v=c.find('s:v',ns);v=v.text if v is not None else ''
                if c.get('t')=='s':v=shared[int(v)]
                if c.get('t')=='inlineStr':v=''.join(c.find('s:is',ns).itertext())
                rec[col]=v
            if not rec.get('A','').startswith('2027'):continue
            def size(col):return [float(t.replace(',','.')) for t in re.split(r'[*xх×]',rec[col])]
            out.append(dict(id=rec['A'],ean=rec['AM'].strip(),name=rec.get('B','').strip(),product_size=size('D'),size=size('E'),base=rec['S'].strip(),shape=rec['T'].strip(),euro=rec.get('L')=='YES'))
        if len({r['id'] for r in out})!=len(out):raise ValueError('В листе NO повторяются MatrixID')
        return out

class Source:
    def __init__(self,path,suffix):
        self.path=Path(path)
        self.zip=None
        if self.path.is_dir():self.entries=[str(p.relative_to(self.path)) for p in self.path.rglob('*') if p.is_file() and p.suffix.lower()==suffix]
        elif self.path.suffix.lower()=='.zip':
            self.zip=zipfile.ZipFile(self.path)
            self.entries=[i.filename for i in self.zip.infolist() if not i.is_dir() and Path(i.filename).suffix.lower()==suffix and '__MACOSX' not in i.filename]
        else:raise ValueError('Нужен ZIP-архив или папка: '+str(self.path))
    def read(self,name):return self.zip.read(name) if self.zip else (self.path/name).read_bytes()
    def close(self):
        if self.zip:self.zip.close()
    def match(self,rec):
        matches=[n for n in self.entries if rec['id'].lower() in Path(n).name.lower() or re.search(r'(?<!\d)'+re.escape(rec['ean'])+r'(?!\d)',Path(n).name)]
        if len(matches)>1:raise ValueError('Несколько файлов для '+rec['id']+': '+', '.join(matches))
        return matches[0] if matches else None
    def drawing(self,stem):
        found=[n for n in self.entries if Path(n).stem==stem]
        if len(found)!=1:raise ValueError('Не найден уникальный чертёж '+stem+'.pdf')
        return found[0]

def clean_pdf(data):
    reader=PdfReader(io.BytesIO(data));page=reader.pages[0]
    if len(reader.pages)!=1:raise ValueError('В развёртке должна быть одна страница')
    props=page['/Resources'].get('/Properties',{});excluded=set()
    for key,value in props.items():
        value=value.get_object();title=str(value.get('/Name',value.get('/Title',''))).lower()
        if 'die-cut' in title or 'die cut' in title:excluded.add(str(key))
    if not excluded:raise ValueError('Нет именованного слоя Die-cut; линии реза нельзя безопасно отделить')
    stream=ContentStream(page.get_contents(),reader);stack=[];operations=[];removed=0
    for operands,op in stream.operations:
        if op in (b'BDC',b'BMC'):
            suppressed=bool(stack and stack[-1]) or (op==b'BDC' and len(operands)>1 and str(operands[1]) in excluded)
            stack.append(suppressed)
            if suppressed:removed+=1
            else:operations.append((operands,op))
        elif op==b'EMC':
            if not stack:raise ValueError('Некорректные группы PDF')
            suppressed=stack.pop()
            if not suppressed:operations.append((operands,op))
        elif not (stack and stack[-1]):operations.append((operands,op))
    if stack or not removed:raise ValueError('Технический слой не удалён')
    stream.operations=operations;page[NameObject('/Contents')]=stream
    writer=PdfWriter();writer.add_page(page);buf=io.BytesIO();writer.write(buf)
    return fitz.open(stream=buf.getvalue(),filetype='pdf')

def save_png(im,path):
    path=Path(path);tmp=path.with_name(path.name+'.writing')
    with tmp.open('wb') as f:im.save(f,format='PNG',compress_level=6)
    os.replace(tmp,path)

def fitted(im,width=2520,height=2520,bg=(0,0,0,0)):
    im=im.convert('RGBA');bbox=im.getbbox()
    if not bbox:raise ValueError('Пустое изображение')
    im=im.crop(bbox);scale=min(width/im.width,height/im.height)
    im=im.resize((max(1,round(im.width*scale)),max(1,round(im.height*scale))),Image.Resampling.LANCZOS)
    canvas=Image.new('RGBA',(N,N),bg);canvas.alpha_composite(im,((N-im.width)//2,(N-im.height)//2));return canvas

def panel(page,rect):
    rect=fitz.Rect(rect)
    if not page.rect.contains(rect):raise ValueError('Грань выходит за страницу PDF')
    scale=min(2520/rect.width,2520/rect.height)
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False)
    im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples).convert('RGBA')
    interior=im.crop((round(im.width*.06),round(im.height*.06),round(im.width*.94),round(im.height*.94))).convert('RGB')
    st=ImageStat.Stat(interior)
    if min(st.mean)>249 and max(st.stddev)<2.5:raise ValueError('Вместо грани вырезалась пустая область')
    return fitted(im)

def net_origin(page,rec):
    matches=detect(page,rec['size'])
    if not matches or matches[0][0]<3.95:raise ValueError('Не удалось уверенно определить стенки развёртки')
    if len(matches)>1 and matches[0][0]-matches[1][0]<.2:raise ValueError('Неоднозначное расположение стенок развёртки')
    _,x,y,*_=matches[0]
    w,d,h=[t*PT for t in rec['size']]
    predicted=[x,x+w,x+w+d,x+2*w+d,x+2*w+2*d]
    # Snap each texture boundary to the actual dieline. Real cut widths may
    # differ slightly from nominal package dimensions in the workbook.
    from pim_geometry import segments
    vv=[(a,min(b,e),max(b,e)) for a,b,c,e in segments(page) if abs(a-c)<.15 and abs(e-b)>.3]
    grid=[]
    for X in predicted:
        cand=[]
        for a,b,e in vv:
            weight=max(0,min(e,y+h)-max(b,y))
            if abs(a-X)<4 and weight>.3:cand.append((a,weight))
        if cand:
            cand.sort();half=sum(t[1] for t in cand)/2;total=0
            for a,weight in cand:
                total+=weight
                if total>=half:grid.append(a);break
        else:grid.append(X)
    if not all(b>a for a,b in zip(grid,grid[1:])):raise ValueError('Неверный порядок границ стенок')
    return grid,y,y+h

def select_photo(page,y):
    draws=page.get_drawings();candidates=[]
    for info in page.get_image_info(xrefs=True):
        b=fitz.Rect(info['bbox'])
        if not info['xref'] or b.width<45 or b.height<45 or b.y0>=y-25 or (b.y0+b.y1)/2>=y:continue
        score=0
        for v in draws:
            r=v['rect'];fill=v.get('fill')
            if fill and b.contains(r) and max(fill)-min(fill)<.18 and sum(fill)/len(fill)<.78 and r.height>b.height*.025:
                score+=r.get_area()
        candidates.append((score,info))
    if not candidates:raise ValueError('Не найдена отдельная фотография лампочки над развёрткой')
    candidates.sort(key=lambda t:t[0],reverse=True)
    if candidates[0][0]<=0:raise ValueError('Не удалось определить фотографию с векторным логотипом')
    return candidates[0][1]

def gx_mask(im):
    # Exterior wood background in the supplied GX reference photos. Flood only
    # connected exterior, then retain the central product and its inner detail.
    rgb=np.asarray(im.convert('RGB'),dtype=np.int16)
    wood=(rgb[:,:,0]-rgb[:,:,1]>12)&(rgb[:,:,0]-rgb[:,:,2]>24)&(rgb[:,:,0]<205)&(rgb[:,:,1]<185)
    m=Image.fromarray(np.uint8(~wood)*255,'L')
    border=ImageDraw.Draw(m)
    border.rectangle((0,0,im.width-1,im.height-1),outline=0,width=1)
    cx,cy=im.width//2,im.height//2
    if m.getpixel((cx,cy))!=255:
        yy,xx=np.nonzero(np.asarray(m)==255)
        if not len(xx):raise ValueError('Не найден светлый корпус GX53')
        closest=np.argmin((xx-cx)**2+(yy-cy)**2);cx,cy=int(xx[closest]),int(yy[closest])
    ImageDraw.floodfill(m,(cx,cy),128,thresh=0)
    body=m.point(lambda p:255 if p==128 else 0)
    inv=ImageOps.invert(ImageOps.expand(body,border=1,fill=0))
    # Fill only exterior white region in inverse. Interior holes in the colour
    # segmentation are physical screws/pins and stay opaque in the product.
    ImageDraw.floodfill(inv,(0,0),128,thresh=0)
    body=inv.point(lambda p:0 if p==128 else 255).crop((1,1,im.width+1,im.height+1))
    # The GX53 housing is circular. Fit its photographed outer ellipse robustly
    # to reject wood grain and tiny branches left by colour segmentation.
    ar=np.asarray(body)>128;pts=[]
    for yy in range(2,im.height-2,3):
        xx=np.flatnonzero(ar[yy])
        if len(xx)>im.width*.35:
            for x in [xx[0],xx[-1]]:
                if 2<x<im.width-3:pts.append((x/im.width,yy/im.height))
    for xx in range(2,im.width-2,3):
        yy=np.flatnonzero(ar[:,xx])
        if len(yy)>im.height*.35:
            for y in [yy[0],yy[-1]]:
                if 2<y<im.height-3:pts.append((xx/im.width,y/im.height))
    pts=np.asarray(pts);best=None;best_count=0;rng=np.random.default_rng(42)
    if len(pts)<10:raise ValueError('Не удалось определить внешний контур GX53')
    x,y=pts.T;A=np.column_stack((y*y,x,y,np.ones(len(x))));target=-x*x
    for _ in range(250):
        idx=rng.choice(len(x),4,replace=False)
        try:coef=np.linalg.solve(A[idx],target[idx])
        except np.linalg.LinAlgError:continue
        aa,bb,cc,dd=coef
        if aa<=0:continue
        cx=-bb/2;cy=-cc/(2*aa);v=cx*cx+aa*cy*cy-dd
        if v<=0:continue
        rx=math.sqrt(v);ry=math.sqrt(v/aa)
        if not(.25<cx<.75 and .25<cy<.75 and .32<rx<.75 and .32<ry<.75):continue
        err=np.abs(((x-cx)/rx)**2+((y-cy)/ry)**2-1)
        good=err<.025;count=int(good.sum())
        if count>best_count:best_count=count;best=good
    if best is None:raise ValueError('Не удалось выделить круглый корпус GX53')
    aa,bb,cc,dd=np.linalg.lstsq(A[best],target[best],rcond=None)[0]
    cx=-bb/2;cy=-cc/(2*aa);v=cx*cx+aa*cy*cy-dd;rx=math.sqrt(v);ry=math.sqrt(v/aa)
    if not(.47<cx<.53 and .46<cy<.54 and .47<rx<.53 and .48<ry<.58):
        cx,cy,rx,ry=.5,.5,.5,.535
    result=Image.new('L',im.size);ImageDraw.Draw(result).ellipse(((cx-rx)*im.width,(cy-ry)*im.height,(cx+rx)*im.width,(cy+ry)*im.height),fill=255)
    return result

def photo(doc,original_page,clean_page,y,base):
    info=select_photo(original_page,y);rect=fitz.Rect(info['bbox']);scale=min(2800/rect.width,2800/rect.height)
    pix=clean_page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=(base!='GX53'))
    im=Image.frombytes('RGBA' if pix.alpha else 'RGB',(pix.width,pix.height),pix.samples).convert('RGBA')
    smask=next((r[1] for r in original_page.get_images(full=True) if r[0]==info['xref']),0)
    if smask and base!='GX53':
        mask=Image.open(io.BytesIO(doc.extract_image(smask)['image'])).convert('L').resize(im.size,Image.Resampling.LANCZOS)
        im.putalpha(ImageChops.multiply(im.getchannel('A'),mask))
    if base=='GX53':
        preview=im.copy();preview.thumbnail((700,700),Image.Resampling.LANCZOS)
        im.putalpha(gx_mask(preview).resize(im.size,Image.Resampling.LANCZOS))
    elif not smask:raise ValueError('У фото нет маски прозрачности; требуется проверка исходника')
    bbox=im.getbbox()
    if not bbox:raise ValueError('Пустая маска лампочки')
    return im.crop(bbox),dict(xref=info['xref'],bbox=list(rect),mask=smask)

def combined(lamp,pack):
    lamp=lamp.convert('RGBA');pack=pack.convert('RGBA');pack=pack.crop(pack.getbbox())
    lamp=lamp.crop(lamp.getbbox())
    # Both stand on one baseline; keep a generous central gap and exterior air.
    cell_w=1240;max_h=2460
    pieces=[]
    for im in [lamp,pack]:
        scale=min(cell_w/im.width,max_h/im.height)
        pieces.append(im.resize((round(im.width*scale),round(im.height*scale)),Image.Resampling.LANCZOS))
    canvas=Image.new('RGBA',(N,N));baseline=2730
    for im,center in zip(pieces,[790,2210]):canvas.alpha_composite(im,(round(center-im.width/2),baseline-im.height))
    return canvas

# Explicitly inspected outline families from the supplied 71 drawing PDFs.
TEMPLATES={
    'E14:P':[(80,'32503 1'),(88,'32505 5'),(90,'32506 2')],
    'E27:P':[(80,'32518 5'),(88,'32520 8'),(90,'32520 8')],
    'E14:C':[(100,'32508 6'),(110,'32510 9'),(112,'32510 9')],
    'E27:C':[(100,'32524 6'),(110,'32525 3'),(112,'32525 3')],
    'E14:CA':[(125,'32351 8')],
    'E27:A':[(105,'32266 5'),(108,'32266 5'),(118,'32403 4'),(135,'32419 5'),(142,'32419 5'),(153,'32419 5')],
    'GU5,3:-':[(56,'32540 6'),(60,'32540 6')],
    'GX53:-':[(25,'32595 6'),(27.5,'32595 6')]
}

def drawing_image(data,size):
    doc=fitz.open(stream=data,filetype='pdf');pg=doc[0]
    spans=[]
    for b in pg.get_text('dict')['blocks']:
        for line in b.get('lines',[]):
            for s in line['spans']:
                if re.search(r'\d.*мм',s['text']):spans.append((s,line['dir']))
    if len(spans)<2:raise ValueError('В чертеже не найдены редактируемые размеры')
    horiz=[(s,d) for s,d in spans if abs(d[0])>.8];vert=[(s,d) for s,d in spans if abs(d[1])>.8]
    if not horiz or not vert:raise ValueError('Не найдены горизонтальный и вертикальный размеры')
    # The bottom horizontal dimension is outer diameter; extra pin spacing
    # (e.g. 42 mm on GX53) stays as in the reference drawing.
    diameter=max(horiz,key=lambda v:v[0]['bbox'][3])
    height=max(vert,key=lambda v:v[0]['bbox'][2]-v[0]['bbox'][0])
    font=fitz.Font(fontfile=str(FONT))
    updates=[]
    for (s,d),value in [(diameter,size[0]),(height,size[1])]:
        label=(f'{value:g}'.replace('.',',')+' мм');r=fitz.Rect(s['bbox']);pg.add_redact_annot(r+(-1,-1,1,1),fill=(1,1,1));updates.append((s,d,label))
    pg.apply_redactions(images=0,graphics=0)
    pg.insert_font(fontname='PIMfont',fontfile=str(FONT))
    for s,d,label in updates:
        r=fitz.Rect(s['bbox']);fs=s['size'];length=font.text_length(label,fontsize=fs)
        if abs(d[0])>.8:point=((r.x0+r.x1-length)/2,s['origin'][1]);rotate=0
        else:point=(s['origin'][0],(r.y0+r.y1+length)/2);rotate=90
        pg.insert_text(point,label,fontsize=fs,fontname='PIMfont',color=(0,0,0),rotate=rotate)
    pix=pg.get_pixmap(matrix=fitz.Matrix(N/pg.rect.width,N/pg.rect.height),colorspace=fitz.csGRAY,alpha=False)
    gray=Image.frombytes('L',(pix.width,pix.height),pix.samples)
    # Preserve antialiasing while taking printed line grey to black.
    gray=gray.point(lambda p:0 if p<205 else round((p-205)*255/50))
    result=Image.new('RGB',(N,N),'white');result.paste(gray.convert('RGB'),((N-gray.width)//2,(N-gray.height)//2))
    doc.close();return result

def config_fingerprint(rec,pdf,pack,draw):
    h=hashlib.sha256(VERSION.encode()+json.dumps(rec,sort_keys=True).encode())
    for item in [pdf,pack,draw]:h.update(hashlib.sha256(item or b'').digest())
    return h.hexdigest()

def build(config,emit=lambda *a:None,cancel=None):
    cancel=cancel or threading.Event()
    catalog=read_rows(config['table']);pdfs=Source(config['pdfs'],'.pdf');packs=Source(config['packs'],'.png');draws=Source(config['drawings'],'.pdf')
    output=Path(config['output'])/'ПИМ';output.mkdir(parents=True,exist_ok=True)
    reports=[];count_full=count_partial=count_error=count_skip=0
    try:
        for i,rec in enumerate(catalog):
            if cancel.is_set():raise Cancelled()
            emit('progress',i,len(catalog),rec['id'])
            statuses={};notes=[];files={};folder=None
            try:
                pname=packs.match(rec);pdfname=pdfs.match(rec)
                if not pname:raise ValueError('Нет фото упаковки')
                folder=output/Path(pname).stem;folder.mkdir(exist_ok=True)
                stem=rec['id'][-4:];packdata=packs.read(pname);pdfdata=pdfs.read(pdfname) if pdfname else None
                family=rec['base']+':'+rec['shape'];template=None;drawdata=None
                if family in TEMPLATES:
                    template=min(TEMPLATES[family],key=lambda v:abs(v[0]-rec['product_size'][1]))[1]
                    drawdata=draws.read(draws.drawing(template))
                elif rec['base']=='E27' and rec['shape']=='-':
                    selected=Path(config['cylinder']) if config.get('cylinder') else ROOT/'assets'/'cylinder_E27_41x125.pdf'
                    drawdata=selected.read_bytes();template=selected.name
                stamp=folder/'.pim-state.json';signature=config_fingerprint(rec,pdfdata,packdata,drawdata)
                suffixes=['+','-2','-6','-8','-9','-10','-12+']
                expected=[folder/(stem+s+'.png') for s in suffixes]
                if not config.get('force') and stamp.exists() and all(p.exists() for p in expected):
                    state=json.loads(stamp.read_text(encoding='utf-8'))
                    if state.get('signature')==signature:
                        count_skip+=1;statuses={s:'УЖЕ ГОТОВО' for s in suffixes};emit('log',rec['id']+': пропущено, комплект уже готов');continue
                # Packaging is copied byte-for-byte: only filename changes.
                with Image.open(io.BytesIO(packdata)) as original:
                    if original.size!=(N,N):raise ValueError('Фото упаковки должно быть 3000 × 3000')
                    pack=original.convert('RGBA')
                (folder/(stem+'-8.png')).write_bytes(packdata);statuses['-8']='ГОТОВО'
                if drawdata:
                    try:save_png(drawing_image(drawdata,rec['product_size']),folder/(stem+'-12+.png'));statuses['-12+']='ГОТОВО'
                    except Exception as e:notes.append('Чертёж: '+str(e));statuses['-12+']='ОШИБКА'
                else:notes.append('В архиве нет подходящего чертежа цилиндра E27. Укажите отдельный PDF в программе.');statuses['-12+']='НЕТ ИСХОДНИКА'
                if pdfdata:
                    doc=fitz.open(stream=pdfdata,filetype='pdf');pg=doc[0];clean=None
                    try:
                        grid,y0,y1=net_origin(pg,rec);clean=clean_pdf(pdfdata);cp=clean[0]
                        # Back panel 0 is the complete text side. Its first row
                        # of pictograms is included, exactly as in the examples.
                        for suffix,rect in [('-6',[grid[3],y0,grid[4],y1]),('-9',[grid[2],y0,grid[3],y1]),('-10',[grid[0],y0,grid[1],y1])]:
                            try:save_png(panel(cp,rect),folder/(stem+suffix+'.png'));statuses[suffix]='ГОТОВО'
                            except Exception as e:statuses[suffix]='ОШИБКА';notes.append(suffix+': '+str(e))
                        try:
                            lamp,photo_meta=photo(doc,pg,cp,y0,rec['base'])
                            save_png(fitted(lamp),folder/(stem+'-2.png'));statuses['-2']='ГОТОВО'
                            save_png(combined(lamp,pack),folder/(stem+'+.png'));statuses['+']='ГОТОВО'
                        except Exception as e:notes.append('Лампочка: '+str(e));statuses.update({'+':'ОШИБКА','-2':'ОШИБКА'})
                    finally:
                        if clean:clean.close()
                        doc.close()
                else:
                    notes.append('Не найден PDF-развёртка');statuses.update({s:'НЕТ PDF' for s in ['+','-2','-6','-9','-10']})
                full=all(statuses.get(s)=='ГОТОВО' for s in suffixes)
                if full:
                    stamp.write_text(json.dumps({'signature':signature,'id':rec['id'],'drawing':template},ensure_ascii=False,indent=2),encoding='utf-8');count_full+=1
                else:count_partial+=1
                emit('log',rec['id']+(': готово 7/7' if full else ': неполный комплект — '+'; '.join(notes)))
            except Exception as e:
                count_error+=1;notes.append(str(e));emit('log',rec['id']+': ОШИБКА — '+str(e))
            finally:
                reports.append([rec['id'],rec['ean'],str(folder or ''),*['%s'%statuses.get(s,'НЕ СОЗДАНО') for s in ['+','-2','-6','-8','-9','-10','-12+']],'; '.join(notes)])
                # A usable report is always present, even if cancelled midway.
                with (output/'ОТЧЕТ.csv').open('w',encoding='utf-8-sig',newline='') as f:
                    writer=csv.writer(f,delimiter=';');writer.writerow(['MatrixID','ШК','Папка','Лампа+упаковка','Лампа','ТЭФ','Упаковка','Лицо','Характеристики','Чертёж','Примечание']);writer.writerows(reports)
                emit('progress',i+1,len(catalog),rec['id'])
        result=dict(complete=count_full,partial=count_partial,errors=count_error,skipped=count_skip,total=len(catalog),output=str(output))
        (output/'ИТОГ.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');emit('done',result);return result
    finally:
        pdfs.close();packs.close();draws.close()
