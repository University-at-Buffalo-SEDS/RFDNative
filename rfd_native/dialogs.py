"""Small native dialogs with explicit layout and dismissal controls."""
import tkinter as tk
from tkinter import ttk


class DestinationDialog(tk.Toplevel):
    def __init__(self, parent, node):
        root=parent.winfo_toplevel()
        super().__init__(root)
        self.withdraw()
        self.result=None
        self.node_id=int(node['node'])
        self.title('Edit destination — '+node['name'])
        self.transient(root)
        self.resizable(True,True)
        self.protocol('WM_DELETE_WINDOW',self.cancel)
        content=ttk.Frame(self,padding=20)
        content.pack(fill='both',expand=True)
        content.columnconfigure(0,weight=1)
        ttk.Label(content,text=f"{node['name']} · Node {self.node_id}",font=('Helvetica Neue',13,'bold')).grid(row=0,column=0,sticky='w')
        ttk.Label(content,text='Destination ID (1–32768; 32768 broadcasts to all nodes)').grid(row=1,column=0,sticky='w',pady=(12,5))
        self.value=tk.StringVar(value=str(node['dest']))
        self.entry=ttk.Entry(content,textvariable=self.value)
        self.entry.grid(row=2,column=0,sticky='ew')
        self.error=tk.StringVar()
        ttk.Label(content,textvariable=self.error,wraplength=420).grid(row=3,column=0,sticky='w',pady=7)
        ttk.Label(content,text='Save updates the network plan. Upload to the radio separately.',wraplength=420).grid(row=4,column=0,sticky='w')
        content.rowconfigure(5,weight=1)
        buttons=ttk.Frame(content)
        buttons.grid(row=6,column=0,sticky='ew',pady=(18,0))
        ttk.Button(buttons,text='Cancel',command=self.cancel).pack(side='left')
        ttk.Button(buttons,text='Save',command=self.save,default='active').pack(side='right')
        self.bind('<Return>',self.save)
        self.bind('<KP_Enter>',self.save)
        self.bind('<Escape>',self.cancel)
        if self.tk.call('tk', 'windowingsystem') == 'aqua':
            self.bind('<Command-period>',self.cancel)
        self.update_idletasks()
        width=max(480,self.winfo_reqwidth())
        height=max(280,self.winfo_reqheight())
        x=max(0,min(root.winfo_rootx()+(root.winfo_width()-width)//2,self.winfo_screenwidth()-width))
        y=max(30,min(root.winfo_rooty()+(root.winfo_height()-height)//2,self.winfo_screenheight()-height-70))
        self.minsize(width,height)
        self.geometry(f'{width}x{height}+{x}+{y}')
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        self.entry.focus_set()
        self.entry.selection_range(0,'end')

    def save(self,event=None):
        try:
            value=int(self.value.get())
            if not 1<=value<=32768:
                raise ValueError('Destination must be between 1 and 32768.')
            if value==self.node_id:
                raise ValueError('Destination must differ from this radio’s node ID.')
        except ValueError as exc:
            self.error.set(str(exc) if 'Destination' in str(exc) else 'Enter a whole number from 1 to 32768.')
            self.entry.focus_set()
            return 'break'
        self.result=value
        self.grab_release()
        self.destroy()
        return 'break'

    def cancel(self,event=None):
        self.result=None
        self.grab_release()
        self.destroy()
        return 'break'
