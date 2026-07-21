# ============================================================
#  Cryo — Automatic backend selection (--backend auto)
#
#  Analyzes the AST, deduces which features the program depends on and
#  chooses the most suitable backend — optimizing processing:
#  pure core programs go to Pyro bytecode (lightweight, no
#  external toolchain); advanced features scale to Go; foreign blocks
#  force the backend that emits them (Go/C/Node).
#
#  The choice is conservative: it only recommends a backend that knows
#  how to compile all used features. When in doubt, falls back to 'go'
#  (superset). The compiler still has a safety net
#  that recompiles in 'go' if the chosen backend fails.
# ============================================================
from dataclasses import fields
from typing import Any, Tuple, Set

from ast_nodes import (
    Node, FunctionDecl, StructDecl, EnumDecl, SkillDecl, VarDecl, ConstDecl,
    ForEach, TryCatch, ForeignBlock, MapLiteral, CastExpr, UnwrapExpr,
    SpawnExpr, AwaitExpr, ArrayLiteral, StructInit, Literal, CallExpr, Lambda,
)


# builtin -> required capability tag
_CALL_TAG = {
    'to_string': 'convfn', 'to_int': 'convfn', 'to_number': 'convfn',
    'input': 'input',
    'sqrt': 'mathfn', 'pow': 'mathfn', 'abs': 'mathfn', 'min': 'mathfn',
    'max': 'mathfn', 'floor': 'mathfn', 'ceil': 'mathfn', 'round': 'mathfn',
    'json_encode': 'json', 'json_decode': 'json',
    'upper': 'strfn', 'lower': 'strfn', 'trim': 'strfn', 'contains': 'strfn',
    'find': 'strfn', 'replace': 'strfn', 'substr': 'strfn', 'split': 'strfn',
    'join': 'strfn',
    'http_get': 'http', 'http_post': 'http', 'sleep': 'http',
    'llm': 'llm', 'agent': 'llm', 'tools': 'llm', 'tools_json': 'llm',
    'tool_get': 'llm', 'schema_of': 'llm', 'skills': 'llm', 'skill_get': 'llm',
    'skill_has': 'llm', 'skills_json': 'llm',
    'remove': 'mapremove',
    'pyro_exec': 'machine', 'pyro_env': 'machine', 'pyro_args': 'machine',
    'pyro_time': 'machine', 'pyro_read': 'machine', 'pyro_write': 'machine',
    'pyro_write_file': 'machine', 'pyro_open': 'machine', 'pyro_exit': 'machine',
}

# backend -> set of supported capability tags
_SUPPORTS = {
    'asm':  set(),
    'pyro': {'float', 'string', 'array', 'map', 'struct', 'enum',
             'convfn', 'mathfn', 'mapremove', 'strfn', 'trycatch',
             'optional', 'cast', 'input', 'json', 'http'},
    'c':    {'float', 'string', 'array', 'struct', 'enum', 'trycatch', 'mathfn'},
    'node': {'float', 'string', 'array', 'map', 'struct', 'enum', 'optional',
             'json', 'cast', 'trycatch', 'convfn', 'mathfn', 'mapremove', 'strfn',
             'firstclassfn'},
    'go':   {'float', 'string', 'array', 'map', 'struct', 'enum', 'optional',
             'json', 'cast', 'trycatch', 'convfn', 'mathfn', 'mapremove', 'strfn',
             'concurrency', 'llm', 'http', 'machine', 'input', 'firstclassfn'},
}

# foreign languages that each backend can emit
_LANG_OF = {
    'go': {'go'}, 'node': {'node', 'js', 'javascript'}, 'c': {'c'},
    'pyro': set(), 'asm': set(),
}

# preference order: more lightweight/native first
_PREF = ['pyro', 'go', 'node', 'c', 'asm']


