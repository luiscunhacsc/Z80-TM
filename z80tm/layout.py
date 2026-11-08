"""Tape construction/inspection only; never called by the transition step."""
START, RAM, PAGE, SELECTED, TARGET, END, PORT_RECORD, PORT_ACTIVE, PORT_END, PORT_ACTIVE_END = range(256, 266)
IRQ_RECORD, IRQ_ACTIVE, IRQ_END, IRQ_ACTIVE_END, RESPONSE_RECORD, RESPONSE_ACTIVE, RESPONSE_END, RESPONSE_ACTIVE_END = range(266, 274)
SLOTS = (
    "A", "F", "B", "C", "D", "E", "H", "L",
    "A_", "F_", "B_", "C_", "D_", "E_", "H_", "L_",
    "PCL", "PCH", "SPL", "SPH", "IXL", "IXH", "IYL", "IYH",
    "I", "R", "IFF1", "IFF2", "IM", "EI_DELAY", "HALT",
    "OP", "INSTL", "INSTH", "TSTATES", "ADDRL", "ADDRH",
    "DATA", "MODE", "CONT", "TMP", "TMP2", "RESULT", "FLAGS", "IO_EVENT",
    "COPYSRC", "COPYDST", "COPYVAL", "COPYCONT",
    "WZL", "WZH", "Q", "OLDQ", "MODF", "P", "INDEX", "FAMILY", "FIRSTOP",
    "EA_L", "EA_H", "DISP", "SAVE", "SAVE2", "SAVEF", "RETI", "ACTION",
    "IRQ", "NMI", "VECTOR", "IM0", "NMI_DELAY",
    "PORTL", "PORTH", "PORTDATA", "PORTMODE",
    "BUSL", "BUSH",
    "HAS_SCRIPT", "NEXT_KIND", "NEXT_VECTOR", *(f"DUE{i}" for i in range(8)),
    *(f"CLOCK{i}" for i in range(8)), *(f"TIME{i}" for i in range(8)),
    # Spectrum peripherals are tape state too. Host input is confined to the
    # KEY_IN*/KEY_REQUEST and cassette input/control mailboxes.
    "ULA_FRAME0", "ULA_FRAME1", "ULA_FRAME2", "ULA_FRAMES", "ULA_OUT", "EAR",
    "KEY_HOLD", "KEY_REQUEST", *(f"KEY{i}" for i in range(8)), *(f"KEY_IN{i}" for i in range(8)),
    "TAPE_CONTROL", "TAPE_STOP", "PULSE_LEFT", *(f"PULSE{i}" for i in range(4)),
    *(f"PULSE_IN{i}" for i in range(4)), "PULSE_LEVEL", "PULSE_END",
)
MARK = {name: 274 + n for n, name in enumerate(SLOTS)}
POS = {name: 2 + 2 * n for n, name in enumerate(SLOTS)}
RAM_START = 1 + 2 * len(SLOTS)
TOKEN_START = 274 + len(SLOTS)
PAIRS = {
    "AF": ("F", "A"), "BC": ("C", "B"), "DE": ("E", "D"),
    "HL": ("L", "H"), "PC": ("PCL", "PCH"), "SP": ("SPL", "SPH"),
    "IX": ("IXL", "IXH"), "IY": ("IYL", "IYH"),
    "AF_": ("F_", "A_"), "BC_": ("C_", "B_"),
    "DE_": ("E_", "D_"), "HL_": ("L_", "H_"),
    "WZ": ("WZL", "WZH"),
}
ARCH_BYTES = SLOTS[:31] + ("WZL", "WZH", "Q", "P", "IRQ", "NMI", "VECTOR", "NMI_DELAY")


def memory_position(address: int) -> int:
    address &= 0xFFFF
    return RAM_START + 2 + (address >> 8) * 257 + (address & 255)


