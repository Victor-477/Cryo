"""
Cryo — AST optimizer (roadmap 11.21)

The optimizer that existed before this was a bytecode peephole in
`codegen_pyro`: it folded adjacent constants and pruned unreachable
instructions, one window at a time, and only for the pyro backend. It could not
reason across statements, so

    int base = 10;
    int total = base * 60;
    print(total);

reached every backend as three variables and two multiplications.

This pass works on the AST instead, which buys two things. It reasons across a
whole function rather than a three-instruction window, and — because it runs
before code generation — **every backend gets the result**, including go, node,
c, wasm and asm, none of which had any optimizer at all.

Four transformations, in the order they help each other:

    1. constant folding        2 + 3          -> 5
    2. constant propagation    x = 5; x + 1   -> 6      (feeds 1)
    3. copy propagation        a = b; f(a)    -> f(b)
    4. dead local elimination  unused, pure   -> gone
    5. inlining                small leaf fns -> their body

WHAT IS DELIBERATELY NOT FOLDED
-------------------------------
Safe mode traps on integer overflow and on division by zero, and those traps
are part of the language's behaviour rather than an accident of the runtime.
Folding `1 / 0` to a compile-time error, or `INT64_MAX + 1` to a wrapped
number, would quietly change what the program does. Both are left alone so the
runtime still raises them.

Everything here is conservative by construction: a name qualifies only if it is
declared exactly once in the function and never assigned afterwards, which
sidesteps shadowing, loop carriage and capture without needing to model any of
them.
"""
from typing import Any, Dict, List, Optional, Set, Tuple

from ast_nodes import (
    Program, Node, FunctionDecl, VarDecl, ConstDecl, Assignment,
    CompoundAssignment, IndexAssignment, Increment, Return, If, While, For,
    DoWhile, ForEach, Block, TryCatch, Switch, SwitchCase, MatchStatement,
    MatchCase, BinaryExpr, TernaryExpr, UnaryExpr, CallExpr, MethodCallExpr,
    CallValueExpr, FieldAccess, IndexAccess, ArrayLiteral, MapLiteral,
    StructInit, Lambda, Identifier, Literal, CastExpr, UnwrapExpr, TryExpr,
    SpawnExpr, AwaitExpr, Assert, SafetyBlock, ForeignBlock, carry_meta,
)

_I64_MIN = -(1 << 63)
_I64_MAX = (1 << 63) - 1

# Folding is limited to operators whose meaning does not depend on types the
# AST cannot see. `+` is included only for two numbers or two strings.
_ARITH = {'+', '-', '*', '/', '%'}
_CMP = {'==', '!=', '<', '>', '<=', '>='}
_BITS = {'&', '|', '^', '<<', '>>'}


# ── walking ──────────────────────────────────────────────────

def _children(node) -> List[Node]:
    out = []
    if isinstance(node, Node):
        for f in getattr(node, '__dataclass_fields__', {}):
            v = getattr(node, f)
            if isinstance(v, Node):
                out.append(v)
            elif isinstance(v, (list, tuple)):
                for it in v:
                    if isinstance(it, Node):
                        out.append(it)
                    elif isinstance(it, (list, tuple)):
                        out.extend(x for x in it if isinstance(x, Node))
    return out


def _walk(node):
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _walk(n)
        return
    if not isinstance(node, Node):
        return
    yield node
    for c in _children(node):
        yield from _walk(c)


# ── purity ───────────────────────────────────────────────────

def _is_pure(node) -> bool:
    """True if evaluating `node` cannot be observed.

    A call is assumed impure: it may print, write a file or mutate module
    state (11.1), and dropping it would change the program.
    """
    for n in _walk(node):
        if isinstance(n, (CallExpr, MethodCallExpr, CallValueExpr, SpawnExpr,
                          AwaitExpr, ForeignBlock, TryExpr)):
            return False
    return True


# ── 1. constant folding ──────────────────────────────────────

def _lit(kind, value) -> Literal:
    return Literal(kind, value)


def _num(lit: Literal):
    return lit.value if lit.kind in ('int', 'float') else None


