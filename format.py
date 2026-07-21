# ============================================================
#  Cryo — Canonical formatter (cryoc fmt)
#
#  Reindents the code to 4 spaces according to the depth of
#  braces/brackets/parentheses, ignoring strings and comments,
#  and leaving foreign blocks (>Lang( ... )) and block comments
#  verbatim. Removes trailing whitespace, ensures a
#  single blank line between blocks and a final newline.
#
#  Security: the formatter ONLY changes whitespace. After
#  formatting, it checks that the token stream is identical to the
#  original; if not (some unforeseen case), it returns the original
#  text unchanged — never changes the meaning of the program.
# ============================================================
import sys

from lexer import Lexer, LexerError

_INDENT = "    "


def _scan(line, in_comment):
    """Analyzes a line (outside block comment): returns
    (net, starts_closer, still_in_comment, foreign_delta), skipping
    strings and comments. `net` = depth variation; `foreign`
    detects opening of >Lang( that doesn't close on the line."""
    i, n = 0, len(line)
    net = 0
    lstr = line.lstrip()
    starts_closer = lstr[:1] in ('}', ')', ']')
    foreign_open = 0
    while i < n:
        c = line[i]
        if in_comment:
            if c == '*' and i + 1 < n and line[i + 1] == '/':
                in_comment = False
                i += 2
                continue
            i += 1
            continue
        if c == '/' and i + 1 < n and line[i + 1] == '/':
            break                                   # line comment
        if c == '/' and i + 1 < n and line[i + 1] == '*':
            in_comment = True
            i += 2
            continue
        if c in ('"', "'"):
            q = c
            i += 1
            while i < n:
                if line[i] == '\\':
                    i += 2
                    continue
                if line[i] == q:
                    i += 1
                    break
                i += 1
            continue
        # foreign block: >Lang(
        if c == '>' and i + 1 < n and (line[i + 1].isalpha() or line[i + 1] == '_'):
            j = i + 1
            while j < n and (line[j].isalnum() or line[j] == '_'):
                j += 1
            if j < n and line[j] == '(':
                foreign_open += 1
                i = j + 1
                continue
        if c in '{[(':
            net += 1
        elif c in '}])':
            net -= 1
        i += 1
    return net, starts_closer, in_comment, foreign_open


def format_source(text: str) -> str:
    lines = text.replace('\r\n', '\n').split('\n')
    out = []
    depth = 0
    in_comment = False
    in_foreign = 0          # depth of parentheses inside >Lang( ... )
    prev_blank = False

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()

        # inside foreign block: verbatim until parentheses close
        if in_foreign:
            out.append(line)
            for c in line:
                if c == '(':
                    in_foreign += 1
                elif c == ')':
                    in_foreign -= 1
            if in_foreign <= 0:
                in_foreign = 0
            prev_blank = False
            continue

        # inside block comment: verbatim
        if in_comment:
            out.append(line)
            _, _, in_comment, _ = _scan(line, True)
            prev_blank = False
            continue

        if s == '':
            if not prev_blank:
                out.append('')
            prev_blank = True
            continue
        prev_blank = False

        net, starts_closer, next_comment, foreign = _scan(s, False)
        this_depth = depth - 1 if starts_closer else depth
        if this_depth < 0:
            this_depth = 0
        out.append(_INDENT * this_depth + s if s else '')
        depth = max(0, depth + net)
        in_comment = next_comment
        if foreign > 0:
            in_foreign = foreign   # opened a multi-line foreign block

    # collapses trailing blank lines and ensures a newline at the end
    while out and out[-1] == '':
        out.pop()
    return '\n'.join(out) + '\n'


def _tokens(text):
    toks = Lexer(text).tokenize()
    return [(t.type, t.value) for t in toks]


def _safe_format(text: str):
    """Formats; returns (formatted_text, ok). If formatting would change
    the tokens (unforeseen case), returns the original and ok=False."""
    try:
        formatted = format_source(text)
        if _tokens(text) == _tokens(formatted):
            return formatted, True
        return text, False
    except LexerError:
        return text, False


# ── CLI: cryoc fmt <files> [--write] [--check] ───────────
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'fmt':
        argv = argv[1:]
    write = False
    check = False
    files = []
    for a in argv:
        if a in ('-w', '--write'):
            write = True
        elif a == '--check':
            check = True
        elif a in ('-h', '--help'):
            print("usage: cryoc fmt <file.cryo>... [--write] [--check]")
            return 0
        else:
            files.append(a)
    if not files:
        print("cryoc fmt: no file provided", file=sys.stderr)
        return 2

    import os
    rc = 0
    for f in files:
        if not os.path.isfile(f):
            print(f"cryoc fmt: file not found: {f}", file=sys.stderr)
            rc = 2
            continue
        with open(f, 'r', encoding='utf-8') as fh:
            src = fh.read()
        formatted, ok = _safe_format(src)
        if not ok:
            print(f"cryoc fmt: {f} was not safely formatted "
                  f"(invalid syntax or unsupported case); kept intact",
                  file=sys.stderr)
            rc = max(rc, 1)
            if not (write or check):
                sys.stdout.write(src)
            continue
        if check:
            if formatted != src:
                print(f"cryoc fmt: {f} is not formatted", file=sys.stderr)
                rc = max(rc, 1)
        elif write:
            if formatted != src:
                with open(f, 'w', encoding='utf-8') as fh:
                    fh.write(formatted)
                print(f"formatted: {f}")
        else:
            sys.stdout.write(formatted)
    return rc


if __name__ == '__main__':
    sys.exit(main())
