"""Slow, reproducible real-ROM acceptance checks using only literal TM steps.

Run from the repository root with `python -m tools.verify_spectrum boot`, then
`... basic`, `... tap` and `... tzx`. Checkpoints are exact tapes, never snapshots
constructed from expected CPU/BASIC state. No opcode or ROM-loader shortcuts.
"""
import argparse
import functools
import hashlib
import json
from pathlib import Path
import struct
from time import perf_counter

from z80tm.layout import POS
from z80tm.spectrum import Spectrum, save_screen, screen_text

CACHE = Path('.tools/spectrum')
REPORT = Path('verification')
BOOT = CACHE / 'boot.json'


def fixture(kind):
    # Tokenized on-disk BASIC fixture: 10 PRINT 2. The ROM must load and run it.
    body = b'\xf52\x0e\0\0\x02\0\0\x0d'
    program = b'\0\x0a' + struct.pack('<H', len(body)) + body
    header = b'\0TM-BASIC  ' + struct.pack('<HHH', len(program), 10, len(program))
    blocks = []
    for flag, data in ((0, header), (255, program)):
        block = bytes([flag]) + data
        blocks.append(block + bytes([functools.reduce(int.__xor__, block)]))
    if kind == 'tap':
        data = b''.join(struct.pack('<H', len(block)) + block for block in blocks)
    else:
        data = b'ZXTape!\x1a\x01\x14' + b''.join(b'\x10' + struct.pack('<HH', 1000, len(block)) + block for block in blocks)
    path = CACHE / ('basic.' + kind)
    path.write_bytes(data)
    return path, program


def advance(board, pc, label, budget=160_000_000_000):
    started, initial = perf_counter(), board.machine.transitions
    next_report, next_save = initial, initial + 5_000_000_000
    while board.machine.transitions - initial < budget:
        board.run(100_000_000, until_pc=pc)
        if board.machine.transitions >= next_report:
            c, r = board.controls(), board.registers
            print(f'{label}: PC={r["PC"]:04X} guest={r["CLOCK"] / 3500000:.3f}s '
                  f'TM={board.machine.transitions:,} keys={len(board.key_queue) + c[POS["KEY_REQUEST"]]} '
                  f'pulses={board.tape_pulses} wall={perf_counter() - started:.1f}s', flush=True)
            next_report = board.machine.transitions + 2_000_000_000
        if board.machine.state == board.program.boundary and board.registers['PC'] == pc:
            return
        if board.machine.transitions >= next_save:
            board.save_checkpoint(CACHE / (label + '-progress.json'))
            next_save = board.machine.transitions + 5_000_000_000
    raise RuntimeError(f'{label}: verification transition budget exhausted')


def record(board, label, started):
    screen = board.memory(0x4000, 6912)
    text = screen_text(board.rom, screen)
    save_screen(REPORT / (label + '.png'), screen)
    board.save_checkpoint(CACHE / (label + '.json'))
    report = dict(passed=False, rom_sha256=board.rom_hash, registers=board.registers, transitions=board.machine.transitions,
                  head_moves=board.machine.head_moves, wall_seconds=perf_counter() - started,
                  screen_text=text, table_states=board.program.states, table_fingerprint=board.program.fingerprint,
                  tape_pulses=board.tape_pulses)
    if board.tape_name:
        report['media_sha256'] = hashlib.sha256(Path(board.tape_name).read_bytes()).hexdigest()
    (REPORT / (label + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(text, flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('boot', 'basic', 'tap', 'tzx'))
    parser.add_argument('--resume', type=Path, help='Resume an interrupted acceptance run')
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(exist_ok=True)
    started = perf_counter()
    label = 'spectrum-' + args.stage
    with Spectrum() as board:
        if args.resume:
            board.load_checkpoint(args.resume)
        elif args.stage != 'boot':
            board.load_checkpoint(BOOT)
        if args.stage == 'boot':
            advance(board, 0x12AC, 'boot')
            board.save_checkpoint(BOOT)
            report = record(board, 'spectrum-boot', started)
            assert '1982' in report['screen_text']
            assert board.memory(0, 16384) == board.rom
        elif args.stage == 'basic':
            if not args.resume:
                for chord in (('1',), ('0',), ('P',), ('2',), ('ENTER',)):
                    board.queue_key(*chord)
            if not args.resume or 'basic-enter' in args.resume.name:
                if not args.resume or args.resume.name != 'basic-entered.json':
                    advance(board, 0x12AC, 'basic-enter')
                board.save_checkpoint(CACHE / 'basic-entered.json')
                for chord in (('R',), ('ENTER',)):
                    board.queue_key(*chord)
            advance(board, 0x12AC, 'basic-run')
            report = record(board, 'spectrum-basic', started)
            assert any(line.strip() == '2' for line in report['screen_text'].splitlines()), report['screen_text']
        else:
            path, program = fixture(args.stage)
            if not args.resume:
                board.mount(path)
                for chord in (('J',), ('SYMBOL', 'P'), ('SYMBOL', 'P'), ('ENTER',)):
                    board.queue_key(*chord)
            if not args.resume or '-command-' in args.resume.name:
                advance(board, 0x0556, args.stage + '-command')
                board.play()
                board.save_checkpoint(CACHE / (args.stage + '-loading.json'))
            advance(board, 0x12AC, args.stage + '-load')
            report = record(board, 'spectrum-' + args.stage, started)
            prog = int.from_bytes(board.memory(0x5C53, 2), 'little')
            assert board.memory(prog, len(program)) == program
            assert any(line.strip() == '2' for line in report['screen_text'].splitlines()), report['screen_text']
        report['passed'] = True
        (REPORT / (label + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(args.stage.upper() + ' PASSED', flush=True)


if __name__ == '__main__':
    main()
