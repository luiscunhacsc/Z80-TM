"""Strict-TM Spectrum 48K board and read-only display observation."""
from collections import deque
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

from .compiled_machine import CompiledMachine, write_table
from .layout import POS, RAM_START, initial_tape, memory_position, snapshot
from .machine import Machine, TransitionBudget, MissingRule

ROM_PATH = Path(__file__).with_name('48.rom')
KEY_ROWS = (
    ('CAPS', 'Z', 'X', 'C', 'V'), ('A', 'S', 'D', 'F', 'G'),
    ('Q', 'W', 'E', 'R', 'T'), ('1', '2', '3', '4', '5'),
    ('0', '9', '8', '7', '6'), ('P', 'O', 'I', 'U', 'Y'),
    ('ENTER', 'L', 'K', 'J', 'H'), ('SPACE', 'SYMBOL', 'M', 'N', 'B'),
)
KEYS = {key: (row, bit) for row, keys in enumerate(KEY_ROWS) for bit, key in enumerate(keys)}


def key_rows(chord):
    rows = [31] * 8
    for key in chord:
        row, bit = KEYS[key.upper()]
        rows[row] &= ~(1 << bit)
    return rows


def table_files():
    directory = Path(__file__).parent
    source_names = ('compiler.py', 'layout.py', 'machine.py', 'unprefixed.py', 'prefixes.py', 'ed.py',
                    'interrupts.py', 'spectrum_rules.py', 'compiled_machine.py')
    fingerprint = hashlib.sha256(b''.join((directory / name).read_bytes() for name in source_names)).hexdigest()
    cache = directory.parent / '.tools' / 'spectrum'
    cache.mkdir(parents=True, exist_ok=True)
    binary = cache / (fingerprint + '.bin')
    metadata = binary.with_suffix('.json')
    if not binary.exists() or not metadata.exists():
        from .spectrum_rules import build_spectrum
        program, pulse_request = build_spectrum()
        write_table(program.table, binary)
        data = {name: getattr(program, name) for name in ('entry', 'boundary', 'unsupported', 'halted', 'io_error',
                                                         'interrupt_boundary', 'interrupt_error')}
        data.update(pulse_request=pulse_request, fingerprint=fingerprint, states=len(program.table.rows),
                    symbols=len(program.table.alphabet))
        metadata.write_text(json.dumps(data), encoding='utf-8')
    return binary, SimpleNamespace(**json.loads(metadata.read_text(encoding='utf-8')))


def initial_spectrum_tape(rom, registers=None, ram=None):
    if len(rom) != 16384:
        raise ValueError('A 48K Spectrum requires a 16,384-byte ROM')
    tape = initial_tape(rom, registers=registers, ram=ram)
    tape[POS['IRQ']] = 1
    tape[POS['VECTOR']] = 255
    for i in range(8):
        tape[POS[f'KEY{i}']] = tape[POS[f'KEY_IN{i}']] = 31
    return tape


