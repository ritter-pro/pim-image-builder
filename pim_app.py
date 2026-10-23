import json, os, queue, subprocess, sys, threading, time, traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from pim_engine import read_rows,Source,TEMPLATES,build,Cancelled
ROOT=Path(__file__).resolve().parent

class App(tk.Tk):
    def __init__(self):
        super().__init__();self.title('PIM Image Builder • подготовка изображений');self.geometry('1050x830');self.minsize(920,760)
        self.configure(bg='#F4F2F8');self.events=queue.Queue();self.cancel_event=threading.Event();self.busy=False;self.started=0
        self.paths={k:tk.StringVar() for k in ['pdfs','packs','drawings','table','cylinder','output']};self.force=tk.BooleanVar(value=False)
        settings=ROOT/'settings.json'
        if settings.exists():
            try:
                for k,v in json.loads(settings.read_text(encoding='utf-8')).items():
                    if k in self.paths:self.paths[k].set(v)
            except Exception:pass
        if not self.paths['output'].get():self.paths['output'].set(str(Path.home()/'Desktop'/'ПИМ_результат'))
        style=ttk.Style(self);style.theme_use('clam');style.configure('TFrame',background='#F4F2F8');style.configure('TLabel',background='#F4F2F8',font=('Segoe UI',10),foreground='#282334')
        style.configure('Title.TLabel',font=('Segoe UI',25,'bold'));style.configure('Hint.TLabel',font=('Segoe UI',9),foreground='#686171')
        style.configure('TButton',font=('Segoe UI',10),padding=(12,8));style.configure('Accent.TButton',background='#6C43AA',foreground='white');style.map('Accent.TButton',background=[('active','#59358F')])
        style.configure('TCheckbutton',background='#F4F2F8',font=('Segoe UI',10));style.configure('TProgressbar',background='#8051BE',troughcolor='#E1DAEB')
        main=ttk.Frame(self,padding=24);main.pack(fill='both',expand=True)
        ttk.Label(main,text='ПИМ',style='Title.TLabel').pack(anchor='w')
        ttk.Label(main,text='Семь изображений на артикул · 3000 × 3000 · структура папок готова для загрузки',style='Hint.TLabel').pack(anchor='w',pady=(3,17))
        grid=ttk.Frame(main);grid.pack(fill='x');grid.columnconfigure(1,weight=1)
        rows=[('pdfs','PDF-развёртки','archive'),('packs','Фото упаковки','archive'),('drawings','Чертежи «Лампочки»','archive'),('table','Таблица «Список!»','table'),('cylinder','Другой чертёж цилиндра','pdf'),('output','Куда сохранить результат','folder')]
        for i,(key,label,kind) in enumerate(rows):
            ttk.Label(grid,text=label,width=24).grid(row=i,column=0,sticky='w',pady=5)
            ttk.Entry(grid,textvariable=self.paths[key],font=('Segoe UI',10)).grid(row=i,column=1,sticky='ew',padx=(0,8),pady=5,ipady=5)
            ttk.Button(grid,text='Выбрать',command=lambda k=key,t=kind:self.browse(k,t)).grid(row=i,column=2,pady=5)
            if kind=='archive':ttk.Button(grid,text='Папку',command=lambda k=key:self.browse(k,'folder')).grid(row=i,column=3,padx=(6,0),pady=5)
        ttk.Label(main,text='Чертёж цилиндра E27 41 × 125 мм уже встроен. Дополнительный PDF выбирайте только для замены встроенного.',style='Hint.TLabel',wraplength=940).pack(anchor='w',pady=(8,12))
        options=ttk.Frame(main);options.pack(fill='x')
        ttk.Checkbutton(options,text='Пересобрать все выбранные позиции, даже если готовый комплект уже есть',variable=self.force).pack(anchor='w')
        buttons=ttk.Frame(main);buttons.pack(fill='x',pady=(15,14))
        self.check_button=ttk.Button(buttons,text='Проверить исходники',command=lambda:self.launch(True));self.check_button.pack(side='left')
        self.start_button=ttk.Button(buttons,text='Создать ПИМ',style='Accent.TButton',command=lambda:self.launch(False));self.start_button.pack(side='left',padx=10)
        self.stop_button=ttk.Button(buttons,text='Остановить после артикула',state='disabled',command=self.stop);self.stop_button.pack(side='left')
        ttk.Button(buttons,text='Открыть результат',command=self.open_output).pack(side='right')
        self.status=tk.StringVar(value='Выберите четыре источника и папку результата. Архивы можно не распаковывать.')
        ttk.Label(main,textvariable=self.status,wraplength=960).pack(anchor='w',pady=(0,8))
        self.progress=ttk.Progressbar(main,maximum=100);self.progress.pack(fill='x')
        self.log=ScrolledText(main,height=13,font=('Consolas',10),bg='white',fg='#282334',relief='flat',padx=12,pady=12);self.log.pack(fill='both',expand=True,pady=(12,8));self.log.configure(state='disabled')
        self.log.tag_configure('error',foreground='#A52E38');self.log.tag_configure('good',foreground='#326745')
        ttk.Label(main,text='Имена: 0000+ · 0000-2 · 0000-6 · 0000-8 · 0000-9 · 0000-10 · 0000-12+     |     Готовые файлы исходников не изменяются.',style='Hint.TLabel').pack(anchor='w')
        self.after(100,self.poll);self.protocol('WM_DELETE_WINDOW',self.close)
    def browse(self,key,kind):
        if self.busy:return
        if kind=='folder':path=filedialog.askdirectory(title='Выберите папку')
        else:
            filters={'archive':[('ZIP-архив','*.zip')],'table':[('Excel','*.xlsx')],'pdf':[('PDF чертёж','*.pdf')]}
            path=filedialog.askopenfilename(title='Выберите исходный файл',filetypes=filters[kind])
        if path:self.paths[key].set(path)
    def append(self,text):
        tag='error' if any(s in text.lower() for s in ['ошибка','неполный','нет подходящего','не найден']) else 'good'
        self.log.configure(state='normal');self.log.insert('end',text+'\n',tag);self.log.see('end');self.log.configure(state='disabled')
    def launch(self,check_only):
        if self.busy:return
        cfg={k:v.get().strip() for k,v in self.paths.items()};cfg['force']=self.force.get()
        for key in ['pdfs','packs','drawings','table','output']:
            if not cfg[key]:messagebox.showerror('Не заполнено','Укажите: '+key);return
        for key in ['pdfs','packs','drawings','table']:
            if not Path(cfg[key]).exists():messagebox.showerror('Не найдено','Не найден путь:\n'+cfg[key]);return
        if cfg['cylinder'] and not Path(cfg['cylinder']).is_file():messagebox.showerror('Не найдено','Не найден чертёж цилиндра');return
        (ROOT/'settings.json').write_text(json.dumps({k:cfg[k] for k in self.paths},ensure_ascii=False,indent=2),encoding='utf-8')
        self.busy=True;self.cancel_event.clear();self.started=time.monotonic();self.progress['value']=0
        self.start_button.configure(state='disabled');self.check_button.configure(state='disabled');self.stop_button.configure(state='normal')
        self.append('Проверка исходников…' if check_only else 'Начинаю сборку. Источники открываются только для чтения.')
        threading.Thread(target=self.worker,args=(cfg,check_only),daemon=True).start()
    def worker(self,cfg,check_only):
        try:
            if check_only:
                cat=read_rows(cfg['table']);sources=[Source(cfg[k],s) for k,s in [('pdfs','.pdf'),('packs','.png'),('drawings','.pdf')]]
                try:
                    pdf_ok=sum(bool(sources[0].match(r)) for r in cat);pack_ok=sum(bool(sources[1].match(r)) for r in cat)
                    draw_ok=0
                    for r in cat:
                        family=r['base']+':'+r['shape']
                        if family in TEMPLATES:
                            stem=min(TEMPLATES[family],key=lambda v:abs(v[0]-r['product_size'][1]))[1];sources[2].drawing(stem);draw_ok+=1
                        elif r['base']=='E27' and r['shape']=='-':draw_ok+=1
                    self.events.put(('log',f'Позиций NO: {len(cat)}. PDF: {pdf_ok}/{len(cat)}. Фото упаковки: {pack_ok}/{len(cat)}. Чертежи: {draw_ok}/{len(cat)}.'))
                    if draw_ok<len(cat):self.events.put(('log','Неполный набор чертежей: для цилиндров E27 укажите отдельный PDF. Остальные изображения будут собраны.'))
                    self.events.put(('checked',))
                finally:
                    for s in sources:s.close()
            else:build(cfg,lambda *a:self.events.put(a),self.cancel_event)
        except Cancelled:self.events.put(('cancelled',))
        except Exception as e:
            (ROOT/'last_error.log').write_text(traceback.format_exc(),encoding='utf-8');self.events.put(('failed',str(e)))
    def poll(self):
        try:
            while True:
                event=self.events.get_nowait();kind=event[0]
                if kind=='log':self.append(event[1])
                elif kind=='progress':
                    current,total,sku=event[1:];self.progress['value']=100*current/max(1,total);self.status.set(f'{current} / {total} · {sku} · прошло {int(time.monotonic()-self.started)//60} мин')
                elif kind in ['done','checked','failed','cancelled']:
                    self.busy=False;self.start_button.configure(state='normal');self.check_button.configure(state='normal');self.stop_button.configure(state='disabled')
                    if kind=='done':
                        r=event[1];msg=f"Полных новых комплектов: {r['complete']}. Уже готовых: {r['skipped']}. Неполных: {r['partial']}. Ошибок: {r['errors']}."
                        self.status.set(msg);self.append(msg);messagebox.showinfo('Обработка завершена',msg+'\n\nПодробности — ПИМ/ОТЧЕТ.csv')
                    elif kind=='checked':self.status.set('Проверка завершена. Результат — в журнале. Можно запускать сборку.')
                    elif kind=='cancelled':self.status.set('Остановлено. Готовые изображения сохранены. Можно продолжить повторным запуском.')
                    else:self.status.set('Ошибка: '+event[1]);self.append('Ошибка: '+event[1]);messagebox.showerror('Ошибка',event[1]+'\nПодробности — last_error.log рядом с программой.')
        except queue.Empty:pass
        self.after(100,self.poll)
    def stop(self):self.cancel_event.set();self.status.set('Остановлю обработку после текущего артикула…')
    def open_output(self):
        p=Path(self.paths['output'].get())/'ПИМ'
        if not p.exists():messagebox.showinfo('Результат','Папка ПИМ ещё не создана.');return
        if sys.platform=='win32':os.startfile(str(p))
        elif sys.platform=='darwin':subprocess.Popen(['open',str(p)])
        else:subprocess.Popen(['xdg-open',str(p)])
    def close(self):
        if self.busy:
            if not messagebox.askyesno('Идёт обработка','Остановить после текущего артикула? Окно закроется после завершения обработки.'):return
            self.stop();self.after(200,self.wait_close)
        else:self.destroy()
    def wait_close(self):
        if self.busy:self.after(200,self.wait_close)
        else:self.destroy()
if __name__=='__main__':App().mainloop()
