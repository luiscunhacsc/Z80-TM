"""Offline fixture audit with a reusable fixed rule table."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from z80tm.compiler import build_cpu
from z80tm.harness import CPU

ARCH = ("a", "f", "b", "c", "d", "e", "h", "l", "pc", "sp", "ix", "iy",
        "af_", "bc_", "de_", "hl_", "i", "r", "iff1", "iff2", "im", "wz", "q", "p")


def check_vector(program, family, vector, check_wz=True):
    initial, final = vector["initial"], vector["final"]
    registers = {key.upper(): initial[key] for key in ARCH}
    ports = [(direction, address, value) for address, value, direction in vector.get("ports", [])]
    cpu = CPU(registers=registers, ram=dict(initial["ram"]), program=program, ports=ports)
    ins = cpu.step_instruction(budget=30_000_000, trace_cycles=True)
    errors = {}
    actual = cpu.registers
    for key in ARCH:
        if key == "wz" and not check_wz:
            continue
        expected = final[key]
        if actual[key.upper()] != expected:
            errors[key] = [actual[key.upper()], expected]
    for address, value in final["ram"]:
        if cpu.read_memory(address) != value:
            errors[f"ram:{address:04x}"] = [cpu.read_memory(address), value]
    if ins.tstates != len(vector["cycles"]):
        errors["timing"] = [ins.tstates, len(vector["cycles"])]
    cycle_time = sum(event.tstates for event in ins.cycles)
    if cycle_time != ins.tstates:
        errors["cycle_timing"] = [cycle_time, ins.tstates]
    if actual["CLOCK"] != ins.tstates:
        errors["clock"] = [actual["CLOCK"], ins.tstates]
    expected_bus = []
    for tick, (address, value, pins) in enumerate(vector["cycles"]):
        if pins not in ("r-m-", "-wm-", "r--i", "-w-i"):
            continue
        kind = {"r-m-": "read", "-wm-": "write", "r--i": "io_read", "-w-i": "io_write"}[pins]
        if pins.startswith("r"):
            value = vector["cycles"][tick + 1][1]
        expected_bus.append((kind, address, value, tick - (2 if kind.startswith("io_") else 1)))
    bus = [("read" if e.kind == "m1" else e.kind, e.address, e.data, e.start) for e in ins.cycles if e.kind != "internal"]
    if bus != expected_bus:
        errors["bus"] = [bus, expected_bus]
    actual_ports = [(e.address, e.data, e.direction) for e in cpu.port_events]
    expected_ports = [tuple(p) for p in vector.get("ports", [])]
    if actual_ports != expected_ports:
        errors["ports"] = [actual_ports, expected_ports]
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="tests/fixtures/prefixes.json")
    parser.add_argument("--family", default="")
    parser.add_argument("--no-wz", action="store_true")
    args = parser.parse_args()
    started = perf_counter()
    program = build_cpu()
    print(f"Built {len(program.table.rows)} states in {perf_counter()-started:.3f}s", flush=True)
    families = json.loads(Path(args.file).read_text(encoding="utf-8"))["fixtures"]
    count = failed = 0
    for family in families:
        if not family["opcode"].startswith(args.family):
            continue
        for vector in family["tests"]:
            count += 1
            try:
                errors = check_vector(program, family, vector, not args.no_wz)
            except Exception as exc:
                errors = {"exception": str(exc)}
            if errors:
                failed += 1
                print(json.dumps({"name": vector["name"], "errors": errors}), flush=True)
        if count % 100 == 0:
            print(f"Checked {count}; failed {failed}", flush=True)
    print(f"RESULT {count} vectors; {failed} failures; {perf_counter()-started:.3f}s", flush=True)
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
