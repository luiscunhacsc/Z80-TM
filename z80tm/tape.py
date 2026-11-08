"""TAP/TZX media decoding into external EAR levels and durations.

This module never accesses CPU registers or RAM. The ROM receives the resulting
pulses through generated input/timing rules and performs the actual loading.
"""
from dataclasses import dataclass
from pathlib import Path
import zlib


class TapeError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise TapeError(f'Truncated tape data at byte {self.pos}')
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def number(self, size=1, signed=False):
        return int.from_bytes(self.take(size), 'little', signed=signed)

    def left(self):
        return len(self.data) - self.pos


@dataclass(frozen=True)
class Block:
    kind: str
    args: tuple = ()


@dataclass(frozen=True)
class TapeImage:
    blocks: tuple
    format: str

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise TapeError('Tape image exceeds the 64 MiB limit')
        return cls.parse(path.read_bytes(), path.suffix.lower())

    @classmethod
    def parse(cls, data, suffix='.tap'):
        r, blocks = Reader(data), []
        if data.startswith(b'ZXTape!\x1a'):
            suffix = '.tzx'
        if suffix == '.tap':
            while r.left():
                block = r.take(r.number(2))
                if len(block) < 2:
                    raise TapeError('TAP blocks must include a flag and checksum byte')
                blocks.append(Block('standard', (block, 1000)))
        elif suffix == '.tzx':
            if r.take(8) != b'ZXTape!\x1a' or r.number() != 1:
                raise TapeError('Expected a TZX version 1 file')
            r.number()  # Compatible minor revisions are accepted.
            while r.left():
                offset, kind = r.pos, r.number()
                if kind == 0x10:
                    pause = r.number(2)
                    block = r.take(r.number(2))
                    if not block:
                        raise TapeError('Empty standard-speed TZX block')
                    blocks.append(Block('standard', (block, pause)))
                elif kind == 0x11:
                    pilot, sync1, sync2, zero, one, count = (r.number(2) for _ in range(6))
                    bits, pause = r.number(), r.number(2)
                    blocks.append(Block('data', (r.take(r.number(3)), pause, pilot, count, (sync1, sync2), zero, one, bits)))
                elif kind == 0x12:
                    blocks.append(Block('tone', (r.number(2), r.number(2))))
                elif kind == 0x13:
                    blocks.append(Block('pulses', (tuple(r.number(2) for _ in range(r.number())),)))
                elif kind == 0x14:
                    zero, one, bits, pause = r.number(2), r.number(2), r.number(), r.number(2)
                    blocks.append(Block('data', (r.take(r.number(3)), pause, 0, 0, (), zero, one, bits)))
                elif kind == 0x15:
                    sample, pause, bits = r.number(2), r.number(2), r.number()
                    blocks.append(Block('direct', (r.take(r.number(3)), sample, pause, bits)))
                elif kind in (0x18, 0x19):
                    blocks.append(Block('csw' if kind == 0x18 else 'generalized', (r.take(r.number(4)),)))
                elif kind == 0x20:
                    blocks.append(Block('pause', (r.number(2),)))
                elif kind == 0x23:
                    blocks.append(Block('jump', (r.number(2, True),)))
                elif kind == 0x24:
                    count = r.number(2)
                    if not count:
                        raise TapeError('TZX loop count must be positive')
                    blocks.append(Block('loop', (count,)))
                elif kind == 0x25:
                    blocks.append(Block('endloop'))
                elif kind == 0x26:
                    blocks.append(Block('call', (tuple(r.number(2, True) for _ in range(r.number(2))),)))
                elif kind == 0x27:
                    blocks.append(Block('return'))
                elif kind == 0x28:
                    raise TapeError('Interactive TZX selection blocks (0x28) are not supported; select/export a tape path first')
                elif kind == 0x2A:
                    if r.number(4) != 0:
                        raise TapeError('Invalid 48K tape-stop block')
                    blocks.append(Block('pause', (0,)))
                elif kind == 0x2B:
                    if r.number(4) != 1 or (level := r.number()) not in (0, 1):
                        raise TapeError('Invalid signal-level block')
                    blocks.append(Block('level', (level,)))
                else:
                    if kind in (0x21, 0x30):
                        r.take(r.number())
                    elif kind == 0x22:
                        pass
                    elif kind == 0x31:
                        r.number()
                        r.take(r.number())
                    elif kind == 0x32:
                        r.take(r.number(2))
                    elif kind == 0x33:
                        r.take(3 * r.number())
                    elif kind == 0x35:
                        r.take(16)
                        r.take(r.number(4))
                    elif kind == 0x5A:
                        r.take(9)
                    else:
                        raise TapeError(f'Unsupported TZX block 0x{kind:02X} at byte {offset}')
                    blocks.append(Block('metadata'))
        else:
            raise TapeError('Expected a .tap or .tzx tape image')
        if not blocks:
            raise TapeError('Tape image contains no blocks')
        return cls(tuple(blocks), suffix[1:].upper())

    def pulses(self):
        """Yield (T-states, EAR level); None requests a user tape stop."""
        pc, level, steps, loops, calls = 0, 0, 0, [], []
        while pc < len(self.blocks):
            if pc < 0 or steps >= 1_000_000:
                raise TapeError('Invalid or excessive TZX control flow')
            block = self.blocks[pc]
            steps += 1
            next_pc, pause = pc + 1, 0
            kind, args = block.kind, block.args
            if kind in ('standard', 'data'):
                if kind == 'standard':
                    data, pause = args
                    pilot, count, sync, zero, one, bits = 2168, 8063 if data[0] < 128 else 3223, (667, 735), 855, 1710, 8
                else:
                    data, pause, pilot, count, sync, zero, one, bits = args
                if not 1 <= bits <= 8 or (count and pilot == 0) or zero == 0 or one == 0 or any(x == 0 for x in sync):
                    raise TapeError('Invalid TZX bit count or pulse length')
                for _ in range(count):
                    yield pilot, level
                    level ^= 1
                for duration in sync:
                    yield duration, level
                    level ^= 1
                for index, value in enumerate(data):
                    used = bits if index == len(data) - 1 else 8
                    for bit in range(7, 7 - used, -1):
                        duration = one if value & (1 << bit) else zero
                        yield duration, level
                        level ^= 1
                        yield duration, level
                        level ^= 1
            elif kind in ('tone', 'pulses'):
                durations = (args[0] for _ in range(args[1])) if kind == 'tone' else iter(args[0])
                for duration in durations:
                    if not duration:
                        raise TapeError('Zero-length TZX pulse')
                    yield duration, level
                    level ^= 1
            elif kind == 'direct':
                data, sample, pause, bits = args
                if not sample or not 1 <= bits <= 8:
                    raise TapeError('Invalid direct-recording sample length')
                previous, duration = None, 0
                for index, value in enumerate(data):
                    for bit in range(7, 7 - (bits if index == len(data) - 1 else 8), -1):
                        level = (value >> bit) & 1
                        if previous is not None and level != previous:
                            yield duration, previous
                            duration = 0
                        previous, duration = level, duration + sample
                if duration:
                    yield duration, previous
            elif kind == 'csw':
                r = Reader(args[0])
                pause, rate, compression, count = r.number(2), r.number(3), r.number(), r.number(4)
                if not rate or compression not in (1, 2):
                    raise TapeError('Invalid CSW sampling rate or compression')
                payload = r.take(r.left())
                if compression == 2:
                    try:
                        decoder = zlib.decompressobj()
                        payload = decoder.decompress(payload, 64 * 1024 * 1024)
                        if not decoder.eof or decoder.unused_data:
                            raise TapeError('Invalid or oversized compressed CSW recording')
                    except zlib.error as exc:
                        raise TapeError(f'Invalid compressed CSW: {exc}') from exc
                r, remainder = Reader(payload), 0
                for _ in range(count):
                    samples = r.number() or r.number(4)
                    duration, remainder = divmod(samples * 3_500_000 + remainder, rate)
                    if not duration:
                        raise TapeError('CSW pulse shorter than one Spectrum T-state')
                    yield duration, level
                    level ^= 1
                if r.left():
                    raise TapeError('CSW pulse count does not match its data')
                if count:
                    level ^= 1  # CSW retains its last level, like direct recording.
            elif kind == 'generalized':
                r = Reader(args[0])
                pause = r.number(2)
                pilot_count, pilot_width, pilot_alphabet = r.number(4), r.number(), r.number() or 256
                data_count, data_width, data_alphabet = r.number(4), r.number(), r.number() or 256
                if pilot_count > 1_000_000 or data_count > 16_000_000:
                    raise TapeError('Generalized tape block exceeds the symbol limit')
                def symbols(count, width):
                    result = []
                    for _ in range(count):
                        flags = r.number()
                        if flags > 3:
                            raise TapeError('Invalid generalized-symbol flags')
                        result.append((flags, tuple(r.number(2) for _ in range(width))))
                    return result
                pilot_symbols = symbols(pilot_alphabet, pilot_width) if pilot_count else []
                pilot_stream = [(r.number(), r.number(2)) for _ in range(pilot_count)]
                if sum(count for _, count in pilot_stream) > 16_000_000:
                    raise TapeError('Generalized pilot exceeds the expanded symbol limit')
                data_symbols = symbols(data_alphabet, data_width) if data_count else []
                width = (data_alphabet - 1).bit_length()
                payload = r.take((data_count * width + 7) // 8)
                if r.left():
                    raise TapeError('Unexpected generalized-data bytes')
                def sequence():
                    for symbol, count in pilot_stream:
                        if symbol >= len(pilot_symbols):
                            raise TapeError('Invalid pilot symbol')
                        for _ in range(count):
                            yield pilot_symbols[symbol]
                    position = 0
                    for _ in range(data_count):
                        symbol = 0
                        for _ in range(width):
                            symbol = (symbol << 1) | ((payload[position // 8] >> (7 - position % 8)) & 1)
                            position += 1
                        if symbol >= len(data_symbols):
                            raise TapeError('Invalid data symbol')
                        yield data_symbols[symbol]
                # Symbol flags describe the first edge relative to the level
                # of the preceding pulse, unlike ordinary duration sequences.
                previous = level
                for flags, durations in sequence():
                    level = previous ^ 1 if flags == 0 else previous if flags == 1 else flags - 2
                    for duration in durations:
                        if not duration:
                            break
                        yield duration, level
                        previous = level
                        level ^= 1
            elif kind == 'pause':
                pause, = args
                if pause == 0:
                    yield None
                    level = 0
            elif kind == 'level':
                level, = args
            elif kind == 'jump':
                next_pc = pc + args[0]
            elif kind == 'loop':
                loops.append([pc + 1, args[0]])
            elif kind == 'endloop':
                if not loops:
                    raise TapeError('TZX loop end without a loop start')
                loops[-1][1] -= 1
                if loops[-1][1]:
                    next_pc = loops[-1][0]
                else:
                    loops.pop()
            elif kind == 'call':
                if args[0]:
                    targets = [pc + offset for offset in args[0]]
                    calls.append([pc + 1, targets[1:]])
                    next_pc = targets[0]
            elif kind == 'return':
                if not calls:
                    raise TapeError('TZX return without a call')
                if calls[-1][1]:
                    next_pc = calls[-1][1].pop(0)
                else:
                    next_pc = calls.pop()[0]
            if pause:
                if kind in ('direct', 'csw'):
                    level ^= 1
                yield 3500, level
                level = 0
                if pause > 1:
                    yield (pause - 1) * 3500, 0
            if not 0 <= next_pc <= len(self.blocks):
                raise TapeError('TZX jump target is outside the file')
            pc = next_pc
        if loops or calls:
            raise TapeError('Unterminated TZX loop or call')
