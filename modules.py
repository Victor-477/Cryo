# ============================================================
#  Cryo — Module resolution (import "file.cryo" [as alias])
# ============================================================
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from dataclasses import fields
from ast_nodes import (
    Program, Node, ModuleImport, Import, Library, QualifiedIdentifier,
    FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl, VarDecl, Assignment,
    CompoundAssignment, IndexAssignment, FieldAssignment, Increment, Return, If, While, For, DoWhile,
    ForEach, TryCatch, Block, Break, Continue, Switch, SwitchCase, Assert, SafetyBlock,
    BinaryExpr, TernaryExpr, UnaryExpr, CallExpr, CallValueExpr, MethodCallExpr,
    FieldAccess, IndexAccess, ArrayLiteral, MapLiteral, StructInit, Lambda, Identifier,
    MatchStatement, MatchCase, CastExpr, UnwrapExpr, TryExpr, ForeignBlock,
    EnumMember, carry_meta
)


class ModuleError(Exception):
    """Module resolution error (missing file, cycle, collision, visibility)."""
    pass


# declarations that a module exports
_DECLS = (FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl,
          VarDecl,            # module state (11.1) is part of a module too
          Import, Library)


def _decl_name(n: Node) -> Optional[str]:
    return getattr(n, 'name', None) if isinstance(
        n, (FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl,
            VarDecl)) else None


# ── renaming a module's own references (ISSUES/19) ─────────
#
# When a module is imported under an alias every one of its declarations is
# mangled to `alias__name`. Its OWN code still says `name`, so those references
# have to be rewritten in step or the module refers to symbols that no longer
# exist — which is precisely how a pub function calling a pub sibling failed
# with "unknown function".

_NAME_FIELDS = {
    'Identifier': ('name',),
    'CallExpr': ('callee',),
    'Assignment': ('name',),
    'CompoundAssignment': ('name',),
    'Increment': ('name',),
    'StructInit': ('struct_name',),
}
_TYPE_FIELDS = {
    'VarDecl': ('var_type',),
    'ConstDecl': ('var_type',),
    'ForEach': ('var_type',),
    'FunctionDecl': ('return_type',),
    'CastExpr': ('target_type',),
    'StructField': ('field_type',),
    'Lambda': ('return_type',),
    'TryCatch': ('catch_type',),
}

# a bare name inside a type string, not part of `ns::Name` on either side
_BARE_IN_TYPE = re.compile(r'(?<![\w:])([A-Za-z_]\w*)(?![\w:])')
# `ns::Name` anywhere inside a type string: `k::T[]`, `map<string,k::T>`, ...
_QUALIFIED_IN_TYPE = re.compile(r'\b([A-Za-z_]\w*)::([A-Za-z_]\w*)')
# fields that hold a single type string, on any node
_TYPE_STR_FIELDS = ('var_type', 'field_type', 'return_type', 'target_type', 'catch_type')


def _bound_names(fn) -> Set[str]:
    """Names a function binds itself: parameters and local declarations.

    A local of the same name SHADOWS the module's, so it must not be renamed.
    Without this, a function with its own `count` would have that local
    rewritten to `store__count` and silently read module state instead.
    """
    names = {pn for _pt, pn in getattr(fn, 'params', []) or []}

    def walk(node):
        if isinstance(node, (list, tuple)):
            for x in node:
                walk(x)
            return
        if not isinstance(node, Node):
            return
        if isinstance(node, (VarDecl, ConstDecl)):
            names.add(node.name)
        if isinstance(node, ForEach):
            names.add(node.var_name)
        for f in fields(node):
            walk(getattr(node, f.name))

    walk(getattr(fn, 'body', []) or [])
    return names


def _rename_type(t, mapping: Dict[str, str], types: Dict[str, str]):
    """A type string, with this module's own type names mangled (L-17).

    `T` alone was always renamed; `T[]`, `T?`, `map<string,T>` and
    `fn(T)->T` were not, so a module using its own struct inside any of them
    referred to a `T` that, once mangled to `alias__T`, no longer existed —
    which only the Go backend, being typed, noticed. Only TYPE names are
    rewritten inside a composite: a module function called `map` must not
    turn `map<string,int>` into `alias__map<string,int>`.
    """
    if not isinstance(t, str):
        return t
    if t in mapping:
        return mapping[t]
    if not types:
        return t
    return _BARE_IN_TYPE.sub(lambda m: types.get(m.group(1), m.group(1)), t)


