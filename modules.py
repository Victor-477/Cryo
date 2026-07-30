# ============================================================
#  Cryo — Module resolution (import "file.cryo" [as alias])
# ============================================================
import os
from typing import Dict, List, Optional, Set, Tuple

from dataclasses import fields
from ast_nodes import (
    Program, Node, ModuleImport, Import, Library, QualifiedIdentifier,
    FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl, VarDecl, Assignment,
    CompoundAssignment, IndexAssignment, Increment, Return, If, While, For, DoWhile,
    ForEach, TryCatch, Block, Break, Continue, Switch, SwitchCase, Assert, SafetyBlock,
    BinaryExpr, TernaryExpr, UnaryExpr, CallExpr, CallValueExpr, MethodCallExpr,
    FieldAccess, IndexAccess, ArrayLiteral, MapLiteral, StructInit, Lambda, Identifier
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
}


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


def _rename_refs(node, mapping: Dict[str, str]):
    """Rewrite every reference to one of this module's own declarations."""
    if isinstance(node, list):
        return [_rename_refs(x, mapping) for x in node]
    if isinstance(node, tuple):
        return tuple(_rename_refs(x, mapping) for x in node)
    if not isinstance(node, Node):
        return node

    if isinstance(node, FunctionDecl):
        # inside a function, drop the names it shadows
        inner = {k: v for k, v in mapping.items() if k not in _bound_names(node)}
        params = [(inner.get(pt, pt), pn) for pt, pn in (node.params or [])]
        return FunctionDecl(
            node.name, params,
            inner.get(node.return_type, node.return_type),
            _rename_refs(node.body, inner),
            is_tool=node.is_tool, line=node.line,
            type_params=node.type_params, type_bounds=node.type_bounds,
            is_pub=node.is_pub)

    kind = type(node).__name__
    kwargs = {}
    for f in fields(node):
        val = getattr(node, f.name)
        if kind in _NAME_FIELDS and f.name in _NAME_FIELDS[kind] and isinstance(val, str):
            kwargs[f.name] = mapping.get(val, val)
        elif kind in _TYPE_FIELDS and f.name in _TYPE_FIELDS[kind] and isinstance(val, str):
            kwargs[f.name] = mapping.get(val, val)
        else:
            kwargs[f.name] = _rename_refs(val, mapping)
    try:
        return type(node)(**kwargs)
    except TypeError:
        return node


def _parse_file(path: str) -> Program:
    from lexer import Lexer
    from parser import Parser
    try:
        with open(path, 'r', encoding='utf-8') as f:
            src = f.read()
    except OSError as e:
        raise ModuleError(f"[Module Error] could not read module '{path}': {e}")
    return Parser(Lexer(src).tokenize()).parse()


