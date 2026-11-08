"""Tk front end. Only the worker owns the TM; widgets enqueue external input."""
import argparse
from pathlib import Path
import queue
import threading
import time

from .layout import POS
from .spectrum import ROM_PATH, Spectrum, save_screen, screen_rgb
from .spectrum_keyboard import PRIMARY, basic_keys, character_keys


class SpectrumWindow:
    def __init__(self, root, args):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        self.tk, self.ttk, self.dialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.root, self.args = root, args
        self.commands, self.events = queue.Queue(), queue.Queue()
        self.caps, self.symbol = tk.BooleanVar(), tk.BooleanVar()
        self.status = tk.StringVar(value='Building/loading the fixed TM table…')
        self.media = tk.StringVar(value='No cassette mounted')
        self.paused, self.closing, self.last_screen = False, False, None
        self.exit_code = 0
        self.scale = 2 if root.winfo_screenheight() >= 1000 else 1
        root.title('ZX Spectrum 48K — strict single-tape Turing machine')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.canvas = tk.Label(root, bg='white', padx=24, pady=24, takefocus=True)
        self.canvas.pack(fill='x')
        self.canvas.bind('<Button-1>', lambda event: self.canvas.focus_set())
        self.canvas.bind('<KeyPress>', self.key)
        bar = ttk.Frame(root, padding=6)
        bar.pack(fill='x')
        for label, command in (
            ('Mount TAP/TZX', self.mount), ('LOAD ""', lambda: self.source('LOAD ""')),
            ('Play', lambda: self.send('play')), ('Stop tape', lambda: self.send('stop_tape')),
            ('Rewind', lambda: self.send('rewind')), ('Pause / resume', self.pause),
            ('Save state', self.save), ('Open state', self.restore), ('Screenshot', self.screenshot),
        ):
            ttk.Button(bar, text=label, command=command).pack(side='left', padx=2)
        ttk.Label(root, textvariable=self.media).pack(anchor='w', padx=8)
        keyboard = ttk.Frame(root, padding=6)
        keyboard.pack()
        for row, keys in enumerate(('1234567890', 'QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM')):
            for col, key in enumerate(keys):
                label = key + ('\n' + PRIMARY[key] if key in PRIMARY else '')
                ttk.Button(keyboard, text=label, width=10,
                           command=lambda key=key: self.virtual_key(key)).grid(row=row, column=col, padx=1, pady=1)
        controls = ttk.Frame(root)
        controls.pack()
        ttk.Checkbutton(controls, text='CAPS SHIFT', variable=self.caps).pack(side='left')
        ttk.Checkbutton(controls, text='SYMBOL SHIFT', variable=self.symbol).pack(side='left')
        for label, chord in (('Extended mode', ('CAPS', 'SYMBOL')), ('Space', ('SPACE',)),
                             ('Enter', ('ENTER',)), ('Delete', ('CAPS', '0')), ('Edit', ('CAPS', '1')),
                             ('Break', ('CAPS', 'SPACE'))):
            ttk.Button(controls, text=label, command=lambda chord=chord: self.send('keys', [chord])).pack(side='left')
        editor = ttk.LabelFrame(root, text='Queue BASIC source through the keyboard (start with an empty K cursor)', padding=6)
        editor.pack(fill='x', padx=8, pady=6)
        self.source_text = tk.Text(editor, height=4, width=78, undo=True)
        self.source_text.insert('1.0', '10 PRINT "HELLO"\n20 GO TO 10\n')
        self.source_text.pack(side='left', fill='x', expand=True)
        actions = ttk.Frame(editor)
        actions.pack(side='left', padx=6)
        ttk.Button(actions, text='Queue source', command=lambda: self.source(self.source_text.get('1.0', 'end'))).pack(fill='x')
        ttk.Button(actions, text='RUN', command=lambda: self.source('RUN')).pack(fill='x')
        ttk.Button(actions, text='LIST', command=lambda: self.source('LIST')).pack(fill='x')
        ttk.Button(actions, text='Clear queued keys', command=lambda: self.send('clear_keys')).pack(fill='x')
        ttk.Label(root, text='Click the screen to type. Letter keys enter Spectrum keywords in K mode. '
                  'Shift = CAPS; Ctrl = SYMBOL.\nEach queued chord lasts three emulated frames. '
                  'Strict TM execution is much slower than real time; source entry and tape loading take time.').pack(padx=8, anchor='w')
        ttk.Label(root, textvariable=self.status, wraplength=950).pack(fill='x', padx=8, pady=8)
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()
        root.after(100, self.poll)
        self.canvas.focus_set()

    def send(self, name, value=None):
        self.commands.put((name, value))

    def source(self, text):
        try:
            self.send('keys', basic_keys(text))
            self.canvas.focus_set()
        except ValueError as exc:
            self.messagebox.showerror('Keyboard input', str(exc), parent=self.root)

    def virtual_key(self, key):
        chord = tuple(x for x, enabled in (('CAPS', self.caps.get()), ('SYMBOL', self.symbol.get())) if enabled) + (key,)
        self.send('keys', [chord])
        self.caps.set(False)
        self.symbol.set(False)
        self.canvas.focus_set()

    def key(self, event):
        special = {'Return': ('ENTER',), 'BackSpace': ('CAPS', '0'), 'Delete': ('CAPS', '0'),
                   'Left': ('CAPS', '5'), 'Down': ('CAPS', '6'), 'Up': ('CAPS', '7'),
                   'Right': ('CAPS', '8'), 'Escape': ('CAPS', 'SPACE')}
        if event.keysym in special:
            self.send('keys', [special[event.keysym]])
        elif event.keysym.lower() in 'abcdefghijklmnopqrstuvwxyz0123456789' and len(event.keysym) == 1:
            shift = ('CAPS',) if event.state & 1 else ()
            symbol = ('SYMBOL',) if event.state & 4 else ()
            self.send('keys', [shift + symbol + (event.keysym.upper(),)])
        elif event.char and event.char.isprintable():
            try:
                self.send('keys', character_keys(event.char))
            except ValueError:
                pass
        return 'break'

    def mount(self):
        path = self.dialog.askopenfilename(parent=self.root, filetypes=[('Spectrum tapes', '*.tap *.tzx'), ('All files', '*.*')])
        if path:
            self.send('mount', path)

    def save(self):
        path = self.dialog.asksaveasfilename(parent=self.root, defaultextension='.json', filetypes=[('Exact TM checkpoint', '*.json')])
        if path:
            self.send('save', path)

    def restore(self):
        path = self.dialog.askopenfilename(parent=self.root, filetypes=[('Exact TM checkpoint', '*.json')])
        if path:
            self.send('restore', path)

    def screenshot(self):
        if self.last_screen is not None:
            path = self.dialog.asksaveasfilename(parent=self.root, defaultextension='.png', filetypes=[('PNG image', '*.png')])
            if path:
                save_screen(path, self.last_screen)

    def pause(self):
        self.paused = not self.paused
        self.send('pause', self.paused)

    def close(self):
        if not self.closing:
            self.closing = True
            self.status.set('Saving exact TM session state…')
            self.send('close')

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'closed':
                    self.root.destroy()
                    return
                if kind == 'error':
                    self.messagebox.showerror('Spectrum', value, parent=self.root)
                elif kind == 'status':
                    self.status.set(value)
                elif kind == 'paused':
                    self.paused = value
                elif kind == 'frame':
                    pixels, border, flash, status, media = value
                    self.last_screen = pixels
                    ppm = b'P6\n256 192\n255\n' + screen_rgb(pixels, flash)
                    self.photo = self.tk.PhotoImage(data=ppm, format='PPM').zoom(self.scale)
                    color = '#%02x%02x%02x' % (205 if border & 2 else 0, 205 if border & 4 else 0, 205 if border & 1 else 0)
                    self.canvas.configure(image=self.photo, bg=color)
                    self.status.set(status)
                    self.media.set(media)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def work(self):
        paused, last_frame, machine = False, 0, None
        try:
            machine = Spectrum(self.args.rom, self.args.backend)
            if self.args.resume:
                machine.load_checkpoint(self.args.resume)
            if self.args.tape:
                machine.mount(self.args.tape)
            while True:
                try:
                    name, value = self.commands.get(timeout=0.1 if paused else 0)
                except queue.Empty:
                    name, value = None, None
                try:
                    if name == 'close':
                        try:
                            machine.save_checkpoint(self.args.session)
                        except Exception as exc:
                            self.events.put(('error', f'Could not save session: {exc}'))
                        break
                    if name == 'keys':
                        for chord in value:
                            machine.queue_key(*chord)
                    elif name == 'clear_keys':
                        machine.key_queue.clear()
                    elif name == 'mount':
                        machine.mount(value)
                    elif name == 'play':
                        machine.play()
                    elif name == 'stop_tape':
                        machine.pause_tape()
                    elif name == 'rewind':
                        if machine.tape_name:
                            machine.mount(machine.tape_name)
                    elif name == 'pause':
                        paused = value
                    elif name == 'save':
                        machine.save_checkpoint(value)
                    elif name == 'restore':
                        machine.load_checkpoint(value)
                    if not paused:
                        machine.run(5_000_000 if self.args.backend == 'compiled' else 10_000)
                except Exception as exc:
                    paused = True
                    self.events.put(('paused', True))
                    self.events.put(('error', str(exc)))
                if time.monotonic() - last_frame >= 0.25:
                    last_frame = time.monotonic()
                    controls, registers = machine.controls(), machine.registers
                    clock = registers['CLOCK']
                    state = 'Paused' if paused else 'Running'
                    status = (f'{state} · PC {registers["PC"]:04X} · guest {clock / 3_500_000:.3f} s · '
                              f'{machine.machine.transitions:,} literal TM transitions · '
                              f'{len(machine.key_queue) + controls[POS["KEY_REQUEST"]]} queued keys')
                    playing = controls[POS['TAPE_CONTROL']] in (1, 3)
                    media = ('No cassette mounted' if machine.tape_name is None else
                             f'{Path(machine.tape_name).name} · {"finished" if machine.tape_finished else "playing" if playing else "stopped"}'
                             f' · {machine.tape_pulses:,} signal segments')
                    pixels = machine.memory(0x4000, 6912, coherent=True)
                    if pixels is None:
                        self.events.put(('status', status))
                    else:
                        self.events.put(('frame', (pixels, controls[POS['ULA_OUT']] & 7,
                                                   bool(controls[POS['ULA_FRAMES']] & 16), status, media)))
        except Exception as exc:
            self.exit_code = 1
            self.events.put(('error', str(exc)))
        finally:
            if machine is not None:
                machine.close()
            self.events.put(('closed', None))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Spectrum 48K: every guest operation executes as literal TM transitions')
    parser.add_argument('--rom', type=Path, default=ROM_PATH)
    parser.add_argument('--backend', choices=('compiled', 'python'), default='compiled', help='Host language for the same strict TM loop')
    parser.add_argument('--resume', type=Path, help='Resume an exact TM checkpoint')
    parser.add_argument('--tape', type=Path, help='Mount a .tap or .tzx (press LOAD "" then Play)')
    parser.add_argument('--session', type=Path, default=ROM_PATH.parent.parent / '.tools' / 'spectrum' / 'session.json',
                        help='Autosave the exact session here on close')
    args = parser.parse_args(argv)
    try:
        import tkinter as tk
    except ImportError:
        parser.error('The GUI needs Python with Tkinter. On this Windows workspace use Start-Spectrum.cmd.')
    args.session.parent.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    window = SpectrumWindow(root, args)
    root.mainloop()
    return window.exit_code
