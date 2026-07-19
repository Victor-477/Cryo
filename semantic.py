# ============================================================
#  Cryo — Análise semântica prévia
#
#  Roda depois do parse + resolução de módulos e ANTES da geração
#  de código, capturando erros cedo e com linha, em vez de deixá-los
#  vazar tarde (e de forma diferente) em cada backend.
#
#  Verifica (independente de backend):
#   - uso de variável não declarada;
#   - chamada a função desconhecida (nem builtin, nem declarada);
#   - número de argumentos incompatível em função do usuário;
#   - `break`/`continue` fora de laço;
#   - declaração de topo duplicada (função/struct/enum).
#
#  Conservador por princípio: só acusa o que é inequívoco, para
#  nunca rejeitar um programa válido.
# ============================================================
from typing import Dict, List, Set, Tuple

from ast_nodes import (
    Program, Node, FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl,
    VarDecl, Assignment, IndexAssignment, CompoundAssignment, Increment,
    Return, If, While, For, DoWhile, ForEach, Switch, TryCatch, Break,
    Continue, Assert, SafetyBlock, ForeignBlock, Import, ModuleImport, Library,
    BinaryExpr, TernaryExpr, CastExpr, UnwrapExpr, SpawnExpr, AwaitExpr,
    MapLiteral, UnaryExpr, CallExpr, MethodCallExpr, FieldAccess, IndexAccess,
    ArrayLiteral, StructInit, Identifier, Literal,
)


class SemanticError(Exception):
    pass


# builtins conhecidos (superconjunto de todos os backends; a rejeição
# por backend específico continua acontecendo no codegen)
BUILTINS: Set[str] = {
    'print', 'input', 'input_int', 'input_num', 'len', 'assert',
    'to_string', 'to_int', 'to_number', 'throw',
    'sqrt', 'pow', 'abs', 'min', 'max', 'floor', 'ceil', 'round',
    'has', 'keys', 'remove',
    'upper', 'lower', 'trim', 'contains', 'find', 'replace', 'substr',
    'split', 'join',
    'json_encode', 'json_decode',
    'http_get', 'http_post', 'sleep',
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
        self.global_consts: Set[str] = set()
        self.type_names: Set[str] = set()        # struct/enum/schema (usáveis em schema_of etc.)
        self.loop_depth = 0

    def err(self, line: int, msg: str):
        loc = f"Linha {line}: " if line else ""
        self.errors.append(loc + msg)

    # ── coleta de declarações de topo ───────────────────────
    def collect(self):
        seen: Dict[str, str] = {}   # nome -> tipo de declaração
        for n in self.program.statements:
            name = kind = None
            if isinstance(n, FunctionDecl):
                name, kind = n.name, 'função'
                self.fn_arity[n.name] = len(n.params)
            elif isinstance(n, StructDecl):
                name, kind = n.name, 'struct'
                self.type_names.add(n.name)
            elif isinstance(n, EnumDecl):
                name, kind = n.name, 'enum'
                self.type_names.add(n.name)
                for m in n.members:
                    self.enum_members.add(f"{n.name}_{m}")
            elif isinstance(n, ConstDecl):
                self.global_consts.add(n.name)
            if name is not None:
                if name in seen:
                    self.err(getattr(n, 'line', 0),
                             f"declaração duplicada '{name}' "
                             f"({seen[name]} e {kind})")
                seen[name] = kind

    # ── varredura ───────────────────────────────────────────
    def run(self):
        self.collect()
        scope = _Scope()
        for n in self.program.statements:
            if isinstance(n, FunctionDecl):
                self.check_function(n)
            else:
                self.check_stmt(n, scope)
        if self.errors:
            raise SemanticError(
                "análise semântica encontrou "
                f"{len(self.errors)} problema(s):\n  - "
                + "\n  - ".join(self.errors))

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
                self.err(0, f"atribuição a variável não declarada '{n.name}'")
            self.check_expr(n.value, scope)
        elif isinstance(n, CompoundAssignment):
            if not self._known_var(n.name, scope):
                self.err(0, f"atribuição a variável não declarada '{n.name}'")
            self.check_expr(n.value, scope)
        elif isinstance(n, Increment):
            if not self._known_var(n.name, scope):
                self.err(0, f"'{n.op}' em variável não declarada '{n.name}'")
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
            self.loop_depth += 1   # break dentro de switch é válido
            for case in n.cases:
                for v in case.values:
                    self.check_expr(v, scope)
                self.check_block(case.body, scope)
            if n.default_body:
                self.check_block(n.default_body, scope)
            self.loop_depth -= 1
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
                self.err(0, "'break' fora de laço/switch")
        elif isinstance(n, Continue):
            if self.loop_depth == 0:
                self.err(0, "'continue' fora de laço")
        elif isinstance(n, Assert):
            self.check_expr(n.condition, scope)
            if n.message is not None:
                self.check_expr(n.message, scope)
        elif isinstance(n, SafetyBlock):
            self.check_block(n.body, scope)
        elif isinstance(n, (CallExpr, MethodCallExpr)):
            self.check_expr(n, scope)
        # Import/Library/ForeignBlock/decls aninhadas: sem checagem aqui

    def _known_var(self, name: str, scope: _Scope) -> bool:
        return (scope.has(name) or name in self.global_consts
                or name in self.enum_members or name in self.type_names)

    def check_expr(self, n: Node, scope: _Scope):
        if n is None:
            return
        if isinstance(n, Identifier):
            if not self._known_var(n.name, scope):
                self.err(n.line, f"variável não declarada '{n.name}'")
        elif isinstance(n, CallExpr):
            if n.callee not in BUILTINS:
                if n.callee not in self.fn_arity:
                    self.err(n.line, f"função desconhecida '{n.callee}'")
                elif len(n.args) != self.fn_arity[n.callee]:
                    self.err(n.line,
                             f"função '{n.callee}' espera "
                             f"{self.fn_arity[n.callee]} argumento(s), "
                             f"recebeu {len(n.args)}")
            for a in n.args:
                self.check_expr(a, scope)
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
        elif isinstance(n, (CastExpr, UnwrapExpr, SpawnExpr, AwaitExpr)):
            self.check_expr(getattr(n, 'expr', None) or n.operand, scope)
        elif isinstance(n, FieldAccess):
            self.check_expr(n.obj, scope)   # não validamos o nome do campo
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
        # Literal e demais: nada a checar


def check(program: Program) -> None:
    """Roda a análise semântica; levanta SemanticError se houver problemas."""
    _Checker(program).run()