def resolve_modules(program: Program, base_dir: str) -> Program:
    loaded: Set[str] = set()            # abspaths already incorporated
    loading: List[str] = []             # stack for cycle detection
    origem: Dict[str, str] = {}         # declaration name -> file
    decls: List[Node] = []

    # (alias, symbol_name) -> (mangled_name, is_pub)
    aliased_exports: Dict[Tuple[str, str], Tuple[str, bool]] = {}

    def load(path: str, importer_dir: str, alias: Optional[str] = None):
        full = os.path.normpath(os.path.join(importer_dir, path))
        full = os.path.abspath(full)
        if full in loaded:
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
        if alias:
            for n in mod.statements:
                nm = _decl_name(n) if isinstance(n, _DECLS) else None
                if nm:
                    local_rename[nm] = f"{alias}__{nm}"

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
                        n = _rename_refs(n, local_rename)
                        if True:
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

        loading.pop()
        loaded.add(full)

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
    def transform_node(n: Node) -> Node:
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
                return CallExpr(mangled, new_args, line=n.line, type_args=n.type_args)
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
                return StructInit(mangled, new_fields, type_args=n.type_args)
            raise ModuleError(f"[Module Error] unknown struct '{sname}' in module '{ns}'")

        if isinstance(n, VarDecl):
            vtype = n.var_type
            if '::' in vtype:
                parts = vtype.split('::', 1)
                ns, tname = parts[0], parts[1]
                key = (ns, tname)
                if key in aliased_exports:
                    mangled, is_pub = aliased_exports[key]
                    if not is_pub:
                        raise ModuleError(f"[Module Error] '{tname}' is not pub in module '{ns}'")
                    vtype = mangled
            return VarDecl(vtype, n.name, transform_node(n.value))

        if isinstance(n, ConstDecl):
            vtype = n.var_type
            if '::' in vtype:
                parts = vtype.split('::', 1)
                ns, tname = parts[0], parts[1]
                key = (ns, tname)
                if key in aliased_exports:
                    mangled, is_pub = aliased_exports[key]
                    if not is_pub:
                        raise ModuleError(f"[Module Error] '{tname}' is not pub in module '{ns}'")
                    vtype = mangled
            return ConstDecl(vtype, n.name, transform_node(n.value), is_pub=n.is_pub)

        if isinstance(n, FunctionDecl):
            new_params = []
            for pt, pn in n.params:
                if '::' in pt:
                    parts = pt.split('::', 1)
                    ns, tname = parts[0], parts[1]
                    key = (ns, tname)
                    if key in aliased_exports:
                        mangled, is_pub = aliased_exports[key]
                        if not is_pub:
                            raise ModuleError(f"[Module Error] '{tname}' is not pub in module '{ns}'")
                        pt = mangled
                new_params.append((pt, pn))
            ret_type = n.return_type
            if ret_type and '::' in ret_type:
                parts = ret_type.split('::', 1)
                ns, tname = parts[0], parts[1]
                key = (ns, tname)
                if key in aliased_exports:
                    mangled, is_pub = aliased_exports[key]
                    if not is_pub:
                        raise ModuleError(f"[Module Error] '{tname}' is not pub in module '{ns}'")
                    ret_type = mangled
            new_body = [transform_node(s) for s in n.body]
            return FunctionDecl(n.name, new_params, ret_type, new_body, is_tool=n.is_tool, line=n.line, type_params=n.type_params, type_bounds=n.type_bounds, is_pub=n.is_pub)

        if isinstance(n, Assignment):
            return Assignment(n.name, transform_node(n.value))

        if isinstance(n, CompoundAssignment):
            return CompoundAssignment(n.op, n.name, transform_node(n.value))

        if isinstance(n, IndexAssignment):
            return IndexAssignment(transform_node(n.obj), transform_node(n.index), transform_node(n.value))

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
            return ForEach(n.var_type, n.var_name, transform_node(n.iterable), [transform_node(s) for s in n.body])

        if isinstance(n, Block):
            return Block([transform_node(s) for s in n.body])

        if isinstance(n, BinaryExpr):
            return BinaryExpr(n.op, transform_node(n.left), transform_node(n.right))

        if isinstance(n, TernaryExpr):
            return TernaryExpr(transform_node(n.condition), transform_node(n.then_value), transform_node(n.else_value))

        if isinstance(n, UnaryExpr):
            return UnaryExpr(n.op, transform_node(n.operand))

        if isinstance(n, CallExpr):
            new_args = [transform_node(a) for a in n.args]
            return CallExpr(n.callee, new_args, line=n.line, type_args=n.type_args)

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
            return StructInit(n.struct_name, new_fields, type_args=n.type_args)

        if isinstance(n, Lambda):
            return Lambda(n.params, n.return_type, [transform_node(s) for s in n.body], line=n.line)

        return n

    transformed_decls = [transform_node(d) for d in decls]
    transformed_rest = [transform_node(r) for r in rest]

    final_statements = transformed_decls + transformed_rest
    return Program(final_statements)
