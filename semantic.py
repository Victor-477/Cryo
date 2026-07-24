# ============================================================
#  Cryo — Preliminary semantic analysis
#
#  Runs after parse + module resolution and BEFORE code generation,
#  catching errors early and with line, instead of letting them
#  leak late (and differently) in each backend.
#
#  Checks (backend independent):
#   - use of undeclared variable;
#   - call to unknown function (neither builtin nor declared);
#   - incompatible number of arguments in user function;
#   - `break`/`continue` outside of loop;
#   - duplicate top-level declaration (function/struct/enum).
#
#  Conservative by principle: only flags what is unequivocal, to
#  never reject a valid program.
# ============================================================
from typing import Dict, List, Set, Tuple

from ast_nodes import (
    Program, Node, FunctionDecl, StructDecl, EnumMember, EnumDecl, ConstDecl, SkillDecl,
    VarDecl, Assignment, IndexAssignment, CompoundAssignment, Increment,
    Return, If, While, For, DoWhile, ForEach, Switch, TryCatch, Break,
    Continue, Assert, SafetyBlock, ForeignBlock, Import, ModuleImport, Library,
    BinaryExpr, TernaryExpr, CastExpr, UnwrapExpr, TryExpr, SpawnExpr, AwaitExpr,
    MapLiteral, UnaryExpr, CallExpr, MethodCallExpr, FieldAccess, IndexAccess,
    ArrayLiteral, StructInit, Identifier, Literal, Lambda, MatchCase, MatchStatement,
)


class SemanticError(Exception):
    pass


# known builtins (superset of all backends; rejection
# by specific backend still happens in codegen)
BUILTINS: Set[str] = {
    'print', 'input', 'input_int', 'input_num', 'len', 'assert',
    'to_string', 'to_int', 'to_number', 'throw',
    'sqrt', 'pow', 'abs', 'min', 'max', 'floor', 'ceil', 'round',
    'clamp', 'sign', 'gcd', 'hypot',
    'has', 'keys', 'remove',
    'upper', 'lower', 'trim', 'contains', 'find', 'replace', 'substr',
    'split', 'join', 'starts_with', 'ends_with', 'repeat',
    'json_encode', 'json_decode',
    'http_get', 'http_post', 'sleep', 'write_bytes', 'read_file', 'args', 'http_serve',
    'schema_of', 'llm', 'tools', 'tools_json', 'tool_get', 'agent',
    'skills', 'skill_get', 'skill_has', 'skills_json',
    'pyro_exec', 'pyro_env', 'pyro_args', 'pyro_time', 'pyro_read',
    'pyro_write', 'pyro_write_file', 'pyro_open', 'pyro_exit',
}


class _Scope:
    def __init__(self):
        self.stack: List[Set[str]] = [set()]

    def push(self): self.stack.append(set())
    def pop(self):  self.stack.pop()
    def declare(self, name: str): self.stack[-1].add(name)
    def has(self, name: str) -> bool:
        return any(name in s for s in self.stack)


