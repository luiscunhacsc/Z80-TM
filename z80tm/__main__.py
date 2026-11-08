import argparse
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from .compiler import build_cpu
from .harness import CPU, DEMO, UnsupportedOpcode, CPUHalted, PortScriptError
from .machine import TransitionBudget, MissingRule


def number(value):
    return int(value[1:], 16) if value.startswith("$") else int(value, 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Strict single-tape Z80 Turing machine (Python 3.11+)")
    parser.add_argument("command", choices=("demo", "run", "coverage"))
    parser.add_argument("binary", nargs="?", type=Path)
    parser.add_argument("--load", type=number, default=0, help="Raw binary load address (e.g. 0x20ff)")
    parser.add_argument("--pc", type=number, help="Initial PC; defaults to load address")
    parser.add_argument("--register", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--budget", type=int, default=20_000_000, help="Maximum TM transitions")
    parser.add_argument("--instructions", type=int, help="Stop after this many retired instructions")
    parser.add_argument("--trace", action="store_true", help="Print instruction-boundary state")
    parser.add_argument("--cycles", action="store_true", help="Include machine-cycle events")
    parser.add_argument("--json", action="store_true", help="JSON report (trace is separate JSON lines)")
    parser.add_argument("--peek", action="append", default=[], type=number, help="Inspect a RAM address")
    parser.add_argument("--ports", type=Path, help='JSON port script: [["r" or "w", address, data], ...]')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or later is required")
    try:
        registers = {}
        for item in args.register:
            key, value = item.split("=", 1)
            registers[key.upper()] = number(value)
        if args.command == "run" and args.binary is None:
            parser.error("run requires a raw binary file")
        if args.command != "run" and args.binary is not None:
            parser.error("a binary file is only valid with run")
        start = perf_counter()
        program = build_cpu()
        build_seconds = perf_counter() - start
        if args.command == "coverage":
            for opcode, name in sorted(program.coverage.items()):
                print(f"{opcode:02X}  {name}")
            print(f"{len(program.coverage)} unprefixed opcodes; other families unsupported")
            return 0
        binary = DEMO if args.command == "demo" else args.binary.read_bytes()
        ports = json.loads(args.ports.read_text(encoding="utf-8")) if args.ports else ()
        cpu = CPU(binary, args.load, args.pc, registers, program=program, ports=ports)

        def show(instruction):
            if args.json:
                print(json.dumps(asdict(instruction)))
            else:
                r = instruction.registers
                print(f"{instruction.address:04X}: {instruction.opcode:02X}  "
                      f"A={r['A']:02X} F={r['F']:02X} BC={r['BC']:04X} HL={r['HL']:04X} "
                      f"PC={r['PC']:04X}  T={instruction.tstates}")
                for cycle in instruction.cycles:
                    addr = "----" if cycle.address is None else f"{cycle.address:04X}"
                    print(f"  {cycle.start:6} {cycle.kind:8} {addr} +{cycle.tstates}T")

        exit_code = 0
        try:
            cpu.run(args.budget, args.instructions, show if args.trace or args.cycles else None, args.cycles)
            reason = "halt" if cpu.stopped_on_halt else "instruction limit"
        except (UnsupportedOpcode, TransitionBudget, MissingRule, CPUHalted, PortScriptError) as exc:
            reason = str(exc)
            exit_code = 2 if isinstance(exc, UnsupportedOpcode) else 3 if isinstance(exc, TransitionBudget) else 4
        addresses = args.peek or ([0x4000 + 256 * i for i in range(8)] if args.command == "demo" else [])
        report = {
            "stop": reason, "registers": cpu.registers,
            "memory": {f"{a & 65535:04X}": cpu.read_memory(a) for a in addresses},
            "guest_instructions": cpu.instructions, "guest_tstates": cpu.tstates,
            "port_events": [asdict(event) for event in cpu.port_events],
            "tm_transitions": cpu.machine.transitions, "head_moves": cpu.machine.head_moves,
            "host_execution_seconds": cpu.elapsed, "table_build_seconds": build_seconds,
            "table_states": len(program.table.rows), "alphabet_symbols": len(program.table.alphabet),
            "runtime": platform.python_implementation() + " " + platform.python_version(),
            "platform": platform.platform(), "processor": platform.processor(),
            "detailed_tracing": args.trace or args.cycles,
        }
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Stop: {reason}")
            r = cpu.registers
            print(f"A={r['A']:02X} F={r['F']:02X} B={r['B']:02X} HL={r['HL']:04X} PC={r['PC']:04X}")
            if addresses:
                print("RAM: " + " ".join(f"{a & 65535:04X}={cpu.read_memory(a):02X}" for a in addresses))
            if args.command == "demo":
                for a in addresses:
                    print("".join("#" if cpu.read_memory(a) & (128 >> bit) else "." for bit in range(8)))
            print(f"{cpu.instructions} guest instructions; {cpu.tstates} base T-states")
            print(f"{cpu.machine.transitions:,} TM transitions; {cpu.machine.head_moves:,} head moves")
            print(f"Host execution: {cpu.elapsed:.6f}s; table build: {build_seconds:.6f}s")
            print(f"{report['runtime']}; {report['platform']}; {report['processor']}")
        return exit_code
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

