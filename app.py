#!/usr/bin/env python3
"""Native cross-platform Tk GUI. All serial operations run on a dedicated worker."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import traceback

from rfd_native.backend import Cancelled, Firmware, NativeSerial, RadioClient, RadioError, serial_ports, rssi_unavailable_reason
from rfd_native.dashboard import Dashboard
from rfd_native.paths import log_directory


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('RFD Native — Radio Tools')
        icon = Path(__file__).resolve().parent / 'assets' / 'AppIcon.png'
        if icon.exists():
            self.app_icon = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self.app_icon)
        self.geometry('1020x810')
        self.minsize(860, 700)
        self.configure(background='#f4f5f7')
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.events: queue.Queue = queue.Queue()
        self.tasks: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.client: RadioClient | None = None
        self.identity = None
        self.firmware: Firmware | None = None
        self.busy = False
        self.connecting = False
        self.flashing = False
        self.closing = False
        self.monitoring = False
        self.monitor_stop = threading.Event()
        self.last_settings = ''
        self.log_lines: list[str] = []
        logs = log_directory()
        logs.mkdir(parents=True, exist_ok=True)
        self.log_path = logs / (datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value='115200')
        self.phase_var = tk.StringVar(value='Ready to identify your radio')
        self.identity_var = tk.StringVar(value='No radio identified')
        self.mode_var = tk.StringVar(value='Connect to read model, firmware, and mode.')
        self.connection_summary = tk.StringVar(value='Native radio tools · Not connected')
        self.file_var = tk.StringVar(value='Select an RFD900X2 .gbl file')
        self.file_detail = tk.StringVar(value='GBL structure and CRC32 are checked before upload.')
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text = tk.StringVar(value='No upload in progress')
        self._build()
        self.refresh_ports()
        default = Path.home() / 'Downloads' / 'RFDAsync V4.03 rfd900x2.gbl'
        if default.exists():
            self.load_firmware(default)
        threading.Thread(target=self.worker, name='rfd-serial', daemon=True).start()
        self.after(75, self.drain_events)

    def _build(self):
        style = ttk.Style(self)
        style.configure('Title.TLabel', font=('Helvetica Neue', 23, 'bold'))
        style.configure('Subtitle.TLabel', font=('Helvetica Neue', 11))
        style.configure('Section.TLabelframe.Label', font=('Helvetica Neue', 12, 'bold'))
        style.configure('Status.TLabel', font=('Helvetica Neue', 13, 'bold'))
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='RFD Native', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, textvariable=self.connection_summary,
                  style='Subtitle.TLabel').pack(anchor='w', pady=(4, 18))
        self.pages = ttk.Notebook(outer)
        self.pages.pack(fill='both', expand=True)
        firmware_page = ttk.Frame(self.pages)
        self.pages.add(firmware_page, text='Connection & firmware')
        self.dashboard = Dashboard(self.pages, self)
        self.pages.add(self.dashboard, text='Settings, RSSI & networks')
        top = ttk.Frame(firmware_page)
        top.pack(fill='x')
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=2)
        connection = ttk.LabelFrame(top, text='1  Radio connection', padding=14, style='Section.TLabelframe')
        connection.grid(row=0, column=0, sticky='nsew', padx=(0, 14))
        ttk.Label(connection, text='Serial port').grid(row=0, column=0, sticky='w')
        self.port_combo = ttk.Combobox(connection, textvariable=self.port_var, state='readonly', width=29)
        self.port_combo.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(4, 8))
        self.refresh_button = ttk.Button(connection, text='Refresh', command=self.refresh_ports)
        self.refresh_button.grid(row=2, column=0, sticky='w')
        ttk.Label(connection, text='Normal firmware baud').grid(row=3, column=0, columnspan=2, sticky='w', pady=(12, 4))
        self.baud_combo = ttk.Combobox(connection, textvariable=self.baud_var,
                                     values=['57600', '115200', '38400', '19200', '9600', '230400'],
                                     state='readonly', width=12)
        self.baud_combo.grid(row=4, column=0, sticky='w')
        self.connect_button = ttk.Button(connection, text='Connect & read settings', command=self.connect)
        self.connect_button.grid(row=5, column=0, columnspan=2, sticky='ew', pady=(14, 2))
        self.disconnect_button = ttk.Button(connection, text='Disconnect', command=self.connection_action)
        self.disconnect_button.grid(row=6, column=0, columnspan=2, sticky='ew')
        connection.columnconfigure(0, weight=1)
        device = ttk.LabelFrame(top, text='Radio identity', padding=16, style='Section.TLabelframe')
        device.grid(row=0, column=1, sticky='nsew')
        ttk.Label(device, textvariable=self.identity_var, font=('Helvetica Neue', 13, 'bold'),
                  wraplength=530).pack(anchor='w', pady=(2, 8))
        ttk.Label(device, textvariable=self.mode_var, wraplength=530).pack(anchor='w')
        actions = ttk.Frame(device)
        actions.pack(fill='x', pady=(20, 5))
        self.settings_button = ttk.Button(actions, text='Read settings', command=self.read_settings)
        self.settings_button.pack(side='left')
        self.rssi_button = ttk.Button(actions, text='Live RSSI graph', command=self.read_rssi)
        self.rssi_button.pack(side='left', padx=8)
        self.boot_button = ttk.Button(device, text='Start installed firmware', command=self.start_firmware)
        self.boot_button.pack(anchor='w', pady=(6, 0))
        ttk.Label(device, text='Settings reads do not change saved configuration. Disconnect resumes serial data for other tools.',
                  style='Subtitle.TLabel', wraplength=520).pack(anchor='w', pady=(18, 0))
        fw = ttk.LabelFrame(firmware_page, text='2  Firmware image', padding=14, style='Section.TLabelframe')
        fw.pack(fill='x', pady=(16, 0))
        line = ttk.Frame(fw)
        line.pack(fill='x')
        ttk.Label(line, textvariable=self.file_var, wraplength=740).pack(side='left', anchor='w', fill='x', expand=True)
        self.browse_button = ttk.Button(line, text='Choose .gbl…', command=self.choose_firmware)
        self.browse_button.pack(side='right', padx=(12, 0))
        ttk.Label(fw, textvariable=self.file_detail, wraplength=900, style='Subtitle.TLabel').pack(anchor='w', pady=(8, 0))
        run = ttk.LabelFrame(firmware_page, text='3  Upload', padding=14, style='Section.TLabelframe')
        run.pack(fill='x', pady=(16, 0))
        ttk.Label(run, textvariable=self.phase_var, style='Status.TLabel').pack(anchor='w')
        ttk.Progressbar(run, variable=self.progress_var, maximum=100).pack(fill='x', pady=(10, 5))
        row = ttk.Frame(run)
        row.pack(fill='x')
        ttk.Label(row, textvariable=self.progress_text, style='Subtitle.TLabel').pack(side='left')
        self.flash_button = ttk.Button(row, text='Flash selected firmware', command=self.flash)
        self.flash_button.pack(side='right')
        self.cancel_button = ttk.Button(row, text='Cancel operation', command=self.request_cancel)
        self.cancel_button.pack(side='right', padx=8)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill='x', pady=(18, 6))
        ttk.Label(toolbar, text='Activity & serial log', font=('Helvetica Neue', 12, 'bold')).pack(side='left')
        ttk.Button(toolbar, text='Save log…', command=self.save_log).pack(side='right')
        ttk.Button(toolbar, text='Save settings snapshot…', command=self.save_settings).pack(side='right', padx=8)
        ttk.Label(outer, textvariable=self.phase_var, style='Subtitle.TLabel').pack(anchor='w')
        self.log_widget = ScrolledText(outer, height=5, wrap='word', font=('Menlo', 10),
                                       background='#15202c', foreground='#d8e3ee', insertbackground='white',
                                       relief='flat', padx=10, pady=9, state='disabled')
        self.log_widget.pack(fill='x')
        self.log('Session log: ' + str(self.log_path))
        self.update_buttons()

    def log(self, text: str):
        self.events.put(('log', text))

    def append_log(self, text):
        line = datetime.now().strftime('%H:%M:%S') + '  ' + str(text)
        self.log_lines.append(line)
        with self.log_path.open('a', encoding='utf-8') as fp:
            fp.write(line + '\n')
        self.log_widget.configure(state='normal')
        self.log_widget.insert('end', line + '\n')
        if int(self.log_widget.index('end-1c').split('.')[0]) > 3000:
            self.log_widget.delete('1.0', '500.0')
        self.log_widget.see('end')
        self.log_widget.configure(state='disabled')

    def update_buttons(self):
        active = self.client is not None
        self.connect_button.configure(state='normal' if not self.busy and not active else 'disabled')
        self.disconnect_button.configure(text='Cancel connection' if self.connecting else 'Disconnect',
            state='normal' if self.connecting or (not self.busy and active) else 'disabled')
        for button in [self.settings_button, self.rssi_button]:
            button.configure(state='normal' if not self.busy and active else 'disabled')
        self.boot_button.configure(state='normal' if active and not self.busy and self.identity and self.identity.mode=='Bootloader' else 'disabled')
        self.flash_button.configure(state='normal' if not self.busy and active and self.identity and self.firmware else 'disabled')
        self.cancel_button.configure(state='normal' if self.busy else 'disabled')
        for combo in [self.port_combo, self.baud_combo]:
            combo.configure(state='readonly' if not self.busy and not active else 'disabled')
        for button in [self.refresh_button, self.browse_button]:
            button.configure(state='normal' if not self.busy else 'disabled')
        reason = rssi_unavailable_reason(self.client.identity if self.client else None)
        self.dashboard.availability.set(reason or ('RSSI reporting ready' if active else 'Connect a radio to check RSSI availability'))
        self.dashboard.start.configure(state='normal' if active and not self.busy and not reason else 'disabled')
        self.dashboard.stop.configure(state='normal' if self.monitoring else 'disabled')
        self.dashboard.save_button.configure(state='normal' if active and not self.busy else 'disabled')
        self.dashboard.save_button.configure(text='Stop RSSI monitoring to upload' if self.monitoring else 'Upload settings & reboot')
        self.dashboard.upload_group_button.configure(state='normal' if active and not self.busy else 'disabled')
        if not self.dashboard.samples:
            self.dashboard.draw()

    def refresh_ports(self):
        ports = serial_ports()
        self.port_combo['values'] = ports
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0] if ports else '')

    def run_task(self, name, function, done=None, *, flashing=False, connecting=False):
        if self.busy:
            return
        self.cancel.clear()
        self.busy = True
        self.flashing = flashing
        self.connecting = connecting
        self.phase_var.set(name)
        self.update_buttons()
        self.tasks.put((function, done))

    def worker(self):
        while True:
            task = self.tasks.get()
            if task is None:
                return
            function, done = task
            try:
                result = function()
                self.events.put(('done', (done, result)))
            except Cancelled as exc:
                self.events.put(('cancelled', str(exc)))
            except Exception as exc:
                self.events.put(('error', (str(exc), traceback.format_exc())))

    def drain_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.append_log(value)
                elif kind == 'phase':
                    self.phase_var.set(value)
                elif kind == 'rssi':
                    self.dashboard.sample(*value)
                elif kind == 'settings_status':
                    self.dashboard.settings_status(*value)
                elif kind == 'progress':
                    sent, total = value
                    self.progress_var.set(100 * sent / total)
                    self.progress_text.set(f'{sent:,} / {total:,} bytes acknowledged')
                elif kind == 'done':
                    done, result = value
                    if self.connecting and self.cancel.is_set():
                        if self.client:
                            self.client.close()
                            self.client = None
                        self.events.put(('cancelled','Connection cancelled'))
                        continue
                    self.busy = self.flashing = False
                    self.connecting = False
                    self.monitoring = False
                    if done:
                        done(result)
                    self.update_buttons()
                    if self.closing:
                        self.finish_close()
                        return
                elif kind == 'cancelled':
                    was_connecting = self.connecting
                    self.busy = self.flashing = self.connecting = self.monitoring = False
                    message = 'Connection cancelled · Serial port released' if was_connecting else value
                    self.phase_var.set(message)
                    self.append_log(message)
                    self.dashboard.settings_failed(message)
                    if self.client and self.client.identity:
                        self.show_identity(self.client.identity)
                    self.update_buttons()
                    if self.closing:
                        self.finish_close()
                        return
                elif kind == 'error':
                    error, trace = value
                    self.dashboard.settings_failed(error)
                    self.append_log('ERROR: ' + error)
                    self.append_log(trace)
                    self.phase_var.set('Operation stopped — see log')
                    self.busy = self.flashing = False
                    self.connecting = False
                    self.monitoring = False
                    if self.client and self.client.identity:
                        self.show_identity(self.client.identity)
                    self.update_buttons()
                    if self.closing:
                        self.finish_close()
                        return
                    messagebox.showerror('RFD Native', error, parent=self)
        except queue.Empty:
            pass
        self.refresh_connection_summary()
        self.after(75, self.drain_events)

    def refresh_connection_summary(self):
        client = self.client
        if client is None or client.identity is None:
            self.connection_summary.set('Native radio tools · Not connected')
            return
        identity = client.identity
        self.connection_summary.set(f'{identity.version} · Serial {client.port.baud:,} baud · {identity.mode}')

    def show_identity(self, identity):
        self.identity = identity
        self.identity_var.set(identity.version)
        self.mode_var.set(f'{identity.model} · Board {identity.board_id} · {identity.mode} · {identity.baud:,} baud')
        self.baud_var.set(str(identity.baud))
        self.refresh_connection_summary()

    def connect(self):
        path, baud = self.port_var.get(), int(self.baud_var.get())
        if not path:
            messagebox.showinfo('Select a serial port', 'Connect the USB adapter and click Refresh.', parent=self)
            return
        def task():
            port = NativeSerial(path, baud)
            client = RadioClient(port, log=self.log, phase=lambda p: self.events.put(('phase', p)), cancel=self.cancel)
            try:
                identity = client.identify()
                settings = client.settings() if identity.mode == 'AT command' else ''
                client.check_cancel()
            except Exception:
                client.close()
                raise
            self.client = client
            return client.identity or identity, settings
        def done(result):
            identity, settings = result
            self.dashboard.show_settings('')
            self.dashboard.reset_signal()
            self.last_settings = ''
            self.show_identity(identity)
            self.log('Connected: ' + identity.version)
            if settings:
                self.display_settings(settings, reveal=False)
                self.phase_var.set('Connected · Settings loaded')
            else:
                self.phase_var.set('Connected in bootloader mode · Settings unavailable until firmware starts')
        self.run_task('Opening native serial port', task, done, connecting=True)

    def connection_action(self):
        if self.connecting:
            self.request_cancel()
        else:
            self.disconnect()

    def disconnect(self):
        def task():
            if self.client:
                self.client.close()
                self.client = None
        def done(_):
            self.dashboard.show_settings('')
            self.last_settings = ''
            self.identity = None
            self.identity_var.set('Disconnected')
            self.mode_var.set('Serial port released.')
            self.phase_var.set('Ready to identify your radio')
        self.run_task('Disconnecting', task, done)

    def read_settings(self):
        def done(text):
            self.display_settings(text)
            self.phase_var.set('Settings read successfully')
            self.show_identity(self.client.identity)
        self.run_task('Reading settings', lambda: self.client.settings(), done)

    def display_settings(self, text, *, reveal=True):
        self.last_settings = text
        self.log('Settings snapshot:\n' + text)
        self.dashboard.show_settings(text)
        if reveal:
            self.pages.select(self.dashboard)
            self.dashboard.tabs.select(self.dashboard.settings_tab)

    def read_rssi(self):
        if self.busy or not self.client:
            return
        reason = rssi_unavailable_reason(self.client.identity)
        if reason:
            self.pages.select(self.dashboard)
            self.dashboard.tabs.select(self.dashboard.rssi_tab)
            self.phase_var.set('RSSI unavailable with this firmware')
            self.log(reason)
            return
        self.monitor_stop.clear()
        self.monitoring = True
        self.pages.select(self.dashboard)
        self.dashboard.tabs.select(self.dashboard.rssi_tab)
        def done(_):
            self.phase_var.set('RSSI monitoring stopped')
            self.show_identity(self.client.identity)
        self.run_task('Monitoring RSSI', lambda: self.client.monitor_rssi(
            lambda *sample:self.events.put(('rssi',sample)),self.monitor_stop),done)

    def stop_rssi(self):
        self.monitor_stop.set()

    def start_firmware(self):
        self.run_task('Starting installed firmware', lambda: self.client.return_to_firmware(),
                      lambda identity: (self.show_identity(identity), self.phase_var.set('Installed firmware is responding')))

    def choose_firmware(self):
        path = filedialog.askopenfilename(parent=self, title='Choose RFD900X2 firmware',
                                          initialdir=str(Path.home() / 'Downloads'),
                                          filetypes=[('Gecko bootloader firmware', '*.gbl')])
        if path:
            self.load_firmware(path)

    def load_firmware(self, path):
        try:
            self.firmware = Firmware.load(path)
        except (RadioError, OSError) as exc:
            self.firmware = None
            self.file_var.set(str(path))
            self.file_detail.set('Cannot use this image: ' + str(exc))
            self.log('Firmware validation failed: ' + str(exc))
        else:
            fw = self.firmware
            self.file_var.set(fw.path.name)
            self.file_detail.set(f'{len(fw.data):,} bytes · GBL CRC32 valid · Filename target: {fw.target}\n'
                                 f'Firmware family: {fw.family} · SHA-256: {fw.sha256[:20]}…')
            self.log(f'Validated {fw.path}; SHA-256 {fw.sha256}')
        self.update_buttons()

    def flash(self):
        if not self.client or not self.firmware or not self.identity:
            return
        firmware = self.firmware
        # This is the sole GUI entry point that writes firmware.
        detail = (f'Radio: {self.identity.model}\nCurrent: {self.identity.version}\n'
                  f'Image: {firmware.path.name}\nFamily: {firmware.family}\n\n'
                  'This writes firmware to the connected radio. Keep USB and radio power connected.\n'
                  'Continue with this image?')
        if not messagebox.askyesno('Flash selected firmware', detail, parent=self, default='no'):
            return
        self.progress_var.set(0)
        self.progress_text.set('Waiting for bootloader; no blocks sent yet')
        self.dashboard.reset_signal()
        self.dashboard.show_settings('')
        self.last_settings = ''
        def task():
            if self.identity.mode == 'AT command':
                # Capture configuration before any reboot; do not change it.
                text = self.client.settings()
                snapshot = self.log_path.with_suffix('.settings.txt')
                snapshot.write_text(text + '\n', encoding='utf-8')
                self.log('Pre-flash settings saved: ' + str(snapshot))
            identity = self.client.upload(firmware, lambda sent, total: self.events.put(('progress', (sent, total))))
            self.events.put(('phase', 'Firmware verified · Rereading radio settings'))
            try:
                settings = self.client.settings()
            except Exception as exc:
                self.log('Firmware verified, but settings refresh failed: ' + str(exc))
                return identity, '', str(exc)
            return self.client.identity or identity, settings, ''
        def done(result):
            identity, settings, error = result
            self.show_identity(identity)
            self.progress_text.set('Transfer acknowledged; new firmware answered ATI')
            if settings:
                self.display_settings(settings, reveal=False)
                self.phase_var.set('Firmware verified · Radio settings reloaded')
                messagebox.showinfo('Upload verified', identity.version + '\nRadio settings reloaded.', parent=self)
            else:
                self.phase_var.set('Firmware verified · Settings refresh failed')
                messagebox.showwarning('Firmware verified; settings unavailable',
                    identity.version + '\n\nFirmware upload was verified, but settings could not be reread:\n' + error,
                    parent=self)
        self.run_task('Preparing firmware upload', task, done, flashing=True)

    def request_cancel(self):
        if self.monitoring:
            self.stop_rssi()
            return
        self.cancel.set()
        self.phase_var.set('Cancelling connection — releasing serial port' if self.connecting else 'Cancelling — waiting for serial worker')

    def save_log(self):
        path = filedialog.asksaveasfilename(parent=self, initialfile=self.log_path.name, defaultextension='.log')
        if path:
            Path(path).write_text('\n'.join(self.log_lines) + '\n', encoding='utf-8')

    def save_settings(self):
        if not self.last_settings:
            messagebox.showinfo('No snapshot yet', 'Read settings first.', parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, initialfile='RFD900X2-settings.txt', defaultextension='.txt')
        if path:
            Path(path).write_text(self.last_settings + '\n', encoding='utf-8')

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno('Cancel current operation?',
                                      'An operation is still running. Cancel it and close? '
                                      'An interrupted upload can leave the radio in bootloader mode.',
                                      parent=self, default='no'):
                return
            self.closing = True
            if self.monitoring:
                self.stop_rssi()
            else:
                self.cancel.set()
        else:
            self.finish_close()

    def finish_close(self):
        self.dashboard.flush_session()
        if self.client:
            self.client.close()
            self.client = None
        self.tasks.put(None)
        self.destroy()


if __name__ == '__main__':
    App().mainloop()
