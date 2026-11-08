"""Generation-only Spectrum 48K hardware, executed as literal TM rules.

ROM protection, port decoding, keyboard latching, IRQ generation and cassette
pulse timing all execute on the one tape. The host only supplies external
keyboard chords and cassette pulse durations at the declared input mailboxes.
"""
from functools import cache

from .compiler import Compiler
from .machine import Rule

FRAME_TSTATES = 69888


class SpectrumCompiler(Compiler):
    def __init__(self):
        super().__init__()
        self.board_tick = self.b.reserve('Spectrum elapsed T-states')
        self.pulse_tick = self.b.reserve('cassette elapsed T-states')
        self.pulse_request = self.b.reserve('await external cassette pulse')

    @cache
    def memory_call(self, write, next_state):
        access = super().memory_call(write, next_state)
        if write:
            return self.branch('ADDRH', {h: next_state if h < 0x40 else access for h in range(256)})
        return access

    @cache
    def frame_tick(self, duration, next_state):
        # Frame counter always remains below 69888 (01 11 00); a single cycle
        # can cross at most one frame. The low byte retains the overshoot.
        keyboard = self.write('KEY_REQUEST', 0, next_state)
        for row in range(8):
            keyboard = self.copy(f'KEY_IN{row}', f'KEY{row}', keyboard)
        keyboard = self.write('KEY_HOLD', 6, keyboard)
        accept = self.branch('KEY_REQUEST', {0: next_state, 1: keyboard})
        release = next_state
        for row in range(8):
            release = self.write(f'KEY{row}', 31, release)
        hold = self.branch('KEY_HOLD', {i: accept if i == 0 else
                           self.write('KEY_HOLD', i - 1, release if i == 4 else next_state) for i in range(7)})
        wrap = self.transform('ULA_FRAMES', lambda x: (x + 1) & 255, hold)
        wrap = self.write('ULA_FRAME2', 0, self.write('ULA_FRAME1', 0, self.write('IRQ', 1, wrap)))
        high = self.branch('ULA_FRAME1', {x: wrap if x >= 0x11 else next_state for x in range(256)})
        rollover = self.branch('ULA_FRAME2', {0: next_state, 1: high})
        irq = self.branch('ULA_FRAME0', {x: self.write('IRQ', 0, rollover) if x >= 32 else rollover for x in range(256)})
        after = self.branch('IRQ', {0: rollover, 1: irq})
        return self.add_constant(('ULA_FRAME0', 'ULA_FRAME1', 'ULA_FRAME2'), duration, after)

    @cache
    def event(self, kind, duration, next_state):
        if not 1 <= duration <= 7:
            raise ValueError('Unsupported board cycle duration')
        tick = self.write('PULSE_LEFT', duration, self.call(self.board_tick, next_state))
        return super().event(kind, duration, tick)

    def build_pulses(self):
        # A pending request suspends the machine at a public external-input
        # boundary. Host refill never advances time or changes CPU/RAM state.
        resume = self.pulse_tick
        load = self.copy('PULSE_LEVEL', 'EAR', resume)
        for i in range(4):
            load = self.copy(f'PULSE_IN{i}', f'PULSE{i}', load)
        ended = self.write('TAPE_STOP', 1, self.write('TAPE_CONTROL', 0,
                self.write('EAR', 0, self.return_state)))
        self.b.define(self.pulse_request, default=Rule(self.branch('PULSE_END', {0: load, 1: ended}), None, 0))
        again = self.transform('PULSE_LEFT', lambda x: (x - 1) & 255, resume)
        borrow = again
        for i in reversed(range(4)):
            borrow = self.at(f'PULSE{i}', self.b.emit('decrement cassette duration', {
                x: Rule(self.home(borrow if x == 0 else again), (x - 1) & 255, -1) for x in range(256)
            }))
        nonzero = self.pulse_request
        for i in range(4):
            nonzero = self.branch(f'PULSE{i}', {x: borrow if x else nonzero for x in range(256)})
        finished = self.pulse_request
        for i in range(4):
            finished = self.branch(f'PULSE{i}', {x: self.return_state if x else finished for x in range(256)})
        more = self.branch('PULSE_LEFT', {x: nonzero if x else finished for x in range(8)})
        self.b.define(self.pulse_tick, default=Rule(more, None, 0))
        resets = {}
        for control, target in ((0, self.return_state), (1, self.pulse_tick)):
            reset = self.write('TAPE_CONTROL', control, self.write('EAR', 0, target))
            for i in range(4):
                reset = self.write(f'PULSE{i}', 0, reset)
            resets[control + 2] = reset
        pulse = self.branch('TAPE_CONTROL', {0: self.return_state, 1: self.pulse_tick, **resets})
        self.b.define(self.board_tick, default=Rule(self.branch('PULSE_LEFT', {
            duration: self.frame_tick(duration, pulse) for duration in range(1, 8)
        }), None, 0))

    def build_ports(self):
        self.build_pulses()
        done = self.copy_pair('ADDRL', 'ADDRH', 'PORTL', 'PORTH', self.copy('DATA', 'PORTDATA',
               self.copy('MODE', 'PORTMODE', self.write('IO_EVENT', 1, self.return_state))))
        keyboard = done
        for row in range(8):
            selected = self.branch(f'KEY{row}', {mask: self.transform('DATA', lambda v, mask=mask: v & (0xE0 | mask), keyboard)
                                               for mask in range(32)})
            keyboard = self.branch('ADDRH', {h: keyboard if h & (1 << row) else selected for h in range(256)})
        keyboard = self.branch('EAR', {level: self.write('DATA', 0xBF | (level << 6), keyboard) for level in (0, 1)})
        read = self.branch('ADDRL', {low: self.write('DATA', 255, done) if low & 1 else keyboard for low in range(256)})
        write = self.branch('ADDRL', {low: done if low & 1 else self.copy('DATA', 'ULA_OUT', done) for low in range(256)})
        self.b.define(self.port, default=Rule(self.branch('MODE', {0: read, 1: write}), None, 0))


@cache
def build_spectrum():
    compiler = SpectrumCompiler()
    program = compiler.compile()
    return program, compiler.pulse_request
