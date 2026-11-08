# Spectrum 48K with strict Turing-machine execution

`python -m z80tm spectrum` opens the supplied 16 KiB `z80tm/48.rom` at reset.
The guest CPU, ROM protection, frame interrupt, keyboard matrix, ULA port
decoding and tape pulse countdown execute in generated rules on the single
tape. The host renders the screen and supplies external input mailboxes.

## Starting and resuming

On this Windows workspace, `Start-Spectrum.cmd` uses the installed Python with
Tkinter. With no arguments, the launcher resumes the last saved session, or the
verified cold-boot checkpoint if available. `python -m z80tm spectrum` always
starts a new cold boot unless `--resume` is supplied. The Spectrum front end
also works with that Python 3.10 installation;
the existing CPU CLI and verification suite retain their Python 3.11+ requirement.
The portable `.tools/python312` interpreter lacks Tkinter, but can run headless
verification. No Python packages need installing.

The default compiled runner uses the Windows .NET Framework C# compiler already
present on this machine. On other platforms it requires Mono (`mcs` and `mono`).
`--backend python` selects the much slower reference TM loop. Both execute
individual transitions, with identical cell writes, head movements and counts.
The C# program has no Z80 decoder or Spectrum hardware implementation. It only
looks up a finite rule and applies it to one cell. It never skips tape scans.

Generated tables and the compiled executable are cached under `.tools/`.
Cold boot includes the ROM's entire RAM test and takes substantial wall time.
After boot, save a state to avoid repeating it. Closing the window autosaves:

```console
python -m z80tm spectrum --resume .tools/spectrum/session.json
```

Checkpoints include the whole abstract tape, head, control state, exact counters,
keyboard queue and cassette position. They can resume inside an instruction.
The ROM and generated-table fingerprints must match; a mounted cassette must
still exist with the same contents. These are TM checkpoints, not Z80 snapshots
created by bypassing ROM execution. `--rom PATH`, `--tape PATH` and `--session PATH`
override the default files.

## Writing BASIC

Wait for the ROM editor, then click the screen to use the physical keyboard.
The original token-entry behavior applies: **P** enters `PRINT` in K mode,
**R** enters `RUN`, **J** enters `LOAD`. Host Shift means CAPS SHIFT, and Ctrl
means SYMBOL SHIFT. Arrow keys, Backspace and Escape map to the corresponding
Spectrum cursor, delete and BREAK chords. On-screen keys show the K-mode words.
The CAPS/SYMBOL checkboxes apply to the next clicked key; Extended mode presses
both modifiers together. Uppercase letters in strings require CAPS SHIFT.

For example, press `1`, `0`, `P`, `2`, Enter, then `R`, Enter to enter and run
`10 PRINT 2`. The source box can instead queue ordinary text:

```basic
10 PRINT "HELLO"
20 GO TO 10
```

Queue source with an empty K-mode edit line. Each chord is held for three
emulated frames and released before the next one. The queue preserves short
host key presses while the TM is slow. Queueing a large program takes time;
the status bar reports pending keys. Clear queued keys removes pending chords,
and BREAK queues CAPS SHIFT + SPACE.

The text helper supports the primary K-mode statements and the letter-key
extended/symbol tokens, including expressions such as `SIN PI`, `AT`, `THEN`,
`AND` and `OR`. It preserves quoted strings and REM text. Commands requiring
other extended combinations, such as `DEF FN`, must be entered using the
Spectrum keyboard. The helper only emits physical chords. The ROM does all
tokenization, syntax checking, program storage and execution.

## Loading cassettes

1. Mount a `.tap` or `.tzx` file.
2. With an empty edit line, press the `LOAD ""` button (or J, SYMBOL+P twice,
   Enter). Wait for the ROM loader to start.
3. Press Play. Stop tape pauses playback; Rewind returns to the beginning and
   stops. A TZX stop block requires another press of Play.

There is no loader trap: TAP/TZX blocks produce timed EAR levels. Generated
rules advance those durations, and the ROM samples port FE and writes RAM.
Checksums and LOAD/VERIFY behavior therefore belong to the guest loader.

Supported TZX blocks: standard/turbo data (10/11), pure tone (12), pulse sequence
(13), pure data (14), direct recording (15), CSW RLE/zlib (18), generalized data
(19), pause/stop (20), jump (23), loop (24/25), call/return (26/27), 48K stop
(2A), and signal level (2B). Group/text/archive/hardware/custom/glue metadata
(21/22/30/31/32/33/35/5A) is skipped while preserving block indices. Interactive
selection (28), deprecated C64 blocks and unknown blocks fail explicitly.
Tape images are limited to 64 MiB; malformed/truncated records fail clearly.

## Hardware scope and verification

This is a 48K board with 16 KiB ROM, 48 KiB RAM, 256×192 bitmap/attributes,
BRIGHT/FLASH and border color, the eight keyboard rows, 69,888-T-state frames
and a 32-T-state INT pulse. Odd ports read FF. Even ports decode the ULA.
The model uses base CPU cycles: it does not implement contention, the floating
bus, precise pin phases, 128K paging, joystick hardware, audio output or cassette
recording. Timing-sensitive commercial/custom loaders may require more hardware
detail even when their tape encoding is supported.

Focused tests cover ROM protection, row selection, frame IRQ/latching, exact
cassette edges, stop/rewind, display addressing, malformed media, checkpoint
resumption, and Python/C# equality of the full tape and counters after partial
execution. Run:

```console
python -m unittest tests.test_tape tests.test_spectrum tests.test_compiled_machine tests.test_spectrum_equivalence -v
python -m tools.verify_spectrum boot
python -m tools.verify_spectrum basic
python -m tools.verify_spectrum tap
python -m tools.verify_spectrum tzx
```

The acceptance script uses the actual supplied ROM: cold boot, keyboard entry
of `10 PRINT 2` and `RUN`, and loading/autorunning that program through standard
TAP and TZX pulses. Each stage writes JSON measurements and a PNG in
`verification/`, plus exact checkpoints under `.tools/spectrum/`. Long stages
also save progress checkpoints for `--resume`. A stage is verified only when
its assertions complete and it prints `PASSED`.

Hardware references: [48K technical reference](https://worldofspectrum.org/faq/reference/48kreference.htm),
[TZX 1.20 specification](https://worldofspectrum.net/TZXformat.html), and
[ROM loader disassembly](https://skoolkid.github.io/rom/asm/0556.html).