def _walk(node: Any):
    if isinstance(node, Node):
        yield node
        for f in fields(node):
            yield from _walk(getattr(node, f.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


def _type_tags(t: str) -> Set[str]:
    tags: Set[str] = set()
    if not t:
        return tags
    if '?' in t:
        tags.add('optional')
    if 'map<' in t:
        tags.add('map')
    if 'future<' in t:
        tags.add('concurrency')
    if '[]' in t:
        tags.add('array')
    if 'number' in t:
        tags.add('float')
    if 'string' in t:
        tags.add('string')
    if 'fn(' in t:
        tags.add('firstclassfn')
    return tags


def analyze(program) -> Tuple[Set[str], Set[str]]:
    """Returns (required capability tags, foreign block languages)."""
    tags: Set[str] = set()
    foreign: Set[str] = set()

    for n in _walk(program):
        if isinstance(n, EnumDecl):
            tags.add('enum')
        elif isinstance(n, SkillDecl):
            tags.add('llm')
        elif isinstance(n, TryCatch):
            tags.add('trycatch')
        elif isinstance(n, MapLiteral):
            tags.add('map')
        elif isinstance(n, CastExpr):
            tags.add('cast')
        elif isinstance(n, UnwrapExpr):
            tags.add('optional')
        elif isinstance(n, (SpawnExpr, AwaitExpr)):
            tags.add('concurrency')
        elif isinstance(n, Lambda):
            tags.add('firstclassfn')
        elif isinstance(n, ForeignBlock):
            foreign.add(n.lang.strip().lower())
        elif isinstance(n, FunctionDecl):
            if n.is_tool:
                tags.add('llm')
            tags |= _type_tags(n.return_type)
            for p in n.params:
                tags |= _type_tags(p[0])
        elif isinstance(n, (VarDecl, ConstDecl)):
            tags |= _type_tags(n.var_type)
        elif isinstance(n, ForEach):
            tags |= _type_tags(n.var_type)
        elif isinstance(n, ArrayLiteral):
            tags.add('array')
        elif isinstance(n, StructInit):
            tags.add('struct')
        elif isinstance(n, Literal):
            if n.kind == 'float':
                tags.add('float')
            elif n.kind == 'string':
                tags.add('string')
        elif isinstance(n, CallExpr):
            tg = _CALL_TAG.get(n.callee)
            if tg:
                tags.add(tg)

    return tags, foreign


_ADVANCED = {'concurrency', 'llm', 'http', 'machine', 'optional', 'json',
             'cast', 'enum', 'trycatch', 'convfn', 'mathfn', 'mapremove', 'input',
             'firstclassfn'}


def _reason(backend: str, tags: Set[str], foreign: Set[str]) -> str:
    if foreign:
        langs = '/'.join(sorted(foreign))
        return f"foreign block ({langs}) is only emitted by the {backend} backend"
    if backend == 'pyro':
        return "pure core — Pyro bytecode (more lightweight, without external toolchain)"
    if backend == 'go':
        adv = sorted(tags & _ADVANCED)
        return ("features beyond the Pyro core"
                + (f" ({', '.join(adv)})" if adv else ""))
    return f"features covered by the {backend} backend"


def missing_capabilities(program, backend: str) -> Tuple[Set[str], Set[str]]:
    """Features required by the program that ``backend`` DOES NOT cover.

    Returns (missing_feature_tags, unissued_block_languages).
    Empty in both when the backend can handle everything.
    """
    tags, foreign = analyze(program)
    missing_tags = tags - _SUPPORTS.get(backend, set())
    missing_foreign = foreign - _LANG_OF.get(backend, set())
    return missing_tags, missing_foreign


def select_backend(program) -> Tuple[str, str]:
    """Chooses the ideal backend for the program. Returns (backend, reason)."""
    tags, foreign = analyze(program)

    for b in _PREF:
        if not tags <= _SUPPORTS[b]:
            continue
        if foreign and not foreign <= _LANG_OF[b]:
            continue
        return b, _reason(b, tags, foreign)

    # no backend covers everything (e.g., map + C block) — go as safe fallback
    return 'go', "combination of features without a single ideal backend; go as fallback"
