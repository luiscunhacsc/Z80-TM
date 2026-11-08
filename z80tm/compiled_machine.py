"""Optional compiled implementation of the literal, CPU-agnostic TM loop.

This changes the host language, never the transition sequence. The Python
Machine remains the reference implementation and a portable fallback.
"""
from array import array
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from .machine import MissingRule, TransitionBudget

PACK = struct.Struct('<i')


def runner_executable():
    source = Path(__file__).with_name('tm_runner.cs')
    cache = source.parent.parent / '.tools' / 'tm-runner'
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    executable = cache / f'tm-{digest}.exe'
    if executable.exists():
        return [str(executable)] if os.name == 'nt' else ['mono', str(executable)]
    compiler = (Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET' /
                'Framework64' / 'v4.0.30319' / 'csc.exe')
    if os.name == 'nt' and compiler.exists():
        command = [str(compiler), '/nologo', '/optimize+', '/platform:x64', f'/out:{executable}', str(source)]
    elif shutil.which('mcs') and shutil.which('mono'):
        command = ['mcs', '-optimize+', f'-out:{executable}', str(source)]
    else:
        raise RuntimeError('Compiled TM runner needs the Windows .NET Framework compiler or Mono (mcs + mono)')
    subprocess.run(command, check=True, capture_output=True, text=True,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return [str(executable)] if os.name == 'nt' else ['mono', str(executable)]


def write_table(table, path):
    """Serialize fixed rows, retaining defaults and exact exception semantics."""
    with Path(path).open('wb') as stream:
        stream.write(struct.pack('<iii', 0x314D545A, len(table.rows), sum(len(r.exceptions) for r in table.rows)))
        for row in table.rows:
            rule = row.default
            values = [rule.state, -1 if rule.write is None else rule.write, rule.move] if rule else [-1, -1, 0]
            values.append(len(row.exceptions))
            for symbol, rule in sorted(row.exceptions.items()):
                values.extend((symbol, rule.state, -1 if rule.write is None else rule.write, rule.move))
            data = array('i', values)
            if sys.byteorder != 'little':
                data.byteswap()
            stream.write(data.tobytes())


class CompiledMachine:
    def __init__(self, table_path, tape, state, head=0):
        self.process = subprocess.Popen([*runner_executable(), str(table_path)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.state, self.head, self.transitions, self.head_moves = state, head, 0, 0
        self.restore(tape, state, head, 0, 0)

    def restore(self, tape, state, head, transitions, head_moves):
        self._send(b'\x01' + struct.pack('<iiqqi', state, head, transitions, head_moves, len(tape)) + self._pairs(tape))
        self._read(1)
        self.state, self.head, self.transitions, self.head_moves = state, head, transitions, head_moves

    @staticmethod
    def _pairs(values):
        data = array('i', (item for pair in values.items() for item in pair))
        if sys.byteorder != 'little':
            data.byteswap()
        return data.tobytes()

    def _send(self, data):
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def _read(self, size):
        data = bytearray()
        while len(data) < size:
            part = self.process.stdout.read(size - len(data))
            if not part:
                raise RuntimeError('TM runner exited: ' + self.process.stderr.read().decode(errors='replace'))
            data.extend(part)
        return bytes(data)

    def run(self, budget, stops=()):
        if budget < 0:
            raise ValueError('Negative transition budget')
        self._send(b'\x02' + struct.pack('<qi', budget, len(stops)) + struct.pack('<' + 'i' * len(stops), *stops))
        reason, self.state, self.head, self.transitions, self.head_moves = struct.unpack('<iiiqq', self._read(28))
        if reason == 2:
            raise MissingRule(f'No transition: state={self.state}, head={self.head}')
        return reason == 1

    def run_until(self, stops, budget):
        if not self.run(budget, stops):
            raise TransitionBudget(f'Transition budget exhausted at {self.transitions:,} transitions')

    def read(self, start, count):
        self._send(b'\x03' + struct.pack('<ii', start, count))
        values = array('i')
        values.frombytes(self._read(4 * count))
        if sys.byteorder != 'little':
            values.byteswap()
        return values

    def supply_input(self, values):
        self._send(b'\x04' + struct.pack('<i', len(values)) + self._pairs(values))
        self._read(1)

    def export_tape(self):
        self._send(b'\x05')
        count, = struct.unpack('<i', self._read(4))
        return dict(struct.iter_unpack('<ii', self._read(8 * count)))

    def close(self):
        if self.process.poll() is None:
            self._send(b'\x00')
            self.process.wait(timeout=10)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
