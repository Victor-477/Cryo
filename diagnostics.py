"""
Cryo — diagnostic rendering (roadmap 11.24)

Before this, an error was a line number and a sentence:

    - Line 6: [Semantic Error] unknown function 'lenght'

which is enough to find the line and not enough to see the mistake. This module
renders the same information against the source, so the problem is visible
rather than described:

    [Semantic Error] unknown function 'lenght'
      --> app.cryo:6:13
       |
     6 | int total = lenght(items);
       |             ^^^^^^
       = did you mean `len`?

Two things it does, both deliberately small:

  * `render` finds the offending text ON the reported line and underlines it.
    AST nodes carry a line but almost never a column, and threading columns
    through every node would touch the whole front end for a caret. Searching
    the line for the name that is already in the message gets the same result
    and costs nothing.

  * `suggest` proposes a near name. The one that existed (10.12's structure
    parameters) compared the first three characters, which finds `total` from
    `totl` but not `length` from `lenght` — a transposition, the single most
    common typo. Edit distance handles both.
"""
import difflib
import os
from typing import Iterable, List, Optional, Sequence

# Wide enough to be useful, narrow enough that a long line does not push the
# caret off the terminal.
_MAX_LINE = 200


def suggest(name: str, candidates: Iterable[str], limit: int = 2) -> List[str]:
    """Names close to `name`, best first.

    Edit-distance based, so a transposition (`lenght` -> `length`) and a
    dropped character (`totl` -> `total`) are both found. The cutoff is high
    enough that an unrelated name is not offered — a wrong suggestion sends
    the reader looking in the wrong place, which is worse than none.
    """
    if not name:
        return []
    pool = [c for c in dict.fromkeys(candidates) if c and c != name]
    if not pool:
        return []
    close = difflib.get_close_matches(name, pool, n=limit, cutoff=0.72)
    if close:
        return close
    # A short name is a hard case for ratio-based matching: `xs` against `x`
    # scores poorly. Fall back to a case-insensitive prefix, which is what a
    # reader would notice anyway.
    low = name.lower()
    pre = sorted(c for c in pool if c.lower().startswith(low[:2]))
    return pre[:limit]


def hint(name: str, candidates: Iterable[str]) -> str:
    """`did you mean …` as a sentence fragment, or ''."""
    near = suggest(name, candidates)
    if not near:
        return ''
    if len(near) == 1:
        return f"did you mean `{near[0]}`?"
    return "did you mean " + " or ".join(f"`{n}`" for n in near) + "?"


def render(source: Optional[str], line: int, message: str,
           path: Optional[str] = None, needle: Optional[str] = None,
           note: str = '') -> str:
    """One diagnostic, with the source line and a caret under `needle`.

    Falls back to `message` alone when there is no source to point at — the
    compiler must still be able to report something when it is handed a string
    rather than a file.
    """
    head = message
    if not source or not line or line < 1:
        return head + (f"\n  = {note}" if note else '')

    lines = source.splitlines()
    if line > len(lines):
        return head + (f"\n  = {note}" if note else '')

    text = lines[line - 1]
    col, width = _locate(text, needle)
    where = f"{os.path.basename(path)}:{line}:{col + 1}" if path else f"line {line}:{col + 1}"

    shown = text[:_MAX_LINE]
    truncated = len(text) > _MAX_LINE
    gutter = str(line)
    pad = ' ' * len(gutter)

    out = [head,
           f"  --> {where}",
           f"  {pad} |",
           f"  {gutter} | {shown}" + ('  …' if truncated else ''),
           f"  {pad} | {' ' * col}{'^' * max(1, width)}"]
    if note:
        out.append(f"  {pad} = {note}")
    return '\n'.join(out)


def _locate(text: str, needle: Optional[str]):
    """(column, width) of `needle` in `text`, as a whole word where possible."""
    if not needle:
        stripped = text.lstrip()
        return (len(text) - len(stripped), max(1, len(stripped)))
    start = _find_word(text, needle)
    if start < 0:
        start = text.find(needle)
    if start < 0:
        stripped = text.lstrip()
        return (len(text) - len(stripped), max(1, len(stripped)))
    return (start, len(needle))


def _ident_char(ch: str) -> bool:
    return ch.isalnum() or ch == '_'


def _find_word(text: str, needle: str) -> int:
    """First occurrence of `needle` that is not part of a longer identifier.

    Without this, reporting `n` would underline the `n` inside `int`.
    """
    i = text.find(needle)
    while i >= 0:
        before = text[i - 1] if i > 0 else ''
        after_i = i + len(needle)
        after = text[after_i] if after_i < len(text) else ''
        if not _ident_char(before) and not _ident_char(after):
            return i
        i = text.find(needle, i + 1)
    return -1


def render_all(source: Optional[str], items: Sequence, path: Optional[str] = None,
               limit: int = 20) -> str:
    """Several diagnostics at once.

    `items` are (line, message, needle, note) tuples. Reporting every problem
    in a pass rather than the first is the point of batching — one recompile
    per mistake is the slowest way to fix a file.
    """
    blocks = []
    for it in items[:limit]:
        line, message, needle, note = (list(it) + [None, None, ''])[:4]
        blocks.append(render(source, line, message, path, needle, note or ''))
    out = '\n\n'.join(blocks)
    if len(items) > limit:
        out += f"\n\n  … and {len(items) - limit} more"
    return out