class Spectrum:
    def __init__(self, rom_path=ROM_PATH, backend='compiled', registers=None, ram=None):
        self.rom = Path(rom_path).read_bytes()
        self.rom_hash = hashlib.sha256(self.rom).hexdigest()
        tape = initial_spectrum_tape(self.rom, registers, ram)
        self.backend = backend
        if backend == 'compiled':
            path, self.program = table_files()
            self.machine = CompiledMachine(path, tape, self.program.entry)
        elif backend == 'python':
            from .spectrum_rules import build_spectrum
            program, request = build_spectrum()
            self.program = SimpleNamespace(**{name: getattr(program, name) for name in (
                'entry', 'boundary', 'unsupported', 'halted', 'io_error', 'interrupt_boundary', 'interrupt_error')},
                pulse_request=request)
            self.machine = Machine(program.table, tape, program.entry)
        else:
            raise ValueError('Backend must be compiled or python; both execute literal TM transitions')
        self.key_queue = deque()
        self.pulses = iter(())
        self.tape_name = None
        self.tape_pulses = 0
        self.tape_records = 0
        self.tape_finished = True
        self.closed = False

    def read_cells(self, start, count):
        if self.backend == 'compiled':
            return self.machine.read(start, count)
        return [self.machine.tape.get(i, 0) for i in range(start, start + count)]

    def controls(self):
        return dict(enumerate(self.read_cells(0, RAM_START)))

    @property
    def registers(self):
        return snapshot(self.controls())

    def memory(self, start=0, count=65536, coherent=False):
        """Observe RAM; coherent snapshots wait out temporary TM write markers."""
        if not 0 <= start <= 65535 or not 0 <= count <= 65536 - start:
            raise ValueError('Invalid RAM inspection range')
        if not count:
            return b''
        first, last = memory_position(start), memory_position(start + count - 1)
        cells = self.read_cells(first, last - first + 1)
        values = [cells[memory_position(address) - first] for address in range(start, start + count)]
        if coherent and any(value > 255 for value in values):
            return None
        return bytes(value & 255 for value in values)

    def _input(self, values):
        allowed = {'KEY_REQUEST', 'TAPE_CONTROL', 'TAPE_STOP', 'PULSE_LEVEL', 'PULSE_END',
                   *(f'KEY_IN{i}' for i in range(8)), *(f'PULSE_IN{i}' for i in range(4))}
        if not values.keys() <= allowed:
            raise ValueError('Host may only supply external input mailbox cells')
        cells = {POS[key]: value for key, value in values.items()}
        if self.backend == 'compiled':
            self.machine.supply_input(cells)
        else:
            self.machine.tape.update(cells)

    def queue_key(self, *chord):
        self.key_queue.append(key_rows(chord))

    def mount(self, path):
        from .tape import TapeImage
        path = Path(path).resolve()
        image = TapeImage.load(path)
        self.pulses = iter(image.pulses())
        self.tape_name, self.tape_pulses = str(path), 0
        self.tape_records = 0
        self.tape_finished = False
        self._input({'TAPE_CONTROL': 2, 'TAPE_STOP': 0})

    def play(self):
        if self.tape_name is None or self.tape_finished:
            raise ValueError('Mount or rewind a tape first')
        rewind = self.controls()[POS['TAPE_CONTROL']] >= 2
        self._input({'TAPE_CONTROL': 3 if rewind else 1, 'TAPE_STOP': 0})

    def pause_tape(self):
        self._input({'TAPE_CONTROL': 2 if self.controls()[POS['TAPE_CONTROL']] >= 2 else 0})

    def _provide_pulse(self):
        controls = self.controls()
        if controls[POS['TAPE_CONTROL']] >= 2:
            # A rewind/mount latches at the next generated board cycle. If
            # the previous cycle still needs input, keep the old EAR level
            # until that latch; do not consume the new medium's first pulse.
            self._input({'PULSE_END': 0, 'PULSE_LEVEL': controls[POS['EAR']],
                         'PULSE_IN0': 7, 'PULSE_IN1': 0, 'PULSE_IN2': 0, 'PULSE_IN3': 0})
            return
        try:
            pulse = next(self.pulses)
            self.tape_records += 1
        except StopIteration:
            pulse = None
            self.tape_finished = True
        if pulse is None:
            self._input({'PULSE_END': 1})
        else:
            duration, level = pulse
            if not 0 < duration < 1 << 32 or level not in (0, 1):
                raise ValueError('Invalid external tape pulse')
            self._input({'PULSE_END': 0, 'PULSE_LEVEL': level,
                         **{f'PULSE_IN{i}': (duration >> (i * 8)) & 255 for i in range(4)}})
            self.tape_pulses += 1

    def run(self, transitions=5_000_000, until_pc=None):
        """Run bounded literal transitions. Input refills consume no guest time."""
        if transitions < 0:
            raise ValueError('Negative transition budget')
        controls = self.controls()
        if self.key_queue and controls[POS['KEY_REQUEST']] == 0:
            rows = self.key_queue.popleft()
            self._input({**{f'KEY_IN{i}': value for i, value in enumerate(rows)}, 'KEY_REQUEST': 1})
        p, machine = self.program, self.machine
        stops = (p.unsupported, p.io_error, p.interrupt_error, p.pulse_request)
        if until_pc is not None:
            if not 0 <= until_pc <= 65535:
                raise ValueError('PC breakpoint must be 16-bit')
            stops += (p.boundary,)
        end = machine.transitions + transitions
        while machine.transitions < end:
            if machine.state == p.pulse_request:
                self._provide_pulse()
            if self.backend == 'compiled':
                stopped = machine.run(end - machine.transitions, stops)
            else:
                try:
                    machine.run_until(frozenset(stops), end - machine.transitions)
                    stopped = True
                except TransitionBudget:
                    stopped = False
            if stopped and machine.state == p.boundary:
                pc_cells = self.read_cells(POS['PCL'], 3)
                if pc_cells[0] | (pc_cells[2] << 8) == until_pc:
                    return self
            elif stopped and machine.state != p.pulse_request:
                raise MissingRule(f'Spectrum stopped in error state {machine.state}')
        return self

    def close(self):
        if not self.closed and self.backend == 'compiled':
            self.machine.close()
        self.closed = True

    def save_checkpoint(self, path):
        """Save the exact tape/head/state, including a half-finished instruction."""
        _, program = table_files()
        tape = self.machine.export_tape() if self.backend == 'compiled' else self.machine.tape
        data = dict(format='z80tm-spectrum-1', table=program.fingerprint, rom=self.rom_hash,
                    state=self.machine.state, head=self.machine.head, transitions=self.machine.transitions,
                    head_moves=self.machine.head_moves, tape=[(k, v) for k, v in tape.items() if v],
                    keys=list(self.key_queue), tape_path=self.tape_name, tape_records=self.tape_records,
                    tape_pulses=self.tape_pulses, tape_finished=self.tape_finished)
        if self.tape_name:
            data['tape_hash'] = hashlib.sha256(Path(self.tape_name).read_bytes()).hexdigest()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        temporary.replace(path)

    def load_checkpoint(self, path):
        from .tape import TapeImage
        path = Path(path)
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Checkpoint is too large')
        data = json.loads(path.read_text(encoding='utf-8'))
        _, program = table_files()
        if data.get('format') != 'z80tm-spectrum-1' or data.get('table') != program.fingerprint or data.get('rom') != self.rom_hash:
            raise ValueError('Checkpoint does not match this ROM and generated transition table')
        if not 0 <= data['state'] < program.states or not 0 <= data['head'] < 2_000_000:
            raise ValueError('Invalid checkpoint state/head')
        tape = dict(data['tape'])
        if any(type(k) is not int or type(v) is not int or not 0 <= k < 2_000_000 or not 0 <= v < program.symbols
               for k, v in tape.items()):
            raise ValueError('Invalid checkpoint tape symbol or position')
        if any(type(v) is not int or not 0 <= v < 1 << 63 for v in (data['transitions'], data['head_moves'])):
            raise ValueError('Invalid checkpoint counters')
        keys = data['keys']
        if any(len(row) != 8 or any(type(v) is not int or not 0 <= v < 32 for v in row) for row in keys):
            raise ValueError('Invalid checkpoint keyboard queue')
        if any(type(data[name]) is not int or not 0 <= data[name] <= 100_000_000
               for name in ('tape_records', 'tape_pulses')):
            raise ValueError('Invalid checkpoint cassette position')
        pulses = iter(())
        if data['tape_path']:
            tape_path = Path(data['tape_path'])
            if hashlib.sha256(tape_path.read_bytes()).hexdigest() != data['tape_hash']:
                raise ValueError('The checkpoint tape image has changed')
            pulses = iter(TapeImage.load(tape_path).pulses())
            try:
                for _ in range(data['tape_records']):
                    next(pulses)
            except StopIteration as exc:
                raise ValueError('Checkpoint cassette position exceeds the tape image') from exc
        if self.backend == 'compiled':
            self.machine.restore(tape, data['state'], data['head'], data['transitions'], data['head_moves'])
        else:
            self.machine.tape = tape
            self.machine.state, self.machine.head = data['state'], data['head']
            self.machine.transitions, self.machine.head_moves = data['transitions'], data['head_moves']
        self.key_queue = deque(keys)
        self.pulses, self.tape_name = pulses, data['tape_path']
        self.tape_records, self.tape_pulses, self.tape_finished = data['tape_records'], data['tape_pulses'], data['tape_finished']

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def screen_rgb(memory, flash=False):
    """Render a 6912-byte screen snapshot; never modifies emulated state."""
    if len(memory) != 6912:
        raise ValueError('Screen snapshot must contain 6912 bytes')
    palette = [bytes((level if color & 2 else 0, level if color & 4 else 0, level if color & 1 else 0))
               for level in (205, 255) for color in range(8)]
    output = bytearray()
    for y in range(192):
        row = ((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2)
        attr = 6144 + (y // 8) * 32
        for x in range(32):
            value, attribute = memory[row + x], memory[attr + x]
            bright = (attribute >> 3) & 8
            ink, paper = (attribute & 7) | bright, ((attribute >> 3) & 7) | bright
            if flash and attribute & 128:
                ink, paper = paper, ink
            for bit in range(7, -1, -1):
                output.extend(palette[ink if value & (1 << bit) else paper])
    return bytes(output)


def screen_text(rom, memory):
    """Read-only ROM-font recognition for diagnostics and boot verification."""
    glyphs = {rom[0x3C00 + char * 8:0x3C08 + char * 8]: chr(char) for char in range(32, 128)}
    lines = []
    for row in range(24):
        line = ''
        for col in range(32):
            glyph = bytes(memory[(((y & 0xC0) << 5) | ((y & 7) << 8) | ((y & 0x38) << 2)) + col]
                          for y in range(row * 8, row * 8 + 8))
            line += glyphs.get(glyph, glyphs.get(bytes(v ^ 255 for v in glyph), '?'))
        lines.append(line.rstrip())
    return '\n'.join(lines)


def save_screen(path, memory, flash=False):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    rgb = screen_rgb(memory, flash)
    pixels = b''.join(b'\0' + rgb[y * 768:(y + 1) * 768] for y in range(192))
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 256, 192, 8, 2, 0, 0, 0)) +
                           chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))