def _rename_refs(node, mapping: Dict[str, str], types: Optional[Dict[str, str]] = None):
    """Rewrite every reference to one of this module's own declarations.

    `types` is the part of `mapping` that names types (structs, enums); it is
    what gets rewritten inside composite type strings.
    """
    types = types or {}
    if isinstance(node, list):
        return [_rename_refs(x, mapping, types) for x in node]
    if isinstance(node, tuple):
        return tuple(_rename_refs(x, mapping, types) for x in node)
    if not isinstance(node, Node):
        return node

    if isinstance(node, (FunctionDecl, StructDecl)) and node.type_params:
        # a type parameter shadows a module type of the same name
        types = {k: v for k, v in types.items() if k not in node.type_params}

    if isinstance(node, FunctionDecl):
        # inside a function, drop the names it shadows
        inner = {k: v for k, v in mapping.items() if k not in _bound_names(node)}
        params = [(_rename_type(pt, inner, types), pn) for pt, pn in (node.params or [])]
        fn = FunctionDecl(
            node.name, params,
            _rename_type(node.return_type, inner, types),
            _rename_refs(node.body, inner, types),
            is_tool=node.is_tool, is_test=node.is_test, line=node.line,
            type_params=node.type_params, type_bounds=node.type_bounds,
            is_pub=node.is_pub)
        carry_meta(node, fn)
        return fn

    kind = type(node).__name__
    kwargs = {}
    for f in fields(node):
        val = getattr(node, f.name)
        if kind in _NAME_FIELDS and f.name in _NAME_FIELDS[kind] and isinstance(val, str):
            kwargs[f.name] = mapping.get(val, val)
        elif kind in _TYPE_FIELDS and f.name in _TYPE_FIELDS[kind] and isinstance(val, str):
            kwargs[f.name] = _rename_type(val, mapping, types)
        elif f.name == 'params' and isinstance(node, Lambda):
            kwargs[f.name] = [(_rename_type(pt, mapping, types), pn) for pt, pn in val]
        elif f.name == 'type_args' and isinstance(val, list):
            kwargs[f.name] = [_rename_type(t, mapping, types) for t in val]
        elif f.name == 'members' and isinstance(node, EnumDecl):
            kwargs[f.name] = [EnumMember(m.name, [_rename_type(t, mapping, types) for t in m.fields],
                                         line=m.line) for m in val]
        else:
            kwargs[f.name] = _rename_refs(val, mapping, types)
    try:
        new = type(node)(**kwargs)
    except TypeError:
        return node
    carry_meta(node, new)
    return new


# 11.23 — set by the compiler to a Burnout.cache.ParseCache, and to a list that
# collects (path, source) for every file actually read. The list is what the
# artifact key is built from: it is the exact set of inputs this compilation
# depended on, so a cached artifact can only be reused when all of them match.
# Both stay None when the cache is off, and this module never imports Burnout —
# the front end does not depend on the compiler driver.
PARSE_CACHE = None
READ_SOURCES = None

# 12.3 — the package manifest governing this build. Left None, in which case it
# is discovered from the importing file's directory the FIRST time an `@` import
# is seen; a test or a driver can set it to a packages.Manifest to pin it.
#
# Lazy on purpose. A project with no `@` import never reads a cryo.toml, never
# pays for it, and cannot be broken by one — which is the property that lets
# 12.3 be added without auditing what it does to every existing project.
PACKAGE_MANIFEST = None


def _parse_file(path: str) -> Program:
    try:
        return _parse_file_raw(path)
    except Exception as e:
        # A syntax/lexical error in an IMPORTED file was rendered against the
        # importer: its name, its line, its caret (EMP ESCOPO L-15). Tag the
        # error with the file it really came from; the innermost import wins,
        # because only the first _parse_file the exception passes sets it.
        if type(e).__name__ in ('ParseError', 'ParseErrors', 'LexerError')                 and getattr(e, 'path', None) is None:
            e.path = path
        raise


