"""Translate host text to physical Spectrum key chords, never to RAM bytes."""
import re

PRIMARY = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', (
    'NEW', 'BORDER', 'CONTINUE', 'DIM', 'REM', 'FOR', 'GO TO', 'GO SUB', 'INPUT',
    'LOAD', 'LIST', 'LET', 'PAUSE', 'NEXT', 'POKE', 'PRINT', 'PLOT', 'RUN', 'SAVE',
    'RANDOMIZE', 'IF', 'CLS', 'DRAW', 'CLEAR', 'RETURN', 'COPY')))
SYMBOL = dict(zip('1234567890', '!@#$%&\'()_'))
SYMBOL.update(dict(zip('QWERTYUIOPASDFGHJKLZXCVBNM', (
    '<=', '<>', '>=', '<', '>', 'AND', 'OR', 'AT', ';', '"', 'STOP', 'NOT',
    'STEP', 'TO', 'THEN', '^', '-', '+', '=', ':', '£', '?', '/', '*', ',', '.'))))
EXTENDED = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', (
    'READ', 'BIN', 'LPRINT', 'DATA', 'TAN', 'SGN', 'ABS', 'SQR', 'CODE', 'VAL',
    'LEN', 'USR', 'PI', 'INKEY$', 'PEEK', 'TAB', 'SIN', 'INT', 'RESTORE', 'RND',
    'CHR$', 'LLIST', 'COS', 'EXP', 'STR$', 'LN')))
EXTENDED_SYMBOL = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', (
    '~', 'FLASH', 'INK', '\\', 'ATN', '{', '}', 'BEEP', 'IN', 'VAL$', 'SCREEN$',
    'ATTR', 'BRIGHT', 'INVERSE', 'OVER', '©', 'ASN', 'VERIFY', '|', 'MERGE', ']',
    'PAPER', 'ACS', 'CIRCLE', '[', 'CLOSE #')))


def character_keys(char):
    if char == ' ':
        return [('SPACE',)]
    if char == '\n':
        return [('ENTER',)]
    if 'a' <= char.lower() <= 'z':
        return [('CAPS', char)] if char.isupper() else [(char.upper(),)]
    if char.isascii() and char.isdigit():
        return [(char,)]
    for key, value in SYMBOL.items():
        if value == char:
            return [('SYMBOL', key)]
    for key, value in EXTENDED_SYMBOL.items():
        if value == char:
            return [('CAPS', 'SYMBOL'), ('SYMBOL', key)]
    raise ValueError(f'No Spectrum keyboard mapping for {char!r}')


def basic_keys(source):
    """Queue ordinary BASIC source using the ROM editor's K/L/E modes.

    Requires an empty K-mode edit line. The ROM still scans every key, tokenizes
    statements, checks syntax and stores the program. Unsupported spellings
    fail before any input is queued; this is not a BASIC interpreter.
    """
    mappings = {value: [(key,)] for key, value in PRIMARY.items()}
    mappings.update({value: [('SYMBOL', key)] for key, value in SYMBOL.items() if len(value) > 1})
    mappings.update({value: [('CAPS', 'SYMBOL'), (key,)] for key, value in EXTENDED.items()})
    mappings.update({value: [('CAPS', 'SYMBOL'), ('SYMBOL', key)]
                     for key, value in EXTENDED_SYMBOL.items() if len(value) > 1})
    spellings = {name: name for name in mappings}
    spellings.update({'GOTO': 'GO TO', 'GOSUB': 'GO SUB'})
    pattern = re.compile('|'.join(re.escape(x) for x in sorted(spellings, key=len, reverse=True)), re.I)
    chords = []
    for line in source.replace('\r\n', '\n').replace('\r', '\n').splitlines():
        if not line.strip():
            continue
        line = line.lstrip()
        quoted, remark, command, i = False, False, True, 0
        number = re.match(r'\d+', line)
        if number:
            chords.extend((char,) for char in number[0])
            i = number.end()
        while i < len(line):
            char = line[i]
            if not quoted and not remark:
                match = pattern.match(line, i)
                if match and (command or i == 0 or not line[i - 1].isalnum()) and (
                        match.end() == len(line) or not line[match.end()].isalnum()):
                    name = spellings[match[0].upper()]
                    if name in PRIMARY.values() and not command:
                        raise ValueError(f'{name} needs a statement position (after a line number, : or THEN)')
                    chords.extend(mappings[name])
                    command, remark = name == 'THEN', name == 'REM'
                    i = match.end()
                    continue
                if command and char.isalpha():
                    raise ValueError(f'Unsupported command near {line[i:]!r}; use the Spectrum keyboard for this statement')
            if char == '"' and not remark:
                quoted = not quoted
            if not quoted and not remark and char == ':':
                command = True
            # Outside strings, variable letters do not need CAPS SHIFT.
            chords.extend(character_keys(char if quoted or remark else char.lower()))
            i += 1
        chords.append(('ENTER',))
    return chords
