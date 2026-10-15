"""Native Tk radio settings, signal chart and reusable network plans."""
import csv
import json
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from .backend import parse_settings
from .dialogs import DestinationDialog
from .paths import data_directory
from .network import (validate_plan, desired_settings, provisioning_status,
                      save_session, load_session)


class Dashboard(ttk.Frame):
    colors = {'Local RSSI': '#177ddc', 'Remote RSSI': '#11a579',
              'Local noise': '#e69522', 'Remote noise': '#a56cc1'}

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.samples = []
        self.rows = {}
        self.original = {}
        self.plan = []
        self.staged_node = None
        self.plan_seeded = False
        self.saving = False
        self.session_path = data_directory() / 'network-session.json'
        self.session_ready = False
        self.session_timer = None
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True)
        settings = ttk.Frame(self.tabs, padding=12)
        rssi = ttk.Frame(self.tabs, padding=12)
        network = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(settings, text='Radio settings')
        self.tabs.add(rssi, text='RSSI & statistics')
        self.tabs.add(network, text='Network setup')
        self.rssi_tab, self.settings_tab = rssi, settings
        bar = ttk.Frame(settings)
        bar.pack(fill='x')
        ttk.Button(bar, text='Read connected radio', command=app.read_settings).pack(side='left')
        self.save_button = ttk.Button(bar, text='Upload settings & reboot', command=self.save)
        self.save_button.pack(side='left', padx=8)
        self.save_status = tk.StringVar(value='No settings save in this session')
        ttk.Label(settings, textvariable=self.save_status, wraplength=850).pack(anchor='w', pady=(8,3))
        self.save_progress = ttk.Progressbar(settings, maximum=4)
        self.save_progress.pack(fill='x')
        milestones = ttk.Frame(settings)
        milestones.pack(fill='x',pady=(3,5))
        self.save_milestones = []
        for title in ('Sent', 'Saved on radio', 'Reconnected', 'Verified'):
            var = tk.StringVar(value=title + ': pending')
            self.save_milestones.append((title,var))
            ttk.Label(milestones,textvariable=var).pack(side='left',padx=(0,18))
        ttk.Label(settings, text='Double-click a value to edit. Registers come from the connected firmware; S0 is read-only.').pack(anchor='w', pady=6)
        self.settings = ttk.Treeview(settings, columns=('register', 'name', 'value', 'edit'), show='headings', height=11)
        for key, title, width in [('register','Register',75), ('name','Setting',340), ('value','Radio value',120), ('edit','Pending value',120)]:
            self.settings.heading(key, text=title)
            self.settings.column(key, width=width, stretch=key == 'name')
        scroll = ttk.Scrollbar(settings, command=self.settings.yview)
        self.settings.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.settings.pack(fill='both', expand=True)
        self.settings.bind('<Double-1>', self.edit)
        bar = ttk.Frame(rssi)
        bar.pack(fill='x')
        self.start = ttk.Button(bar, text='Start monitoring', command=app.read_rssi)
        self.start.pack(side='left')
        self.stop = ttk.Button(bar, text='Stop', command=app.stop_rssi, state='disabled')
        self.stop.pack(side='left', padx=8)
        ttk.Button(bar, text='Export CSV…', command=self.export).pack(side='right')
        self.availability = tk.StringVar(value='Connect a radio to check RSSI availability')
        ttk.Label(rssi,textvariable=self.availability,wraplength=850).pack(anchor='w',pady=(8,2))
        self.signal_status = tk.StringVar(value='No samples · Values use the units reported by the firmware')
        ttk.Label(rssi, textvariable=self.signal_status).pack(anchor='w', pady=6)
        legend = ttk.Frame(rssi)
        legend.pack(fill='x')
        for name, color in self.colors.items():
            ttk.Label(legend, text='● ' + name, foreground=color).pack(side='left', padx=(0, 20))
        self.canvas = tk.Canvas(rssi, height=200, background='#101c29', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, pady=6)
        self.canvas.bind('<Configure>', lambda _: self.draw())
        self.stats = ttk.Treeview(rssi, columns=('metric','value'), show='headings', height=5)
        self.stats.heading('metric', text='All reported statistics')
        self.stats.heading('value', text='Latest value')
        scroll = ttk.Scrollbar(rssi, command=self.stats.yview)
        self.stats.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.stats.pack(fill='both', expand=True)
        self.raw = tk.StringVar(value='')
        ttk.Label(rssi, textvariable=self.raw, wraplength=900).pack(anchor='w', pady=5)
        bar = ttk.Frame(network)
        bar.pack(fill='x')
        self.profile = tk.StringVar(value='Async mesh')
        profile_combo = ttk.Combobox(bar, textvariable=self.profile, values=['Async mesh', 'SiK pair'], state='readonly', width=16)
        profile_combo.pack(side='left')
        profile_combo.bind('<<ComboboxSelected>>',lambda _:self.make_plan(ask=False))
        ttk.Button(bar, text='Create plan', command=self.make_plan).pack(side='left', padx=6)
        ttk.Button(bar, text='Add radio', command=self.add_radio).pack(side='left', padx=4)
        ttk.Button(bar, text='Remove selected', command=self.remove_radio).pack(side='left', padx=4)
        ttk.Button(bar, text='Open plan…', command=self.load_plan).pack(side='right')
        ttk.Button(bar, text='Export plan…', command=self.save_plan).pack(side='right', padx=6)
        shared = ttk.Frame(network)
        shared.pack(fill='x', pady=8)
        ttk.Label(shared,text='Group settings — shared by every radio in this plan',font=('Helvetica Neue',11,'bold')).grid(row=0,column=0,columnspan=5,sticky='w',pady=(0,5))
        self.shared = {}
        for i, (name, value) in enumerate([('NETID','25'), ('AIR_SPEED','64'), ('MIN_FREQ','915000'), ('MAX_FREQ','928000'), ('NUM_CHANNELS','20')]):
            ttk.Label(shared, text=name).grid(row=1,column=i,sticky='w',padx=4)
            var = tk.StringVar(value=value)
            ttk.Entry(shared, textvariable=var, width=12).grid(row=2,column=i,padx=4)
            self.shared[name] = var
        self.plan_info = tk.StringVar()
        ttk.Label(network, textvariable=self.plan_info, wraplength=900).pack(anchor='w', pady=6)
        self.nodes = ttk.Treeview(network, columns=('name','node','dest','status'), show='headings', height=7)
        for key, title in [('name','Radio'),('node','Node ID'),('dest','Destination ID'),('status','Provisioning')]:
            self.nodes.heading(key,text=title)
            self.nodes.column(key,width=180)
        self.nodes.pack(fill='both', expand=True)
        self.nodes.bind('<Double-1>', self.edit_node)
        self.nodes.bind('<<TreeviewSelect>>',lambda _:self.schedule_session())
        ttk.Label(network, text='Group edits apply to the whole plan. Connect each radio over USB, select its row, and upload to it. Double-click a row to edit destination.').pack(anchor='w',pady=6)
        actions = ttk.Frame(network)
        actions.pack(fill='x')
        self.upload_group_button = ttk.Button(actions, text='Upload group to selected radio…', command=self.upload_group)
        self.upload_group_button.pack(side='left')
        ttk.Button(actions, text='Review selected radio settings', command=self.stage_plan).pack(side='left',padx=8)
        self.local_status = tk.StringVar(value='Network plan is saved automatically on this computer')
        ttk.Label(network,textvariable=self.local_status,wraplength=900).pack(anchor='w',pady=(6,0))
        self.make_plan(ask=False)
        try:
            if self.session_path.exists():
                self.restore_plan(load_session(self.session_path))
                self.local_status.set('Restored last network plan · Group edits save automatically')
        except (ValueError,KeyError,TypeError,OSError) as exc:
            self.local_status.set('Could not restore last plan: '+str(exc))
        self.session_ready = True
        for var in self.shared.values():
            var.trace_add('write',self.group_changed)
        self.schedule_session()

    def session_data(self):
        return {'version':1,'profile':self.profile.get(),
                'shared':{k:v.get() for k,v in self.shared.items()},
                'radios':self.plan,'selected':list(self.nodes.selection())}

    def schedule_session(self):
        if not self.session_ready:
            return
        if self.session_timer is not None:
            self.after_cancel(self.session_timer)
        self.session_timer = self.after(350,self.flush_session)

    def flush_session(self):
        if self.session_timer is not None:
            self.after_cancel(self.session_timer)
            self.session_timer = None
        if not self.session_ready:
            return
        try:
            save_session(self.session_path,self.session_data())
            self.local_status.set('Plan saved locally '+time.strftime('%H:%M:%S')+' · Radio uploads are separate')
        except (ValueError,KeyError,TypeError,OSError) as exc:
            self.local_status.set('Local plan save failed: '+str(exc))

    def group_changed(self,*_):
        self.plan_seeded = True
        self.refresh_provisioning()
        self.schedule_session()

    def refresh_provisioning(self):
        shared = {k:v.get() for k,v in self.shared.items()}
        for index,node in enumerate(self.plan):
            key = str(index)
            if self.nodes.exists(key):
                self.nodes.set(key,'status',provisioning_status(self.profile.get(),shared,node))

    def restore_plan(self,data):
        self.profile.set(data['profile'])
        self.plan = data['radios']
        self.staged_node = None
        self.plan_seeded = True
        for key,var in self.shared.items():
            var.set(str(data['shared'][key]))
        self.render_plan()
        selected = [key for key in data.get('selected',[]) if self.nodes.exists(str(key))]
        if selected:
            self.nodes.selection_set(selected)
            self.nodes.see(selected[0])

    def show_settings(self, text):
        self.staged_node = None
        self.settings.delete(*self.settings.get_children())
        self.rows = {s.register:s for s in parse_settings(text)}
        self.original = {k:s.value for k,s in self.rows.items()}
        if self.rows and not self.plan_seeded:
            for setting in self.rows.values():
                if setting.name in self.shared:
                    self.shared[setting.name].set(setting.value)
            self.plan_seeded = True
        for key,s in self.rows.items():
            self.settings.insert('', 'end', iid=key, values=(key,s.name,s.value,s.value))

    def edit(self, event):
        if self.app.busy:
            return
        row = self.settings.identify_row(event.y)
        if not row or row == 'S0':
            return
        value = simpledialog.askstring('Edit setting', f'{row}: {self.rows[row].name}\nInteger value in firmware register units:', initialvalue=self.settings.item(row,'values')[3], parent=self)
        if value is not None:
            try:
                value = str(int(value))
            except ValueError:
                messagebox.showerror('Invalid value','Enter an integer.',parent=self)
                return
            values = list(self.settings.item(row,'values'))
            values[3] = value
            self.settings.item(row,values=values)

    def save(self):
        if self.app.busy or not self.app.client:
            return
        changes = {k:self.settings.item(k,'values')[3] for k in self.rows
                   if self.settings.item(k,'values')[3] != self.original[k]}
        if not self.rows:
            messagebox.showinfo('Read settings first','Read the connected radio before uploading settings.',parent=self)
            return
        review = '\n'.join(f'{k} {self.rows[k].name}: {self.original[k]} → {v}' for k,v in changes.items()) if changes else 'No values need changing. Save the current radio settings to permanent memory and verify them after reboot.'
        if not messagebox.askyesno('Save radio settings', review + '\n\nSave to the connected radio and reboot?',parent=self):
            return
        staged = self.staged_node
        target_node = self.plan[int(staged)] if staged is not None else None
        self.saving = True
        self.save_progress['value'] = 0
        for title,var in self.save_milestones:
            var.set(title + ': pending')
        self.save_status.set('Starting settings save…')
        def done(text):
            self.saving = False
            self.app.last_settings = text
            self.show_settings(text)
            self.app.show_identity(self.app.client.identity)
            self.app.phase_var.set('Settings saved · Reconnected · Readback verified')
            if target_node is not None and any(node is target_node for node in self.plan):
                target_node['applied'] = {s.name:s.value for s in parse_settings(text)}
                target_node['verified_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                self.refresh_provisioning()
                self.flush_session()
        self.app.run_task('Saving radio settings',lambda:self.app.client.save_settings(
            changes, lambda step,message:self.app.events.put(('settings_status',(step,message)))),done)

    def settings_status(self, step, message):
        self.save_status.set(message)
        self.save_progress['value'] = step
        now = time.strftime('%H:%M:%S')
        for index,(title,var) in enumerate(self.save_milestones,1):
            if step >= index and var.get().endswith('pending'):
                var.set(f'{title}: ✓ {now}')

    def settings_failed(self, error):
        if self.saving:
            self.saving = False
            self.save_status.set('Save stopped: ' + error)

    def sample(self, timestamp, values, raw):
        self.samples.append((timestamp,values,raw))
        # Bound memory for long sessions. CSV contains the latest 10,000 reports.
        self.samples = self.samples[-10000:]
        existing = set(self.stats.get_children())
        for key,value in values.items():
            if key in existing:
                self.stats.item(key,values=(key,value))
            else:
                self.stats.insert('','end',iid=key,values=(key,value))
        for key in existing - values.keys():
            self.stats.delete(key)
        self.raw.set(raw)
        self.signal_status.set(f'{len(self.samples):,} samples retained · {time.strftime("%H:%M:%S",time.localtime(timestamp))} · Firmware-reported units · Last 60 seconds')
        self.draw()

    def reset_signal(self):
        self.samples.clear()
        self.stats.delete(*self.stats.get_children())
        self.raw.set('')
        self.signal_status.set('No samples · Values use the units reported by the firmware')
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete('all')
        w,h = c.winfo_width(),c.winfo_height()
        left,right,top,bottom = 48,max(49,w-16),18,max(19,h-28)
        if not self.samples or w < 100:
            c.create_text(w/2,h/2,text=self.availability.get() if self.availability.get().startswith('Async ') else 'Start monitoring to plot live RSSI and noise',width=max(100,w-80),fill='#d6e1ef')
            return
        end = self.samples[-1][0]
        samples = [s for s in self.samples if s[0] >= end-60]
        ys = [v for _,values,_ in samples for key,v in values.items() if key in self.colors]
        low,high = min(ys),max(ys)
        pad = max(5,(high-low)*.12)
        low,high = low-pad,high+pad
        for i in range(5):
            y = top+(bottom-top)*i/4
            c.create_line(left,y,right,y,fill='#2a394c')
            c.create_text(left-6,y,text=f'{high-(high-low)*i/4:.0f}',anchor='e',fill='#b6c5d7')
        for seconds in (60,45,30,15,0):
            x = left+(right-left)*(60-seconds)/60
            c.create_text(x,bottom+15,text=f'−{seconds}s' if seconds else 'Now',fill='#b6c5d7')
        for key,color in self.colors.items():
            points = [(left+(right-left)*(ts-(end-60))/60, bottom-(vals[key]-low)/(high-low)*(bottom-top))
                      for ts,vals,_ in samples if key in vals]
            if len(points)>1:
                c.create_line(*[v for p in points for v in p],fill=color,width=2)
            if points:
                x,y = points[-1]
                c.create_oval(x-3,y-3,x+3,y+3,fill=color,outline=color)

    def export(self):
        if not self.samples:
            return
        path = filedialog.asksaveasfilename(parent=self,initialfile='rfd-rssi.csv',defaultextension='.csv')
        if path:
            fields = sorted({k for _,v,_ in self.samples for k in v})
            with open(path,'w',newline='') as f:
                writer = csv.DictWriter(f,fieldnames=['timestamp',*fields,'raw'])
                writer.writeheader()
                for ts,values,raw in self.samples:
                    writer.writerow({'timestamp':ts,**values,'raw':raw})

    def make_plan(self, ask=True):
        count = 2 if self.profile.get() == 'SiK pair' else (simpledialog.askinteger('Async network','Initial radio count (2–32767). You can also add radios later:',initialvalue=2,minvalue=2,maxvalue=32767,parent=self) if ask else 2)
        if not count:
            return
        self.plan = [{'name':f'Radio {i}', 'node':i, 'dest':(3-i if count==2 else 32768)} for i in range(1,count+1)]
        self.staged_node = None
        # A new plan starts from this radio's current shared RF parameters.
        for setting in self.rows.values():
            if setting.name in self.shared:
                self.shared[setting.name].set(setting.value)
        self.render_plan()

    def add_radio(self):
        if self.profile.get() != 'Async mesh':
            messagebox.showinfo('SiK pair','SiK pairs use two radios. Select Async mesh and create a plan to add more nodes.',parent=self)
            return
        used = {int(n['node']) for n in self.plan}
        node_id = next((n for n in range(1,32768) if n not in used),None)
        if node_id is None:
            messagebox.showinfo('Address space full','Every Async node ID from 1 through 32767 is already allocated.',parent=self)
            return
        if len(self.plan)==2:
            if messagebox.askyesno('Group destinations','Use broadcast destination 32768 for all existing nodes so every radio can send to the group? Re-save those radios if already configured.',parent=self):
                for node in self.plan:
                    node['dest']=32768
        self.plan.append({'name':f'Radio {node_id}','node':node_id,'dest':32768})
        self.staged_node=None
        self.render_plan()
        item=str(len(self.plan)-1)
        self.nodes.selection_set(item)
        self.nodes.see(item)

    def remove_radio(self):
        if self.profile.get() != 'Async mesh':
            return
        selected=self.nodes.selection()
        if not selected:
            return
        if len(self.plan)<=2:
            messagebox.showinfo('Minimum group size','Keep at least two radios in a communication plan.',parent=self)
            return
        for index in sorted(map(int,selected),reverse=True):
            del self.plan[index]
        self.staged_node=None
        self.render_plan()

    def render_plan(self):
        self.plan_info.set(f'{len(self.plan):,} radios · Async node IDs 1–32767; destination 32768 broadcasts. Address capacity is not a simultaneous traffic guarantee.\nMatch firmware and shared parameters, then configure each radio over USB. A SiK pair uses two radios.')
        self.nodes.delete(*self.nodes.get_children())
        for i,node in enumerate(self.plan):
            self.nodes.insert('','end',iid=str(i),values=(node['name'],node['node'] if self.profile.get()=='Async mesh' else '—',node['dest'] if self.profile.get()=='Async mesh' else '—','Needs upload'))
        self.refresh_provisioning()
        self.schedule_session()

    def edit_node(self,event):
        row = self.nodes.identify_row(event.y)
        if not row or self.profile.get()!='Async mesh':
            return
        editor=DestinationDialog(self,self.plan[int(row)])
        self.wait_window(editor)
        value=editor.result
        if value is not None:
            self.plan[int(row)]['dest']=value
            self.render_plan()
            self.nodes.selection_set(row)
            self.nodes.see(row)
            self.flush_session()

    def stage_plan(self):
        if self.app.busy:
            messagebox.showinfo('Radio is busy','Stop RSSI monitoring or wait for the current operation before uploading settings.',parent=self)
            return
        if not self.app.identity or not self.rows:
            messagebox.showinfo('Read radio first','Connect and read settings for the radio you want to configure.',parent=self)
            return
        selected = self.nodes.selection()
        if not selected:
            messagebox.showinfo('Select a radio','Select the plan row for the radio connected over USB.',parent=self)
            return
        async_mode = self.profile.get()=='Async mesh'
        if async_mode != ('ASYNC' in self.app.identity.version.upper()):
            messagebox.showerror('Firmware mismatch','Install the firmware matching this network plan before applying it.',parent=self)
            return
        if not async_mode and 'SIK' not in self.app.identity.version.upper():
            messagebox.showerror('Firmware mismatch','A SiK pair requires SiK firmware.',parent=self)
            return
        try:
            wanted = desired_settings(self.profile.get(),{k:v.get() for k,v in self.shared.items()},self.plan[int(selected[0])])
        except (ValueError,KeyError,TypeError) as exc:
            messagebox.showerror('Invalid group settings',str(exc),parent=self)
            return
        by_name = {s.name:s.register for s in self.rows.values()}
        missing = wanted.keys()-by_name.keys()
        if missing:
            messagebox.showerror('Unsupported plan fields','Firmware does not report: '+', '.join(sorted(missing)),parent=self)
            return
        try:
            wanted = {k:str(int(v)) for k,v in wanted.items()}
        except ValueError:
            messagebox.showerror('Invalid plan','Shared settings must be integers.',parent=self)
            return
        for name,value in wanted.items():
            key = by_name[name]
            row = list(self.settings.item(key,'values'))
            row[3]=value
            self.settings.item(key,values=row)
        self.staged_node = selected[0]
        self.tabs.select(self.settings_tab)
        self.app.phase_var.set('Network settings staged — review and save to this radio')
        return True

    def upload_group(self):
        if self.stage_plan():
            self.save()

    def save_plan(self):
        path = filedialog.asksaveasfilename(parent=self,initialfile='rfd-network.json',defaultextension='.json')
        if path:
            try:
                data = validate_plan(self.session_data())
                Path(path).write_text(json.dumps(data,indent=2))
                self.flush_session()
            except (ValueError,KeyError,TypeError,OSError) as exc:
                messagebox.showerror('Cannot save plan',str(exc),parent=self)

    def load_plan(self):
        path = filedialog.askopenfilename(parent=self,filetypes=[('Network plan','*.json')])
        if not path:
            return
        try:
            data=validate_plan(json.loads(Path(path).read_text()))
            self.restore_plan(data)
            self.flush_session()
        except (ValueError,KeyError,TypeError,OSError) as exc:
            messagebox.showerror('Cannot open plan',str(exc),parent=self)