class _Checker:
    def __init__(self, program: Program):
        self.program = program
        self.errors: List[str] = []
        self.fn_arity: Dict[str, int] = {}
        self.enum_members: Set[str] = set()     # 'Nivel_ALTO'
        self.enum_defs: Dict[str, EnumDecl] = {}
        self.member_to_enum: Dict[str, str] = {}
        self.global_consts: Set[str] = set()
        self.type_names: Set[str] = set()        # struct/enum/schema (usable in schema_of etc.)
        self.loop_depth = 0

    def err(self, line: int, msg: str):
        self.errors.append((int(line or 0), msg))

    # ── top-level declaration collection ───────────────────────
    def collect(self):
        seen: Dict[str, str] = {}   # name -> declaration type
        for n in self.program.statements:
            name = kind = None
            if isinstance(n, FunctionDecl):
                name, kind = n.name, 'function'
                self.fn_arity[n.name] = len(n.params)
            elif isinstance(n, StructDecl):
                name, kind = n.name, 'struct'
                self.type_names.add(n.name)
            elif isinstance(n, EnumDecl):
                name, kind = n.name, 'enum'
                self.type_names.add(n.name)
                self.enum_defs[n.name] = n
                for m in n.members:
                    self.enum_members.add(f"{n.name}_{m.name}")
                    self.enum_members.add(m.name)
                    self.member_to_enum[m.name] = n.name
                    self.member_to_enum[f"{n.name}_{m.name}"] = n.name
                    if m.fields:
                        self.fn_arity[m.name] = len(m.fields)
                        self.fn_arity[f"{n.name}_{m.name}"] = len(m.fields)
            elif isinstance(n, ConstDecl):
                self.global_consts.add(n.name)
            if name is not None:
                if name in seen:
                    self.err(getattr(n, 'line', 0),
                             f"[Semantic Error] duplicate declaration '{name}' "
                             f"({seen[name]} and {kind})")
                seen[name] = kind

    # ── scanning ───────────────────────────────────────────
    def analyze(self):
        """Traverses the program and fills self.errors with (line, msg)."""
        self.collect()
        scope = _Scope()
        for n in self.program.statements:
            if isinstance(n, FunctionDecl):
                self.check_function(n)
            else:
                self.check_stmt(n, scope)
        return self.errors

    def run(self):
        self.analyze()
        if self.errors:
            def fmt(e):
                line, msg = e
                return (f"Line {line}: " if line else "") + msg
            raise SemanticError(
                "semantic analysis found "
                f"{len(self.errors)} problem(s):\n  - "
                + "\n  - ".join(fmt(e) for e in self.errors))

    def check_function(self, fn: FunctionDecl):
        scope = _Scope()
        for _pt, pn in fn.params:
            scope.declare(pn)
        self.check_block(fn.body, scope)

    def check_block(self, body: List[Node], scope: _Scope):
        scope.push()
        for s in body:
            self.check_stmt(s, scope)
        scope.pop()

    def check_stmt(self, n: Node, scope: _Scope):
        if isinstance(n, (VarDecl, ConstDecl)):
            if n.value is not None:
                self.check_expr(n.value, scope)
            scope.declare(n.name)
        elif isinstance(n, Assignment):
            if not self._known_var(n.name, scope):
                self.err(0, f"[Semantic Error] assignment to undeclared variable '{n.name}'")
            self.check_expr(n.value, scope)
        elif isinstance(n, CompoundAssignment):
            if not self._known_var(n.name, scope):
                self.err(0, f"[Semantic Error] assignment to undeclared variable '{n.name}'")
            self.check_expr(n.value, scope)
        elif isinstance(n, Increment):
            if not self._known_var(n.name, scope):
                self.err(0, f"'{n.op}' in undeclared variable '{n.name}'")
        elif isinstance(n, IndexAssignment):
            self.check_expr(n.obj, scope)
            self.check_expr(n.index, scope)
            self.check_expr(n.value, scope)
        elif isinstance(n, Return):
            if n.value is not None:
                self.check_expr(n.value, scope)
        elif isinstance(n, If):
            self.check_expr(n.condition, scope)
            self.check_block(n.then_body, scope)
            if n.else_body:
                self.check_block(n.else_body, scope)
        elif isinstance(n, While):
            self.check_expr(n.condition, scope)
            self.loop_depth += 1
            self.check_block(n.body, scope)
            self.loop_depth -= 1
        elif isinstance(n, DoWhile):
            self.loop_depth += 1
            self.check_block(n.body, scope)
            self.loop_depth -= 1
            self.check_expr(n.condition, scope)
        elif isinstance(n, For):
            scope.push()
            if n.init is not None:
                self.check_stmt(n.init, scope)
            if n.condition is not None:
                self.check_expr(n.condition, scope)
            if n.update is not None:
                self.check_stmt(n.update, scope)
            self.loop_depth += 1
            self.check_block(n.body, scope)
            self.loop_depth -= 1
            scope.pop()
        elif isinstance(n, ForEach):
            self.check_expr(n.iterable, scope)
            scope.push()
            scope.declare(n.var_name)
            self.loop_depth += 1
            self.check_block(n.body, scope)
            self.loop_depth -= 1
            scope.pop()
        elif isinstance(n, Switch):
            self.check_expr(n.subject, scope)
            self.loop_depth += 1   # break inside switch is valid
            for case in n.cases:
                for v in case.values:
                    self.check_expr(v, scope)
                self.check_block(case.body, scope)
            if n.default_body:
                self.check_block(n.default_body, scope)
            self.loop_depth -= 1
        elif isinstance(n, MatchStatement):
            self.check_expr(n.subject, scope)
            
            # Find thand enum being matched (if any)
            enum_name = None
            for case in n.cases:
                if case.pattern_name != '_':
                    enum_name = self.member_to_enum.get(case.pattern_name)
                    if enum_name:
                        break
            
            matched_variants = set()
            has_wildcard = False
            
            for case in n.cases:
                if case.pattern_name == '_':
                    has_wildcard = True
                    scope.push()
                    self.check_block(case.body, scope)
                    scope.pop()
                    continue
                
                # Check if it's a valid enum member
                if case.pattern_name not in self.member_to_enum:
                    self.err(case.line, f"pattern '{case.pattern_name}' is not a known enum member")
                    continue
                
                matched_variants.add(case.pattern_name)
                
                # Retrieve thand enum member definition to check fields
                enum_def = self.enum_defs.get(self.member_to_enum[case.pattern_name])
                member_def = None
                if enum_def:
                    for m in enum_def.members:
                        if m.name == case.pattern_name or f"{enum_def.name}_{m.name}" == case.pattern_name:
                            member_def = m
                            break
                
                if member_def:
                    expected_len = len(member_def.fields)
                    actual_len = len(case.pattern_vars)
                    if expected_len != actual_len:
                        self.err(case.line, f"pattern '{case.pattern_name}' expects {expected_len} variables, got {actual_len}")
                    
                    scope.push()
                    for i, var_name in enumerate(case.pattern_vars):
                        if i < len(member_def.fields):
                            scope.declare(var_name)
                    self.check_block(case.body, scope)
                    scope.pop()
                else:
                    scope.push()
                    self.check_block(case.body, scope)
                    scope.pop()
            
            # Exhaustiveness check
            if enum_name and not has_wildcard:
                enum_def = self.enum_defs.get(enum_name)
                if enum_def:
                    all_variants = {m.name for m in enum_def.members}
                    matched_short_names = set()
                    for v in matched_variants:
                        if '_' in v:
                            matched_short_names.add(v.split('_')[-1])
                        else:
                            matched_short_names.add(v)
                    missing = all_variants - matched_short_names
                    if missing:
                        self.err(n.line, f"[Semantic Error] non-exhaustive match for enum '{enum_name}'. Missing variants: {', '.join(missing)}")
        elif isinstance(n, TryCatch):
            self.check_block(n.try_body, scope)
            scope.push()
            if n.catch_name:
                scope.declare(n.catch_name)
            if n.catch_body:
                self.check_block(n.catch_body, scope)
            scope.pop()
            if n.finally_body:
                self.check_block(n.finally_body, scope)
        elif isinstance(n, Break):
            if self.loop_depth == 0:
                self.err(0, "'break' outside loop/switch")
        elif isinstance(n, Continue):
            if self.loop_depth == 0:
                self.err(0, "'continue' outside loop")
        elif isinstance(n, Assert):
            self.check_expr(n.condition, scope)
            if n.message is not None:
                self.check_expr(n.message, scope)
        elif isinstance(n, SafetyBlock):
            self.check_block(n.body, scope)
        elif isinstance(n, (CallExpr, MethodCallExpr)):
            self.check_expr(n, scope)
        # Import/Library/ForeignBlock/nested decls: no checking here

    def _known_var(self, name: str, scope: _Scope) -> bool:
        return (scope.has(name) or name in self.global_consts
                or name in self.enum_members or name in self.type_names
                or name in self.fn_arity)   # function name = 1st class value

    def check_expr(self, n: Node, scope: _Scope):
        if n is None:
            return
        if isinstance(n, Identifier):
            if not self._known_var(n.name, scope):
                self.err(n.line, f"[Semantic Error] undeclared variable '{n.name}'")
        elif isinstance(n, CallExpr):
            if n.callee in BUILTINS or scope.has(n.callee):
                pass   # builtin or indirect call via function type variable
            elif n.callee not in self.fn_arity:
                self.err(n.line, f"[Semantic Error] unknown function '{n.callee}'")
            elif len(n.args) != self.fn_arity[n.callee]:
                self.err(n.line,
                         f"function '{n.callee}' expects "
                         f"{self.fn_arity[n.callee]} argumento(s), "
                         f"recebeu {len(n.args)}")
            for a in n.args:
                self.check_expr(a, scope)
        elif isinstance(n, Lambda):
            scope.push()
            for _pt, pn in n.params:
                scope.declare(pn)
            self.check_block(n.body, scope)
            scope.pop()
        elif isinstance(n, MethodCallExpr):
            self.check_expr(n.obj, scope)
            for a in n.args:
                self.check_expr(a, scope)
        elif isinstance(n, BinaryExpr):
            self.check_expr(n.left, scope); self.check_expr(n.right, scope)
        elif isinstance(n, UnaryExpr):
            self.check_expr(n.operand, scope)
        elif isinstance(n, TernaryExpr):
            self.check_expr(n.condition, scope)
            self.check_expr(n.then_value, scope)
            self.check_expr(n.else_value, scope)
        elif isinstance(n, (CastExpr, UnwrapExpr, TryExpr, SpawnExpr, AwaitExpr)):
            self.check_expr(getattr(n, 'expr', None) or n.operand, scope)
        elif isinstance(n, FieldAccess):
            self.check_expr(n.obj, scope)   # we do not validate the field name
        elif isinstance(n, IndexAccess):
            self.check_expr(n.obj, scope); self.check_expr(n.index, scope)
        elif isinstance(n, ArrayLiteral):
            for e in n.elements:
                self.check_expr(e, scope)
        elif isinstance(n, MapLiteral):
            for k, v in n.pairs:
                self.check_expr(k, scope); self.check_expr(v, scope)
        elif isinstance(n, StructInit):
            for _f, v in n.fields:
                self.check_expr(v, scope)
        # Literal and others: nothing to check


def check(program: Program) -> None:
    """Runs semantic analysis; raises SemanticError if there are problems."""
    _Checker(program).run()


def findings(program: Program):
    """Returns the list of (line, message) without raising — used by LSP."""
    return _Checker(program).analyze()
