# ============================================================
#  Cryo — Seleção automática de backend (--backend auto)
#
#  Analisa a AST, deduz de quais recursos o programa depende e
#  escolhe o backend mais adequado — otimizando o processamento:
#  programas de núcleo puro vão para o bytecode Pyro (leve, sem
#  toolchain externo); recursos avançados escalam para Go; blocos
#  estrangeiros forçam o backend que os emite (Go/C/Node).
#
#  A escolha é conservadora: só recomenda um backend que sabe
#  compilar todos os recursos usados. Na dúvida, cai para 'go'
#  (superconjunto). O compilador ainda tem uma rede de segurança
#  que recompila em 'go' se o backend escolhido falhar.
# ============================================================
from dataclasses import fields
from typing import Any, Tuple, Set

from ast_nodes import (
    Node, FunctionDecl, StructDecl, EnumDecl, SkillDecl, VarDecl, ConstDecl,
    ForEach, TryCatch, ForeignBlock, MapLiteral, CastExpr, UnwrapExpr,
    SpawnExpr, AwaitExpr, ArrayLiteral, StructInit, Literal, CallExpr,
)


# builtin -> tag de capacidade exigida
_CALL_TAG = {
    'to_string': 'convfn', 'to_int': 'convfn', 'to_number': 'convfn',
    'input': 'input',
    'sqrt': 'mathfn', 'pow': 'mathfn', 'abs': 'mathfn', 'min': 'mathfn',
    'max': 'mathfn', 'floor': 'mathfn', 'ceil': 'mathfn', 'round': 'mathfn',
    'json_encode': 'json', 'json_decode': 'json',
    'http_get': 'http', 'http_post': 'http', 'sleep': 'http',
    'llm': 'llm', 'agent': 'llm', 'tools': 'llm', 'tools_json': 'llm',
    'tool_get': 'llm', 'schema_of': 'llm', 'skills': 'llm', 'skill_get': 'llm',
    'skill_has': 'llm', 'skills_json': 'llm',
    'remove': 'mapremove',
    'pyro_exec': 'machine', 'pyro_env': 'machine', 'pyro_args': 'machine',
    'pyro_time': 'machine', 'pyro_read': 'machine', 'pyro_write': 'machine',
    'pyro_write_file': 'machine', 'pyro_open': 'machine', 'pyro_exit': 'machine',
}

# backend -> conjunto de tags de capacidade suportadas
_SUPPORTS = {
    'asm':  set(),
    'pyro': {'float', 'string', 'array', 'map', 'struct'},
    'c':    {'float', 'string', 'array', 'struct', 'enum', 'trycatch', 'mathfn'},
    'node': {'float', 'string', 'array', 'map', 'struct', 'enum', 'optional',
             'json', 'cast', 'trycatch', 'convfn', 'mathfn', 'mapremove'},
    'go':   {'float', 'string', 'array', 'map', 'struct', 'enum', 'optional',
             'json', 'cast', 'trycatch', 'convfn', 'mathfn', 'mapremove',
             'concurrency', 'llm', 'http', 'machine', 'input'},
}

# linguagens estrangeiras que cada backend consegue emitir
_LANG_OF = {
    'go': {'go'}, 'node': {'node', 'js', 'javascript'}, 'c': {'c'},
    'pyro': set(), 'asm': set(),
}

# ordem de preferência: mais leve/nativo primeiro
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
    return tags


def analyze(program) -> Tuple[Set[str], Set[str]]:
    """Devolve (tags de capacidade exigidas, linguagens de blocos estrangeiros)."""
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
             'cast', 'enum', 'trycatch', 'convfn', 'mathfn', 'mapremove', 'input'}


def _reason(backend: str, tags: Set[str], foreign: Set[str]) -> str:
    if foreign:
        langs = '/'.join(sorted(foreign))
        return f"bloco estrangeiro ({langs}) só é emitido pelo backend {backend}"
    if backend == 'pyro':
        return "núcleo puro — bytecode Pyro (mais leve, sem toolchain externo)"
    if backend == 'go':
        adv = sorted(tags & _ADVANCED)
        return ("recursos além do núcleo Pyro"
                + (f" ({', '.join(adv)})" if adv else ""))
    return f"recursos cobertos pelo backend {backend}"


def missing_capabilities(program, backend: str) -> Tuple[Set[str], Set[str]]:
    """Recursos exigidos pelo programa que ``backend`` NÃO cobre.

    Devolve (tags_de_recurso_faltando, linguagens_de_bloco_não_emitidas).
    Vazio nos dois quando o backend dá conta de tudo.
    """
    tags, foreign = analyze(program)
    missing_tags = tags - _SUPPORTS.get(backend, set())
    missing_foreign = foreign - _LANG_OF.get(backend, set())
    return missing_tags, missing_foreign


def select_backend(program) -> Tuple[str, str]:
    """Escolhe o backend ideal para o programa. Devolve (backend, motivo)."""
    tags, foreign = analyze(program)

    for b in _PREF:
        if not tags <= _SUPPORTS[b]:
            continue
        if foreign and not foreign <= _LANG_OF[b]:
            continue
        return b, _reason(b, tags, foreign)

    # nenhum backend cobre tudo (ex.: map + bloco C) — go como fallback seguro
    return 'go', "combinação de recursos sem backend único ideal; go como fallback"
