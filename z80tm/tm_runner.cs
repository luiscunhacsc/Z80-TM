// Generic literal single-tape TM runner. No CPU, memory-map, port or opcode logic.
// The sole execution operation reads one cell, chooses one fixed rule, writes
// that cell, moves by -1/0/+1 and changes state. No scan or instruction batching.
using System;
using System.IO;

public static class TMRunner {
    struct Rule { public int Next, Write, Move; }
    struct Row { public Rule Default; public int Start, Count; public bool Dense; }
    static Row[] rows;
    static int[] keys;
    static Rule[] rules;
    static bool[] stops;
    static int[] tape = new int[131072];
    static int origin = 1024, head, state;
    static long transitions, moves;
    static Rule ReadRule(BinaryReader r) {
        return new Rule { Next = r.ReadInt32(), Write = r.ReadInt32(), Move = r.ReadInt32() };
    }
    static int Cell(int position) {
        while (position + origin < 0 || position + origin >= tape.Length) {
            int[] bigger = new int[checked(tape.Length * 2)];
            int shift = tape.Length / 2;
            Array.Copy(tape, 0, bigger, shift, tape.Length);
            origin += shift;
            tape = bigger;
        }
        return position + origin;
    }
    static int Run(long budget) {
        if (budget < 0) throw new ArgumentException("Negative transition budget");
        long end = checked(transitions + budget);
        // Locals avoid static-field traffic in the host loop. They still
        // perform and count every transition, including every blank scan.
        int h = head, s = state, o = origin, reason = 0;
        long t = transitions, m = moves;
        int[] cells = tape, symbols = keys;
        Row[] table = rows;
        Rule[] entries = rules;
        bool[] stop = stops;
        while (t < end) {
            int at = h + o;
            if ((uint)at >= (uint)cells.Length) {
                at = Cell(h); cells = tape; o = origin;
            }
            int symbol = cells[at];
            Row row = table[s];
            Rule rule = row.Default;
            if (row.Count == 1) {
                if (symbols[row.Start] == symbol) rule = entries[row.Start];
            } else if (row.Dense && (uint)symbol < (uint)row.Count) {
                rule = entries[row.Start + symbol];
            } else if (row.Count != 0 && !row.Dense) {
                int lo = row.Start, hi = lo + row.Count - 1;
                while (lo <= hi) {
                    int mid = lo + (hi - lo) / 2, key = symbols[mid];
                    if (key == symbol) { rule = entries[mid]; break; }
                    if (key < symbol) lo = mid + 1; else hi = mid - 1;
                }
            }
            if (rule.Next < 0) { reason = 2; break; }
            cells[at] = rule.Write < 0 ? symbol : rule.Write;
            h += rule.Move;
            s = rule.Next;
            t++;
            if (rule.Move != 0) m++;
            if (stop[s]) { reason = 1; break; }
        }
        head = h; state = s; transitions = t; moves = m;
        return reason;
    }
    public static void Main(string[] args) {
        try {
            using (var f = new BinaryReader(File.OpenRead(args[0]))) {
                if (f.ReadInt32() != 0x314D545A) throw new Exception("Invalid table format");
                int count = f.ReadInt32(), entries = f.ReadInt32();
                rows = new Row[count]; stops = new bool[count];
                keys = new int[entries]; rules = new Rule[entries];
                int offset = 0;
                for (int i = 0; i < count; ++i) {
                    Row row = new Row { Default = ReadRule(f), Start = offset, Count = f.ReadInt32(), Dense = true };
                    for (int j = 0; j < row.Count; ++j) {
                        keys[offset] = f.ReadInt32(); rules[offset] = ReadRule(f);
                        if (keys[offset] != j) row.Dense = false;
                        offset++;
                    }
                    rows[i] = row;
                }
                if (offset != entries) throw new Exception("Invalid rule count");
            }
            using (var input = new BinaryReader(Console.OpenStandardInput()))
            using (var output = new BinaryWriter(Console.OpenStandardOutput())) {
                while (true) {
                    int command = input.ReadByte();
                    if (command == 0) break;
                    if (command == 1) { // Initialize machine / restore an exact TM checkpoint.
                        state = input.ReadInt32(); head = input.ReadInt32();
                        transitions = input.ReadInt64(); moves = input.ReadInt64();
                        Array.Clear(tape, 0, tape.Length);
                        int count = input.ReadInt32();
                        for (int i = 0; i < count; ++i) {
                            int pos = input.ReadInt32(), value = input.ReadInt32();
                            int at = Cell(pos); tape[at] = value;
                        }
                        output.Write((byte)0);
                    } else if (command == 2) { // Run a literal transition budget.
                        long budget = input.ReadInt64();
                        Array.Clear(stops, 0, stops.Length);
                        int count = input.ReadInt32();
                        for (int i = 0; i < count; ++i) stops[input.ReadInt32()] = true;
                        output.Write(Run(budget));
                        output.Write(state); output.Write(head);
                        output.Write(transitions); output.Write(moves);
                    } else if (command == 3) { // Read-only tape inspection.
                        int start = input.ReadInt32(), count = input.ReadInt32();
                        for (int i = 0; i < count; ++i) {
                            int at = Cell(start + i); output.Write(tape[at]);
                        }
                    } else if (command == 4) { // External input / checkpoint initialization only.
                        int count = input.ReadInt32();
                        for (int i = 0; i < count; ++i) {
                            int pos = input.ReadInt32(), value = input.ReadInt32();
                            int at = Cell(pos); tape[at] = value;
                        }
                        output.Write((byte)0);
                    } else if (command == 5) { // Export the entire abstract tape.
                        int count = 0;
                        for (int i = 0; i < tape.Length; ++i) if (tape[i] != 0) count++;
                        output.Write(count);
                        for (int i = 0; i < tape.Length; ++i) if (tape[i] != 0) {
                            output.Write(i - origin); output.Write(tape[i]);
                        }
                    } else throw new Exception("Unknown runner command");
                    output.Flush();
                }
            }
        } catch (EndOfStreamException) {
        } catch (Exception e) {
            Console.Error.WriteLine(e.Message);
            Environment.ExitCode = 1;
        }
    }
}
