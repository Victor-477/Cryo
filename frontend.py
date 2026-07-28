# ============================================================
#  Cryo — Front-end structure  (roadmap 10.11 + 10.13)
#
#  A Cryo file becomes a web page by wrapping foreign blocks of the
#  three front-end languages in ordinary Cryo functions:
#
#      fn styles()   ={ >CSS( body { color: #eee; } ) }
#      fn behavior() ={ >javascript( console.log("hi"); ) }
#      fn page()     ={ >html( <h1>Hi</h1> )<script=behavior, style=styles> }
#
#  Wrapping in a function is what gives a block a NAME, which is what
#  the structure parameters (10.12) refer to — `script=behavior` means
#  "compose the javascript block named `behavior` into this page".
#  Nothing new is needed to declare a name: functions already exist.
#
#  This module is a pure AST -> text pass. It performs no I/O, so it
#  can be unit-tested without touching the filesystem, and it is shared
#  by both output modes of 10.13 (see `render`).
# ============================================================
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ast_nodes import Node, FunctionDecl, ForeignBlock
from foreign import _walk


class FrontendError(Exception):
    """A front-end structure that cannot be assembled into a page."""
    pass


# Accepted spellings per slot. `js` and `style` are allowed because the
# roadmap and the docs both use the long names while the wild uses the short
# ones; rejecting a synonym here would be a pointless papercut.
_HTML = {'html'}
_JS   = {'javascript', 'js'}
_CSS  = {'css'}

FRONTEND_LANGS = _HTML | _JS | _CSS

# structure-parameter key -> the languages that key may point at
_SLOT_LANGS = {'script': _JS, 'style': _CSS}


def _norm(lang: str) -> str:
    return (lang or '').strip().lower()


@dataclass
class Block:
    """One named front-end block: a function whose body is a foreign block."""
    name:   str
    lang:   str                       # normalized
    code:   str
    params: List[Tuple[str, str]] = field(default_factory=list)
    line:   int = 0


@dataclass
class FrontendModule:
    page:   Block                     # the html block that becomes the document
    script: Optional[Block] = None    # resolved from <script=...>
    style:  Optional[Block] = None    # resolved from <style=...>

    @property
    def title(self) -> str:
        return self.page.name


def _blocks(program) -> Dict[str, Block]:
    """Every top-level function whose body is a single front-end foreign block.

    A function containing a front-end block *plus* other statements is not a
    named block — it is ordinary Cryo code that happens to embed markup, and
    treating it as a page fragment would silently drop the rest of the body.
    """
    found: Dict[str, Block] = {}
    for n in _walk(program):
        if not isinstance(n, FunctionDecl):
            continue
        body = [s for s in (n.body or []) if s is not None]
        if len(body) != 1 or not isinstance(body[0], ForeignBlock):
            continue
        fb = body[0]
        lang = _norm(fb.lang)
        if lang not in FRONTEND_LANGS:
            continue
        found[n.name] = Block(n.name, lang, fb.code, list(fb.params),
                              getattr(fb, 'line', 0) or getattr(n, 'line', 0))
    return found


def collect(program) -> FrontendModule:
    """Resolve a program's front-end structure into one renderable module."""
    blocks = _blocks(program)
    pages = [b for b in blocks.values() if b.lang in _HTML]

    if not pages:
        raise FrontendError(
            "no html block found. A front-end file needs a function whose body "
            "is an >html( ... ) block, e.g.\n"
            "    fn page() ={ >html( <h1>Hi</h1> ) }"
        )
    if len(pages) > 1:
        named = [p for p in pages if p.name == 'page']
        if len(named) != 1:
            names = ', '.join(sorted(p.name for p in pages))
            raise FrontendError(
                f"this file has {len(pages)} html blocks ({names}), so the entry "
                f"page is ambiguous. Name exactly one of them `page`, or split "
                f"them into separate files — one page per file."
            )
        pages = named
    page = pages[0]

    mod = FrontendModule(page=page)
    for key, val in page.params:
        if key not in _SLOT_LANGS:
            slots = ', '.join(sorted(_SLOT_LANGS))
            raise FrontendError(
                f">html( ... )<{key} = {val}> is not a known structure "
                f"parameter for an html block. Valid keys: {slots}."
            )
        target = blocks.get(val)
        if target is None:
            # foreign.verify_struct_params already proved the NAME exists, so
            # if it is missing here it exists but is not a front-end block.
            raise FrontendError(
                f">html( ... )<{key} = {val}> points at '{val}', which is not a "
                f"front-end block. It must be a function whose body is a single "
                f">javascript( ... ) or >CSS( ... ) block."
            )
        want = _SLOT_LANGS[key]
        if target.lang not in want:
            expected = ' or '.join(sorted(want))
            raise FrontendError(
                f">html( ... )<{key} = {val}> expects a {expected} block, but "
                f"'{val}' is a {target.lang} block. Swapping `script` and "
                f"`style` is the usual cause."
            )
        setattr(mod, 'script' if key == 'script' else 'style', target)
    return mod