def _parse_file_raw(path: str) -> Program:
    from lexer import Lexer
    from parser import Parser
    try:
        with open(path, 'r', encoding='utf-8') as f:
            src = f.read()
    except OSError as e:
        raise ModuleError(f"[Module Error] could not read module '{path}': {e}")
    if READ_SOURCES is not None:
        READ_SOURCES.append((path, src))
    if PARSE_CACHE is not None:
        hit = PARSE_CACHE.get(src)
        if hit is not None:
            return hit
        ast = Parser(Lexer(src).tokenize()).parse()
        PARSE_CACHE.put(src, ast)
        return ast
    return Parser(Lexer(src).tokenize()).parse()


def resolve_modules(program: Program, base_dir: str) -> Program:
    loaded: Set[Tuple[str, Optional[str]]] = set()  # (abspath, alias) already incorporated
    loading: List[str] = []                         # stack for cycle detection
    origem: Dict[str, str] = {}                     # declaration name -> file
    decls: List[Node] = []

    # (alias, symbol_name) -> (mangled_name, is_pub)
    aliased_exports: Dict[Tuple[str, str], Tuple[str, bool]] = {}

    def load(path: str, importer_dir: str, alias: Optional[str] = None):
        full = None
        if path.startswith('@'):
            # `import "@dep/file.cryo"` — resolved against the manifest rather
            # than the importing file's directory.
            import packages
            try:
                mf = PACKAGE_MANIFEST
                if mf is None:
                    mf = packages.load(importer_dir)
                full = packages.resolve_import(path, importer_dir, mf)
            except packages.PackageError as e:
                raise ModuleError(f"[Module Error] {e}")
        if full is None:
            full = os.path.normpath(os.path.join(importer_dir, path))
            full = os.path.abspath(full)
        if (full, alias) in loaded:
            return                       # dedup
        if full in loading:
            cadeia = ' -> '.join(os.path.basename(p) for p in loading + [full])
            raise ModuleError(f"[Module Error] import cycle detected: {cadeia}")
        if not os.path.isfile(full):
            raise ModuleError(f"[Module Error] module not found: '{path}' (looked in {full})")

        loading.append(full)
        mod = _parse_file(full)
        mod_dir = os.path.dirname(full)

        # ISSUES/19 — every declaration of an aliased module is mangled and
        # KEPT, pub or not. Privacy is enforced by name resolution below
        # (`ns::name` refuses a non-pub symbol), not by deleting the symbol:
        # deleting it also removed it from the module's own reach.
        local_rename: Dict[str, str] = {}
        local_types: Dict[str, str] = {}
        if alias:
            for n in mod.statements:
                nm = _decl_name(n) if isinstance(n, _DECLS) else None
                if nm:
                    local_rename[nm] = f"{alias}__{nm}"
                    if isinstance(n, (StructDecl, EnumDecl)):
                        local_types[nm] = local_rename[nm]

        for n in mod.statements:
            if isinstance(n, ModuleImport):
                load(n.path, mod_dir, n.alias)
            elif isinstance(n, _DECLS):
                name = _decl_name(n)
                if name:
                    if alias:
                        mangled = f"{alias}__{name}"
                        is_pub = getattr(n, 'is_pub', False)
                        aliased_exports[(alias, name)] = (mangled, is_pub)
                        # rewrite this declaration's own references first, so a
                        # call to a sibling follows the mangling
                        n = _rename_refs(n, local_rename, local_types)
                        if isinstance(n, FunctionDecl):
                            mangled_node = FunctionDecl(mangled, n.params, n.return_type, n.body, is_tool=n.is_tool, line=n.line, type_params=n.type_params, type_bounds=n.type_bounds, is_pub=n.is_pub)
                        elif isinstance(n, StructDecl):
                            mangled_node = StructDecl(mangled, n.fields, line=n.line, type_params=n.type_params, type_bounds=n.type_bounds, is_pub=n.is_pub)
                        elif isinstance(n, EnumDecl):
                            mangled_node = EnumDecl(mangled, n.members, line=n.line, is_pub=n.is_pub)
                        elif isinstance(n, ConstDecl):
                            mangled_node = ConstDecl(n.var_type, mangled, n.value, is_pub=n.is_pub)
                        elif isinstance(n, VarDecl):
                            mangled_node = VarDecl(n.var_type, mangled, n.value)
                        elif isinstance(n, SkillDecl):
                            mangled_node = SkillDecl(mangled, n.fields)
                        else:
                            mangled_node = n
                        decls.append(mangled_node)
                    else:
                        if name in origem and origem[name] != full:
                            raise ModuleError(
                                f"[Module Error] duplicate declaration '{name}': defined in "
                                f"{origem[name]} and in {full}")
                        origem.setdefault(name, full)
                        decls.append(n)
                elif isinstance(n, (Import, Library)):
                    # Preserve foreign imports and libraries declared within imported modules
                    if not any(type(d) is type(n) and getattr(d, 'lang', None) == getattr(n, 'lang', None) and getattr(d, 'name', None) == getattr(n, 'name', None) for d in decls):
                        decls.append(n)

        loading.pop()
        loaded.add((full, alias))

    # scan entry program
    rest: List[Node] = []
    for n in program.statements:
        if isinstance(n, ModuleImport):
            load(n.path, base_dir, n.alias)
        else:
            name = _decl_name(n)
            if name and name in origem:
                raise ModuleError(
                    f"[Module Error] duplicate declaration '{name}': defined in "
                    f"{origem[name]} and in the main program")
            rest.append(n)

    # Transform qualified names ns::member -> mangled name ns__member
    aliases = {a for a, _ in aliased_exports}

    def _qual_type(t):
        """Mangle every `ns::Name` inside a type string (L-17).

        A type is not always a bare name: `k::T[]`, `k::T?`, `map<string,k::T>`
        and `fn(k::T)->k::R` all hold a qualified name somewhere inside. Only
        a type that was exactly `ns::T` used to be rewritten, so the composite
        shapes reached the code generators with the raw `::` text — harmless
        to the dynamic backends, a syntax error in Go. A name whose namespace
        is not an import alias is left alone.
        """
        if not isinstance(t, str) or '::' not in t:
            return t

        def sub(m):
            ns, tname = m.group(1), m.group(2)
            if ns not in aliases:
                return m.group(0)
            key = (ns, tname)
            if key not in aliased_exports:
                raise ModuleError(f"[Module Error] unknown type '{tname}' in module '{ns}'")
            mangled, is_pub = aliased_exports[key]
            if not is_pub:
                raise ModuleError(f"[Module Error] '{tname}' is not pub in module '{ns}'")
            return mangled
        return _QUALIFIED_IN_TYPE.sub(sub, t)

    def _qual_params(params):
        return [(_qual_type(pt), pn) for pt, pn in (params or [])]

    def _transform_raw(n: Node) -> Node:
        if n is None:
            return None

        if isinstance(n, QualifiedIdentifier):
            key = (n.namespace, n.name)
            if key in aliased_exports:
                mangled, is_pub = aliased_exports[key]
                if not is_pub:
                    raise ModuleError(f"[Module Error] '{n.name}' is not pub in module '{n.namespace}'")
                return Identifier(mangled, line=n.line)
            raise ModuleError(f"[Module Error] unknown symbol '{n.name}' in module '{n.namespace}'")

        # A bare `ns::name` READ. The parser produces an Identifier whose name
        # still contains '::' (QualifiedIdentifier covers other positions), so
        # without this a pub module VARIABLE could not be read at all, and a
        # private one failed with a misleading "undeclared variable".
        if isinstance(n, Identifier) and '::' in n.name:
            ns, _, vname = n.name.partition('::')
            key = (ns, vname)
            if key in aliased_exports:
                mangled, is_pub = aliased_exports[key]
                if not is_pub:
                    raise ModuleError(
                        f"[Module Error] '{vname}' is not pub in module '{ns}'")
                return Identifier(mangled, line=n.line)
            raise ModuleError(
                f"[Module Error] unknown symbol '{vname}' in module '{ns}'")

        if isinstance(n, CallExpr) and '::' in n.callee:
            parts = n.callee.split('::', 1)
            ns, mname = parts[0], parts[1]
            key = (ns, mname)
            if key in aliased_exports:
                mangled, is_pub = aliased_exports[key]
                if not is_pub:
                    raise ModuleError(f"[Module Error] '{mname}' is not pub in module '{ns}'")
                new_args = [transform_node(a) for a in n.args]
                return CallExpr(mangled, new_args, line=n.line, type_args=[_qual_type(t) for t in n.type_args])
            raise ModuleError(f"[Module Error] unknown function '{mname}' in module '{ns}'")

        if isinstance(n, StructInit) and '::' in n.struct_name:
            parts = n.struct_name.split('::', 1)
            ns, sname = parts[0], parts[1]
            key = (ns, sname)
            if key in aliased_exports:
                mangled, is_pub = aliased_exports[key]
                if not is_pub:
                    raise ModuleError(f"[Module Error] '{sname}' is not pub in module '{ns}'")
                new_fields = [(fn, transform_node(fv)) for fn, fv in n.fields]
                return StructInit(mangled, new_fields, type_args=[_qual_type(t) for t in n.type_args])
            raise ModuleError(f"[Module Error] unknown struct '{sname}' in module '{ns}'")

        if isinstance(n, VarDecl):
            return VarDecl(_qual_type(n.var_type), n.name, transform_node(n.value))

        if isinstance(n, ConstDecl):
            return ConstDecl(_qual_type(n.var_type), n.name, transform_node(n.value), is_pub=n.is_pub)

        if isinstance(n, FunctionDecl):
            new_body = [transform_node(s) for s in n.body]
            return FunctionDecl(n.name, _qual_params(n.params), _qual_type(n.return_type), new_body, is_tool=n.is_tool, is_test=n.is_test, line=n.line, type_params=n.type_params, type_bounds=n.type_bounds, is_pub=n.is_pub)

        if isinstance(n, Assignment):
            return Assignment(n.name, transform_node(n.value))

        if isinstance(n, CompoundAssignment):
            return CompoundAssignment(n.op, n.name, transform_node(n.value))

        if isinstance(n, IndexAssignment):
            return IndexAssignment(transform_node(n.obj), transform_node(n.index), transform_node(n.value))

        if isinstance(n, FieldAssignment):
            return FieldAssignment(transform_node(n.obj), n.field, transform_node(n.value), line=n.line)

        if isinstance(n, Return):
            return Return(transform_node(n.value))

        if isinstance(n, If):
            return If(transform_node(n.condition),
                      [transform_node(s) for s in n.then_body],
                      [transform_node(s) for s in n.else_body] if n.else_body else None)

        if isinstance(n, While):
            return While(transform_node(n.condition), [transform_node(s) for s in n.body])

        if isinstance(n, For):
            return For(transform_node(n.init), transform_node(n.condition), transform_node(n.update), [transform_node(s) for s in n.body])

        if isinstance(n, DoWhile):
            return DoWhile([transform_node(s) for s in n.body], transform_node(n.condition))

        if isinstance(n, ForEach):
            return ForEach(_qual_type(n.var_type), n.var_name, transform_node(n.iterable), [transform_node(s) for s in n.body])

        if isinstance(n, Block):
            return Block([transform_node(s) for s in n.body])

        if isinstance(n, TryCatch):
            return TryCatch([transform_node(s) for s in n.try_body],
                            _qual_type(n.catch_type), n.catch_name,
                            [transform_node(s) for s in n.catch_body] if n.catch_body is not None else None,
                            [transform_node(s) for s in n.finally_body] if n.finally_body is not None else None)

        if isinstance(n, Switch):
            return Switch(transform_node(n.subject),
                          [transform_node(c) for c in n.cases],
                          [transform_node(s) for s in n.default_body] if n.default_body is not None else None)

        if isinstance(n, SwitchCase):
            return SwitchCase([transform_node(v) for v in n.values], [transform_node(s) for s in n.body])

        if isinstance(n, Assert):
            return Assert(transform_node(n.condition), transform_node(n.message), line=n.line)

        if isinstance(n, MatchStatement):
            return MatchStatement(transform_node(n.subject), [transform_node(c) for c in n.cases], line=n.line)

        if isinstance(n, MatchCase):
            return MatchCase(n.pattern_name, n.pattern_vars, [transform_node(s) for s in n.body],
                             line=n.line, guard=transform_node(n.guard))

        if isinstance(n, CastExpr):
            return CastExpr(transform_node(n.expr), _qual_type(n.target_type))

        if isinstance(n, UnwrapExpr):
            return UnwrapExpr(transform_node(n.operand))

        if isinstance(n, TryExpr):
            return TryExpr(transform_node(n.operand), line=n.line)

        if isinstance(n, BinaryExpr):
            return BinaryExpr(n.op, transform_node(n.left), transform_node(n.right))

        if isinstance(n, TernaryExpr):
            return TernaryExpr(transform_node(n.condition), transform_node(n.then_value), transform_node(n.else_value))

        if isinstance(n, UnaryExpr):
            return UnaryExpr(n.op, transform_node(n.operand))

        if isinstance(n, CallExpr):
            new_args = [transform_node(a) for a in n.args]
            return CallExpr(n.callee, new_args, line=n.line, type_args=[_qual_type(t) for t in n.type_args])

        if isinstance(n, MethodCallExpr):
            return MethodCallExpr(transform_node(n.obj), n.method, [transform_node(a) for a in n.args])

        if isinstance(n, FieldAccess):
            return FieldAccess(transform_node(n.obj), n.field)

        if isinstance(n, IndexAccess):
            return IndexAccess(transform_node(n.obj), transform_node(n.index))

        if isinstance(n, ArrayLiteral):
            return ArrayLiteral([transform_node(e) for e in n.elements])

        if isinstance(n, MapLiteral):
            return MapLiteral([(transform_node(k), transform_node(v)) for k, v in n.pairs])

        if isinstance(n, StructInit):
            new_fields = [(fn, transform_node(fv)) for fn, fv in n.fields]
            return StructInit(n.struct_name, new_fields, type_args=[_qual_type(t) for t in n.type_args])

        if isinstance(n, Lambda):
            return Lambda(_qual_params(n.params), _qual_type(n.return_type), [transform_node(s) for s in n.body], line=n.line)

        # Any other node: rebuild it from its fields so its children are still
        # visited. Without this every node type missing from the cases above
        # (`safe { }`, spawn/await, call-by-value, ...) hid a `m::f()` inside
        # it from the rewrite, and the analyser reported it as unknown.
        if isinstance(n, Node):
            return _rebuild_children(n)
        return n

    def _transform_value(v):
        if isinstance(v, Node):
            return transform_node(v)
        if isinstance(v, list):
            return [_transform_value(x) for x in v]
        if isinstance(v, tuple):
            return tuple(_transform_value(x) for x in v)
        return v

    def _rebuild_children(n: Node) -> Node:
        kwargs = {f.name: _transform_value(getattr(n, f.name)) for f in fields(n)}
        # Type strings held by nodes with no case of their own: struct fields,
        # trait signatures, `impl ... for T`. ForeignBlock params are wiring
        # names, not types.
        for k, v in kwargs.items():
            if k in _TYPE_STR_FIELDS and isinstance(v, str):
                kwargs[k] = _qual_type(v)
            elif k == 'params' and isinstance(v, list) and not isinstance(n, ForeignBlock):
                kwargs[k] = _qual_params(v)
            elif k == 'type_args' and isinstance(v, list):
                kwargs[k] = [_qual_type(t) for t in v]
            elif k == 'members' and isinstance(n, EnumDecl):
                kwargs[k] = [EnumMember(m.name, [_qual_type(t) for t in m.fields], line=m.line)
                             if any('::' in t for t in m.fields) else m for m in v]
        if all(kwargs[k] is getattr(n, k) for k in kwargs):
            return n
        try:
            return type(n)(**kwargs)
        except TypeError:
            return n

    def transform_node(n: Node) -> Node:
        """_transform_raw, with the source position carried across.

        _transform_raw rebuilds nodes to rewrite `ns::name` references, and it
        passed `line=` on only a handful of them — CallExpr, Identifier, Lambda
        — because `line` is a declared field on 16 of the 56 node classes and a
        plain attribute on the other 40. Every statement rebuilt here therefore
        arrived at the code generator with no line, and the .pyro debug section
        came out nearly empty: three entries for a twenty-line program. Stack
        traces and line breakpoints both read that section.

        Doing it here rather than at each of the ~20 constructor calls means a
        node type added later cannot reintroduce the bug by forgetting one.
        """
        out = _transform_raw(n)
        if out is not n and isinstance(n, Node) and isinstance(out, Node):
            carry_meta(n, out)
        return out

    transformed_decls = [transform_node(d) for d in decls]
    transformed_rest = [transform_node(r) for r in rest]

    final_statements = transformed_decls + transformed_rest
    return Program(final_statements)