def initial_tape(program: bytes = b"", load: int = 0, registers=None, ram=None, ports=(), interrupts=(), interrupt_data=b"", clock=0) -> dict[int, int]:
    if not 0 <= load <= 65535 or len(program) > 65536:
        raise ValueError("Load address must be 16-bit; binary must fit in 64 KiB")
    tape = {0: START, RAM_START: RAM}
    for name in SLOTS:
        tape[POS[name] - 1] = MARK[name]
        tape[POS[name]] = 0
    for page in range(256):
        tape[RAM_START + 1 + page * 257] = PAGE
    tape[RAM_START + 1 + 256 * 257] = END
    port_start = RAM_START + 2 + 256 * 257
    for index, (direction, address, value) in enumerate(ports):
        if direction not in ("r", "w") or not 0 <= address <= 65535 or not 0 <= value <= 255:
            raise ValueError("Port script records must be [r/w, 16-bit address, byte]")
        for offset, symbol in enumerate((PORT_RECORD if index else PORT_ACTIVE, int(direction == "w"),
                                         address & 255, address >> 8, value)):
            tape[port_start + 5 * index + offset] = symbol
    tape[port_start + 5 * len(ports)] = PORT_END if ports else PORT_ACTIVE_END
    if not 0 <= clock < 1 << 64:
        raise ValueError("Clock must be an unsigned 64-bit T-state count")
    for i in range(8):
        tape[POS[f"CLOCK{i}"]] = (clock >> (8 * i)) & 255
    irq_start = port_start + 5 * len(ports) + 1
    last = -1
    records = []
    for time, kind, vector in interrupts:
        if not last <= time < 1 << 64 or time < 0 or kind not in ("clear", "int", "nmi") or not 0 <= vector <= 255:
            raise ValueError("Interrupt records must be ordered [64-bit time, clear/int/nmi, vector byte]")
        last = time
        records.append([*(time >> (i * 8) & 255 for i in range(8)), ("clear", "int", "nmi").index(kind), vector])
    for index, values in enumerate(records):
        for offset, symbol in enumerate([IRQ_RECORD if index else IRQ_ACTIVE, *values]):
            tape[irq_start + 11 * index + offset] = symbol
    tape[irq_start + 11 * len(records)] = IRQ_END if records else IRQ_ACTIVE_END
    if records:
        tape[POS["HAS_SCRIPT"]] = 1
        for slot, value in zip((*[f"DUE{i}" for i in range(8)], "NEXT_KIND", "NEXT_VECTOR"), records[0]):
            tape[POS[slot]] = value
    response_start = irq_start + 11 * len(records) + 1
    for index, value in enumerate(interrupt_data):
        if not 0 <= value <= 255:
            raise ValueError("IM0 response bytes must be 8-bit")
        tape[response_start + 2 * index] = RESPONSE_RECORD if index else RESPONSE_ACTIVE
        tape[response_start + 2 * index + 1] = value
    tape[response_start + 2 * len(interrupt_data)] = RESPONSE_END if interrupt_data else RESPONSE_ACTIVE_END
    for name, value in (registers or {}).items():
        name = name.upper()
        if name in PAIRS:
            if not 0 <= value <= 65535:
                raise ValueError(f"{name} must be 16-bit")
            low, high = PAIRS[name]
            tape[POS[low]], tape[POS[high]] = value & 255, value >> 8
        elif name in ARCH_BYTES:
            maximum = 2 if name == "IM" else 1 if name in ("IFF1", "IFF2", "EI_DELAY", "HALT", "IRQ", "NMI", "NMI_DELAY", "P") else 255
            if not 0 <= value <= maximum:
                raise ValueError(f"Invalid initial {name}: {value}")
            tape[POS[name]] = value
        else:
            raise ValueError(f"Unknown architectural register: {name}")
    for address, value in (ram or {}).items():
        if not 0 <= address <= 65535 or not 0 <= value <= 255:
            raise ValueError("Invalid initial RAM byte")
        tape[memory_position(address)] = value
    for offset, value in enumerate(program):
        tape[memory_position(load + offset)] = value
    return tape


def byte(tape, slot: str) -> int:
    return tape.get(POS[slot], 0)


def word(tape, low: str, high: str) -> int:
    return byte(tape, low) | byte(tape, high) << 8


def snapshot(tape) -> dict[str, int]:
    result = {name: byte(tape, name) for name in ARCH_BYTES}
    result.update({name: word(tape, *pair) for name, pair in PAIRS.items()})
    result["CLOCK"] = wide(tape, "CLOCK")
    return result


def wide(tape, prefix):
    return sum(byte(tape, f"{prefix}{i}") << (8 * i) for i in range(8))