# ── rendering (10.13: two output modes) ─────────────────────

def _indent(text: str, pad: str) -> str:
    lines = [ln.rstrip() for ln in (text or '').strip().split('\n')]
    return '\n'.join(pad + ln if ln else '' for ln in lines)


def _document(title: str, style: str, body: str, script: str) -> str:
    """The one HTML skeleton both output modes share.

    Both modes must produce the same document *structure* so that switching
    `--emit` changes only how the logic arrives, never how the page looks.
    """
    head = ['<!doctype html>', '<html lang="en">', '<head>',
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, '
            'initial-scale=1">',
            f'  <title>{title}</title>']
    if style:
        head += ['  <style>', _indent(style, '    '), '  </style>']
    head += ['</head>', '<body>']
    tail = []
    if script:
        tail += ['  <script>', _indent(script, '    '), '  </script>']
    tail += ['</body>', '</html>', '']
    return '\n'.join(head + [_indent(body, '  ')] + tail)


def render_html(mod: FrontendModule) -> str:
    """Mode `html` — one self-contained vanilla file, no build step, no fetch.

    CSS and JS are inlined rather than linked so the result opens correctly
    from a `file://` path; a linked stylesheet would work over http only.
    """
    return _document(
        title=mod.title,
        style=mod.style.code if mod.style else '',
        body=mod.page.code,
        script=mod.script.code if mod.script else '',
    )


def render_pyro(mod: FrontendModule, binary: str = 'app.wasm') -> str:
    """Mode `pyro` — the same page, but the logic ships as a **binary**.

    The Cryo functions of the program are compiled separately to a WebAssembly
    module; this document is the shell that loads it. The author's own
    javascript block still runs, after the module is ready, so it can call the
    exported Cryo functions through the global `cryo`.
    """
    author = mod.script.code.strip() if mod.script else ''
    loader = [
        f"// Loads {binary} — the Cryo functions of this program, compiled to",
        "// a binary the browser executes directly. i64 exports surface as",
        "// BigInt in JS, so numbers coming back from Cryo are BigInt.",
        "let cryo = null;",
        "async function __cryo_boot() {",
        f"  const res = await fetch({binary!r});",
        "  if (!res.ok) throw new Error('cannot load ' + res.url);",
        "  const { instance } = await WebAssembly.instantiate(",
        "    await res.arrayBuffer(),",
        "    { env: { log: (x) => console.log('[cryo]', x.toString()) } });",
        "  cryo = instance.exports;",
        "  document.dispatchEvent(new CustomEvent('cryo:ready'));",
        "}",
    ]
    if author:
        loader += [
            "",
            "// --- author's >javascript( ... ) block, run once Cryo is ready ---",
            "document.addEventListener('cryo:ready', function () {",
            _indent(author, '  '),
            "});",
        ]
    loader += ["", "__cryo_boot().catch((e) => console.error('[cryo]', e));"]
    return _document(
        title=mod.title,
        style=mod.style.code if mod.style else '',
        body=mod.page.code,
        script='\n'.join(loader),
    )


def strip_frontend(program):
    """The program with its front-end declarations removed.

    `--emit pyro` compiles the program's *logic* to a binary, and the front-end
    parts are not logic: `import >html<` and the block-wrapping functions
    describe the document. Handing them to a code generator is what produced
    "'Import' not supported by the wasm backend" — the generator was being
    asked to compile the page. Returns a shallow copy; `program` is untouched.
    """
    import copy
    from ast_nodes import Import, Program

    blocks = set(_blocks(program))
    kept = []
    for s in (program.statements or []):
        if isinstance(s, Import) and _norm(getattr(s, 'lang', '')) in FRONTEND_LANGS:
            continue
        if isinstance(s, FunctionDecl) and s.name in blocks:
            continue
        kept.append(s)
    out = copy.copy(program)
    out.statements = kept
    return out


def has_logic(program) -> bool:
    """True if anything remains to compile once the page is stripped away."""
    return bool(strip_frontend(program).statements)


def render(program, emit: str = 'html', binary: str = 'app.wasm') -> str:
    """Entry point used by the compiler. `emit` is 'html' or 'pyro'."""
    mod = collect(program)
    if emit == 'html':
        return render_html(mod)
    if emit == 'pyro':
        return render_pyro(mod, binary)
    raise FrontendError(
        f"unknown front-end output mode '{emit}'. Use --emit html (a single "
        f"vanilla .html file) or --emit pyro (an .html shell plus a binary)."
    )
