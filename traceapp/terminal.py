from __future__ import annotations

import re


def clean_terminal_output(value: str) -> str:
    """Simulate a terminal screen so only the final visible state is kept.

    Properly handles cursor-movement and line-erase CSI sequences, which
    readline and zsh emit on every keystroke redraw. Without this, tools that
    replay keystrokes one-at-a-time (e.g. RustDesk remote desktop) produce
    a log full of partial commands and garbled prompt redraws.
    """
    if not value:
        return ""

    # Multi-row character buffer. Each entry is a list of chars for that row.
    buf: list[list[str]] = [[]]
    row = 0
    col = 0

    def _grow(r: int) -> None:
        while len(buf) <= r:
            buf.append([])

    i = 0
    n = len(value)

    while i < n:
        ch = value[i]

        # ── escape sequences ──────────────────────────────────────────────
        if ch == "\x1b" and i + 1 < n:
            nxt = value[i + 1]

            if nxt == "[":
                # CSI: ESC [ <params> <final>
                j = i + 2
                while j < n and (value[j].isdigit() or value[j] == ";"):
                    j += 1
                if j >= n:
                    i = j
                    continue
                final = value[j]
                params_str = value[i + 2 : j]
                raw = [int(p) if p else 0 for p in params_str.split(";")] if params_str else []
                p1 = raw[0] if raw else 0

                if final == "K":        # Erase in line
                    _grow(row)
                    if p1 == 0:         # cursor → end of line
                        buf[row] = buf[row][:col]
                    elif p1 == 1:       # start of line → cursor
                        buf[row] = [" "] * min(col, len(buf[row])) + buf[row][col:]
                    elif p1 == 2:       # whole line
                        buf[row] = []

                elif final == "J":      # Erase in display
                    if p1 == 2:         # clear screen
                        buf = [[]]
                        row = 0
                        col = 0
                    elif p1 == 0:       # cursor → end of display
                        _grow(row)
                        buf[row] = buf[row][:col]
                        del buf[row + 1 :]

                elif final == "G":      # Cursor to column (1-based)
                    col = max(0, (p1 or 1) - 1)

                elif final in ("H", "f"):  # Cursor position row;col (1-based)
                    p2 = raw[1] if len(raw) > 1 else 0
                    row = max(0, (p1 or 1) - 1)
                    col = max(0, (p2 or 1) - 1)
                    _grow(row)

                elif final == "A":      # Cursor up
                    row = max(0, row - (p1 or 1))

                elif final == "B":      # Cursor down
                    row += p1 or 1
                    _grow(row)

                elif final == "C":      # Cursor forward
                    col += p1 or 1

                elif final == "D":      # Cursor backward
                    col = max(0, col - (p1 or 1))

                # All other CSI sequences (colours, modes, …) → ignored.
                i = j + 1
                continue

            elif nxt == "]":
                # OSC: read until BEL or ST (ESC \)
                j = i + 2
                while j < n:
                    if value[j] == "\x07":
                        j += 1
                        break
                    if value[j] == "\x1b" and j + 1 < n and value[j + 1] == "\\":
                        j += 2
                        break
                    j += 1
                i = j
                continue

            elif nxt in "()":
                # Character set designator: ESC ( x
                i += 3
                continue

            else:
                # Any other two-char escape → skip
                i += 2
                continue

        # ── printable / control characters ────────────────────────────────
        elif ch == "\r":
            col = 0

        elif ch == "\n":
            row += 1
            col = 0
            _grow(row)

        elif ch == "\b":
            col = max(0, col - 1)

        elif ch == "\t":
            _grow(row)
            spaces = 4 - (col % 4)
            for _ in range(spaces):
                if col < len(buf[row]):
                    buf[row][col] = " "
                else:
                    buf[row].append(" ")
                col += 1

        elif ch in ("\x07", "\x00"):
            pass  # BEL, NUL

        elif ord(ch) < 32:
            pass  # remaining control chars

        else:
            _grow(row)
            if col < len(buf[row]):
                buf[row][col] = ch
            else:
                while len(buf[row]) < col:
                    buf[row].append(" ")
                buf[row].append(ch)
            col += 1

        i += 1

    result_lines = ["".join(line).rstrip() for line in buf]
    cleaned = "\n".join(result_lines)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned.strip()