def fold_expr(node):
    """Fold a literal-only expression, or return it unchanged."""
    if isinstance(node, UnaryExpr):
        inner = node.operand
        if isinstance(inner, Literal):
            if node.op == '-' and inner.kind in ('int', 'float'):
                v = -inner.value
                if inner.kind == 'int' and not (_I64_MIN <= v <= _I64_MAX):
                    return node
                return _lit(inner.kind, v)
            if node.op == '!' and inner.kind == 'bool':
                return _lit('bool', not inner.value)
            if node.op == '~' and inner.kind == 'int':
                return _lit('int', ~inner.value)
        return node

    if not isinstance(node, BinaryExpr):
        return node
    a, b = node.left, node.right
    if not (isinstance(a, Literal) and isinstance(b, Literal)):
        return node
    op = node.op

    # strings: only concatenation and comparison
    if a.kind == 'string' and b.kind == 'string':
        if op == '+':
            return _lit('string', a.value + b.value)
        if op in _CMP:
            return _lit('bool', _compare(op, a.value, b.value))
        return node
    if a.kind == 'bool' and b.kind == 'bool':
        if op == '&&':
            return _lit('bool', a.value and b.value)
        if op == '||':
            return _lit('bool', a.value or b.value)
        if op in ('==', '!='):
            return _lit('bool', (a.value == b.value) if op == '==' else (a.value != b.value))
        return node

    x, y = _num(a), _num(b)
    if x is None or y is None:
        return node
    both_int = a.kind == 'int' and b.kind == 'int'

    if op in _CMP:
        return _lit('bool', _compare(op, x, y))

    if op in _BITS:
        if not both_int:
            return node
        if op in ('<<', '>>') and (y < 0 or y > 63):
            return node                       # leave the runtime to define it
        r = {'&': x & y, '|': x | y, '^': x ^ y,
             '<<': x << y if op == '<<' else 0, '>>': x >> y if op == '>>' else 0}[op]
        return _lit('int', r) if _I64_MIN <= r <= _I64_MAX else node

    if op not in _ARITH:
        return node

    # Division and modulo by zero TRAP at runtime in safe mode. Folding them
    # here would move a runtime error to compile time, or worse, remove it.
    if op in ('/', '%') and y == 0:
        return node

    if both_int:
        if op == '+':
            r = x + y
        elif op == '-':
            r = x - y
        elif op == '*':
            r = x * y
        elif op == '/':
            r = int(x / y) if (x < 0) != (y < 0) and x % y else x // y
        else:
            r = x - y * (int(x / y) if (x < 0) != (y < 0) and x % y else x // y)
        # Safe mode traps on overflow; a folded wrap-around would silently
        # produce a number the running program would have refused.
        return _lit('int', r) if _I64_MIN <= r <= _I64_MAX else node

    fx, fy = float(x), float(y)
    try:
        r = {'+': fx + fy, '-': fx - fy, '*': fx * fy,
             '/': fx / fy, '%': fx % fy}[op]
    except (ZeroDivisionError, ValueError, OverflowError):
        return node
    return _lit('float', r)


def _compare(op, x, y):
    return {'==': x == y, '!=': x != y, '<': x < y,
            '>': x > y, '<=': x <= y, '>=': x >= y}[op]


# ── the rewriter ─────────────────────────────────────────────

class _Rewriter:
    """Rebuilds a tree, substituting names and folding as it goes."""

    def __init__(self, subs: Dict[str, Node]):
        self.subs = subs
        self.hits = 0

    def expr(self, node):
        if node is None:
            return None
        if isinstance(node, Identifier):
            rep = self.subs.get(node.name)
            if rep is not None:
                self.hits += 1
                return _clone(rep)
            return node
        if isinstance(node, Literal):
            return node
        if isinstance(node, BinaryExpr):
            return fold_expr(BinaryExpr(node.op, self.expr(node.left),
                                        self.expr(node.right)))
        if isinstance(node, UnaryExpr):
            return fold_expr(UnaryExpr(node.op, self.expr(node.operand)))
        if isinstance(node, TernaryExpr):
            return TernaryExpr(self.expr(node.condition),
                               self.expr(node.then_value),
                               self.expr(node.else_value))
        if isinstance(node, CallExpr):
            return CallExpr(node.callee, [self.expr(a) for a in node.args],
                            line=node.line, type_args=node.type_args)
        if isinstance(node, MethodCallExpr):
            return MethodCallExpr(self.expr(node.obj), node.method,
                                  [self.expr(a) for a in node.args])
        if isinstance(node, CallValueExpr):
            return CallValueExpr(self.expr(node.callee),
                                 [self.expr(a) for a in node.args])
        if isinstance(node, FieldAccess):
            return FieldAccess(self.expr(node.obj), node.field)
        if isinstance(node, IndexAccess):
            return IndexAccess(self.expr(node.obj), self.expr(node.index))
        if isinstance(node, ArrayLiteral):
            return ArrayLiteral([self.expr(e) for e in node.elements])
        if isinstance(node, MapLiteral):
            return MapLiteral([(self.expr(k), self.expr(v))
                               for k, v in node.pairs])
        if isinstance(node, StructInit):
            return StructInit(node.struct_name,
                              [(f, self.expr(v)) for f, v in node.fields],
                              type_args=node.type_args)
        if isinstance(node, CastExpr):
            return CastExpr(self.expr(node.expr), node.target_type)
        if isinstance(node, UnwrapExpr):
            return UnwrapExpr(self.expr(node.operand))
        if isinstance(node, TryExpr):
            return TryExpr(self.expr(node.operand), node.line)
        if isinstance(node, SpawnExpr):
            return SpawnExpr(self.expr(node.expr))
        if isinstance(node, AwaitExpr):
            return AwaitExpr(self.expr(node.expr))
        if isinstance(node, Lambda):
            # A lambda's parameters shadow; substituting inside one is only
            # safe for names it does not rebind.
            inner = {k: v for k, v in self.subs.items()
                     if k not in {pn for _pt, pn in node.params}}
            sub = _Rewriter(inner)
            body = [sub.stmt(s) for s in node.body]
            self.hits += sub.hits
            return Lambda(node.params, node.return_type, body, line=node.line)
        return node

    def stmt(self, node):
        """_stmt_raw, with the source position carried across.

        See ast_nodes.carry_meta. Substitution rebuilds every statement it
        touches and none of the constructors below pass `line=`, so an
        optimized build reached the code generator with the positions stripped
        and produced a .pyro whose debug section — the one stack traces and
        line breakpoints read — was nearly empty.
        """
        out = self._stmt_raw(node)
        if out is not node and isinstance(node, Node) and isinstance(out, Node):
            carry_meta(node, out)
        return out

    def _stmt_raw(self, node):
        if node is None:
            return None
        if isinstance(node, VarDecl):
            return VarDecl(node.var_type, node.name, self.expr(node.value))
        if isinstance(node, ConstDecl):
            return ConstDecl(node.var_type, node.name, self.expr(node.value),
                             is_pub=node.is_pub)
        if isinstance(node, Assignment):
            return Assignment(node.name, self.expr(node.value))
        if isinstance(node, CompoundAssignment):
            return CompoundAssignment(node.op, node.name, self.expr(node.value))
        if isinstance(node, IndexAssignment):
            return IndexAssignment(self.expr(node.obj), self.expr(node.index),
                                   self.expr(node.value))
        if isinstance(node, Return):
            return Return(self.expr(node.value))
        if isinstance(node, If):
            return If(self.expr(node.condition),
                      [self.stmt(s) for s in node.then_body],
                      [self.stmt(s) for s in node.else_body]
                      if node.else_body else None)
        if isinstance(node, While):
            return While(self.expr(node.condition),
                         [self.stmt(s) for s in node.body])
        if isinstance(node, For):
            return For(self.stmt(node.init), self.expr(node.condition),
                       self.stmt(node.update), [self.stmt(s) for s in node.body])
        if isinstance(node, DoWhile):
            return DoWhile([self.stmt(s) for s in node.body],
                           self.expr(node.condition))
        if isinstance(node, ForEach):
            return ForEach(node.var_type, node.var_name,
                           self.expr(node.iterable),
                           [self.stmt(s) for s in node.body])
        if isinstance(node, Block):
            return Block([self.stmt(s) for s in node.body])
        if isinstance(node, TryCatch):
            return TryCatch([self.stmt(s) for s in node.try_body],
                            node.catch_type, node.catch_name,
                            [self.stmt(s) for s in node.catch_body]
                            if node.catch_body else None,
                            [self.stmt(s) for s in node.finally_body]
                            if node.finally_body else None)
        if isinstance(node, Switch):
            return Switch(self.expr(node.subject),
                          [SwitchCase([self.expr(v) for v in c.values],
                                      [self.stmt(s) for s in c.body])
                           for c in node.cases],
                          [self.stmt(s) for s in node.default_body]
                          if node.default_body else None)
        if isinstance(node, MatchStatement):
            return MatchStatement(self.expr(node.subject),
                                  [MatchCase(c.pattern_name, c.pattern_vars,
                                             [self.stmt(s) for s in c.body],
                                             c.line, c.guard)
                                   for c in node.cases], node.line)
        if isinstance(node, Assert):
            return Assert(self.expr(node.condition),
                          self.expr(node.message) if node.message else None,
                          node.line)
        if isinstance(node, SafetyBlock):
            return SafetyBlock(node.is_safe, [self.stmt(s) for s in node.body])
        # An expression used as a statement — `print(x);` is a bare CallExpr,
        # not a wrapper — still has an expression inside it to rewrite.
        return self.expr(node)


def _clone(node):
    """A fresh copy, so one substituted value is never shared between sites."""
    if isinstance(node, Literal):
        return Literal(node.kind, node.value)
    if isinstance(node, Identifier):
        return Identifier(node.name)
    return node


# ── analysis: which names are safe to substitute ─────────────

def _assigned_names(body) -> Set[str]:
    out = set()
    for n in _walk(body):
        if isinstance(n, (Assignment, CompoundAssignment)):
            out.add(n.name)
        elif isinstance(n, Increment):
            out.add(n.name)
        elif isinstance(n, ForEach):
            out.add(n.var_name)
        elif isinstance(n, MatchCase):
            out.update(n.pattern_vars)
        elif isinstance(n, TryCatch) and n.catch_name:
            out.add(n.catch_name)
        elif isinstance(n, Lambda):
            out.update(pn for _pt, pn in n.params)
    return out


def _declared_counts(body) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for n in _walk(body):
        if isinstance(n, (VarDecl, ConstDecl)):
            counts[n.name] = counts.get(n.name, 0) + 1
    return counts


def _read_counts(body) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for n in _walk(body):
        if isinstance(n, Identifier):
            counts[n.name] = counts.get(n.name, 0) + 1
    return counts


# A value may only be moved out of its declaration when it means the same
# thing without it. `int? y = 5` does not qualify: substituting the 5 turns
# `y == null` into `5 == null`, which the statically typed backends reject and
# which asks a different question everywhere else. Arrays, maps and structs
# carry their element types in the declaration too.
_SCALARS = {'int', 'number', 'string', 'bool'}


def _movable(var_type: Optional[str]) -> bool:
    return (var_type or '') in _SCALARS


def _optimize_body(body: List[Node], module_names: Set[str]) -> List[Node]:
    """Propagate, fold and prune inside one function body."""
    for _ in range(4):                      # each pass can expose the next
        assigned = _assigned_names(body)
        decls = _declared_counts(body)

        subs: Dict[str, Node] = {}
        for n in _walk(body):
            if not isinstance(n, (VarDecl, ConstDecl)):
                continue
            name = n.name
            # Declared exactly once, never assigned, not module state. Those
            # three together make substitution valid everywhere the name is
            # visible, without modelling scopes, loops or capture.
            if decls.get(name, 0) != 1 or name in assigned or name in module_names:
                continue
            if not _movable(n.var_type):
                continue
            v = n.value
            if isinstance(v, Literal):
                subs[name] = v                                  # constant
            elif (isinstance(v, Identifier) and v.name not in assigned
                  and v.name not in module_names
                  and decls.get(v.name, 0) == 1):
                subs[name] = v                                  # copy

        rw = _Rewriter(subs)
        new_body = [rw.stmt(s) for s in body]

        # A declaration that nobody reads and cannot be observed is gone. The
        # read count is taken AFTER substitution, so a variable that existed
        # only to be copied disappears with its copy.
        reads = _read_counts(new_body)
        pruned = _prune_dead(new_body, reads, module_names)

        if rw.hits == 0 and pruned == new_body:
            body = pruned
            break
        body = pruned
    return body


def _prune_dead(body, reads, module_names):
    out = []
    for s in body:
        if (isinstance(s, VarDecl) and s.name not in module_names
                and reads.get(s.name, 0) == 0
                and (s.value is None or _is_pure(s.value))):
            continue                                   # dead local
        out.append(_prune_inside(s, reads, module_names))
    return out


def _prune_inside(s, reads, module_names):
    """_prune_inside_raw, with the source position carried across.

    See ast_nodes.carry_meta — pruning a dead local out of a loop body must not
    also drop the loop's own line number.
    """
    out = _prune_inside_raw(s, reads, module_names)
    if out is not s and isinstance(s, Node) and isinstance(out, Node):
        carry_meta(s, out)
    return out


def _prune_inside_raw(s, reads, module_names):
    if isinstance(s, If):
        return If(s.condition, _prune_dead(s.then_body, reads, module_names),
                  _prune_dead(s.else_body, reads, module_names)
                  if s.else_body else None)
    if isinstance(s, While):
        return While(s.condition, _prune_dead(s.body, reads, module_names))
    if isinstance(s, For):
        return For(s.init, s.condition, s.update,
                   _prune_dead(s.body, reads, module_names))
    if isinstance(s, DoWhile):
        return DoWhile(_prune_dead(s.body, reads, module_names), s.condition)
    if isinstance(s, ForEach):
        return ForEach(s.var_type, s.var_name, s.iterable,
                       _prune_dead(s.body, reads, module_names))
    if isinstance(s, Block):
        return Block(_prune_dead(s.body, reads, module_names))
    return s


# ── 5. inlining small leaf functions ─────────────────────────

def _inlinable(fn: FunctionDecl) -> Optional[Node]:
    """The returned expression, if this function is worth inlining.

    Deliberately narrow: one `return <expr>`, no calls of its own (so it cannot
    recurse, directly or otherwise), and every parameter read at most once —
    which means substituting an argument can never duplicate work or reorder a
    side effect.
    """
    if getattr(fn, 'type_params', None) or getattr(fn, 'is_tool', False):
        return None
    # Only a scalar result. A function returning `int[]` carries that type in
    # its SIGNATURE; the expression `[1, 2, 3]` on its own does not, and the
    # statically typed backends infer `[]any` from it — so inlining
    # `fn mk() -> int[] = { return [1,2,3]; }` turned a working loop into a
    # type error. The AST has no types, so inlining is confined to the cases
    # where the expression means the same thing without the signature.
    if fn.return_type not in ('int', 'number', 'string', 'bool'):
        return None
    if len(fn.body) != 1 or not isinstance(fn.body[0], Return):
        return None
    expr = fn.body[0].value
    if expr is None or not _is_pure(expr):
        return None
    if any(not isinstance(n, (Literal, Identifier, BinaryExpr, UnaryExpr,
                              TernaryExpr))
           for n in _walk(expr)):
        return None
    if any(isinstance(n, Literal) and n.kind not in
           ('int', 'float', 'string', 'bool') for n in _walk(expr)):
        return None
    reads = _read_counts([expr])
    for _pt, pn in fn.params:
        if reads.get(pn, 0) > 1:
            return None
    if any(isinstance(n, Identifier) and n.name not in
           {pn for _pt, pn in fn.params}
           for n in _walk(expr) if isinstance(n, Identifier)):
        return None                    # reads something outside its parameters
    return expr


class _Inliner(_Rewriter):
    def __init__(self, table: Dict[str, Tuple[List[str], Node]]):
        super().__init__({})
        self.table = table

    def expr(self, node):
        node = super().expr(node)
        if isinstance(node, CallExpr) and node.callee in self.table:
            params, body = self.table[node.callee]
            if len(params) != len(node.args):
                return node
            # Only literal or identifier arguments: anything else could carry
            # a side effect whose position would move.
            if not all(isinstance(a, (Literal, Identifier)) for a in node.args):
                return node
            sub = _Rewriter(dict(zip(params, node.args)))
            self.hits += 1
            return fold_expr(sub.expr(_deep_clone(body)))
        return node


def _deep_clone(node):
    if isinstance(node, Literal):
        return Literal(node.kind, node.value)
    if isinstance(node, Identifier):
        return Identifier(node.name)
    if isinstance(node, BinaryExpr):
        return BinaryExpr(node.op, _deep_clone(node.left),
                          _deep_clone(node.right))
    if isinstance(node, UnaryExpr):
        return UnaryExpr(node.op, _deep_clone(node.operand))
    if isinstance(node, TernaryExpr):
        return TernaryExpr(_deep_clone(node.condition),
                           _deep_clone(node.then_value),
                           _deep_clone(node.else_value))
    if isinstance(node, IndexAccess):
        return IndexAccess(_deep_clone(node.obj), _deep_clone(node.index))
    if isinstance(node, FieldAccess):
        return FieldAccess(_deep_clone(node.obj), node.field)
    return node


# ── entry point ──────────────────────────────────────────────

def optimize(program: Program) -> Program:
    """Fold, propagate, prune and inline, to a fixed point.

    One pass is not enough, and that is the whole point of working across
    statements: folding `base * 60` turns `total` into a constant, which only
    the NEXT pass can propagate into `copy`, which the pass after that can
    prune. Three rounds settle every case the tests exercise; the loop stops as
    soon as a round changes nothing.
    """
    for _ in range(4):
        new = _optimize_once(program)
        if repr(new.statements) == repr(program.statements):
            return new
        program = new
    return program


def _optimize_once(program: Program) -> Program:
    module_names = {n.name for n in program.statements
                    if isinstance(n, (VarDecl, ConstDecl))}

    # A foreign block is opaque text that may name any variable in the
    # program. Nothing module-level can be proven unused with one present.
    has_foreign = any(isinstance(n, ForeignBlock) for n in _walk(program.statements))

    # A module-level declaration assigned nowhere in the whole program is a
    # constant, and 11.1 makes it visible to every function — so it can be
    # substituted everywhere, which is what makes a script (whose body is all
    # top level) benefit at all.
    global_subs: Dict[str, Node] = {}
    if not has_foreign:
        assigned_anywhere = _assigned_names(program.statements)
        decls = _declared_counts(program.statements)
        for n in program.statements:
            if (isinstance(n, (VarDecl, ConstDecl))
                    and isinstance(n.value, Literal)
                    and _movable(n.var_type)
                    and n.name not in assigned_anywhere
                    and decls.get(n.name, 0) == 1):
                global_subs[n.name] = n.value

    # Which small functions may be inlined.
    table: Dict[str, Tuple[List[str], Node]] = {}
    for n in program.statements:
        if isinstance(n, FunctionDecl) and n.name != 'main':
            body = _inlinable(n)
            if body is not None:
                table[n.name] = ([pn for _pt, pn in n.params], body)

    out: List[Node] = []
    for n in program.statements:
        if isinstance(n, FunctionDecl):
            body = n.body
            if table:
                inl = _Inliner({k: v for k, v in table.items()
                                if k != n.name})   # never into itself
                body = [inl.stmt(s) for s in body]
            if global_subs:
                # A parameter or local of the same name shadows the module one.
                local = _declared_counts(body) | {pn: 1 for _pt, pn in n.params}
                gs = {k: v for k, v in global_subs.items() if k not in local}
                body = [_Rewriter(gs).stmt(s) for s in body]
            body = _optimize_body(body, module_names)
            out.append(FunctionDecl(n.name, n.params, n.return_type, body,
                                    is_tool=n.is_tool, line=n.line,
                                    type_params=n.type_params,
                                    type_bounds=n.type_bounds,
                                    is_pub=n.is_pub))
        else:
            out.append(n)

    # Top-level statements form main's body and get the same treatment. They
    # are optimised as one sequence but written back where they were: the
    # order of declarations relative to them is part of the program.
    idx = [i for i, s in enumerate(out) if not isinstance(s, FunctionDecl)]
    if idx:
        stmts = [out[i] for i in idx]
        if table:
            inl = _Inliner(table)
            stmts = [inl.stmt(s) for s in stmts]
        if global_subs:
            stmts = [_Rewriter(global_subs).stmt(s) for s in stmts]
        stmts = _optimize_body(stmts, module_names)
        # A module constant nobody reads any more is gone with its readers.
        live = _read_counts(stmts) if not has_foreign else {'': 1}
        for fn in out:
            if isinstance(fn, FunctionDecl):
                for k, c in _read_counts(fn.body).items():
                    live[k] = live.get(k, 0) + c
        stmts = [s for s in stmts
                 if not (isinstance(s, (VarDecl, ConstDecl))
                         and s.name in global_subs
                         and live.get(s.name, 0) == 0)]
        # Pruning may drop statements, so the surviving ones refill the
        # original slots in order and any leftover slots are removed.
        rebuilt: List[Node] = []
        pending = list(stmts)
        taken = set(idx)
        for i, s in enumerate(out):
            if i not in taken:
                rebuilt.append(s)
            elif pending:
                rebuilt.append(pending.pop(0))
        rebuilt.extend(pending)
        out = rebuilt

    return Program(out)
