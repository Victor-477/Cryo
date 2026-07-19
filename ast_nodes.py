# ============================================================
#  Cryo Compiler - AST Nodes  (v0.2)
# ============================================================
from dataclasses import dataclass, field
from typing import Optional, List, Any, Tuple


@dataclass
class Node:
    pass

@dataclass
class Program(Node):
    statements: List[Node]

# -- tipos declarados ------------------------------------

@dataclass
class StructField(Node):
    field_type: str
    name:       str

@dataclass
class StructDecl(Node):
    name:   str
    fields: List[StructField]

@dataclass
class EnumDecl(Node):
    name:    str
    members: List[str]

@dataclass
class SkillDecl(Node):
    """Skill nativa de LLM: nome + configuração (compilada no binário).
    Substitui arquivos SKILL.md por dados nativos e compactos."""
    name:   str
    fields: List[Tuple[str, 'Node']]   # chave -> valor (desc, model, tools, ...)

# -- variaveis / constantes ------------------------------

@dataclass
class VarDecl(Node):
    var_type: str
    name:     str
    value:    Optional[Node]

@dataclass
class ConstDecl(Node):
    var_type: str
    name:     str
    value:    Node

@dataclass
class Assignment(Node):
    name:  str
    value: Node

@dataclass
class IndexAssignment(Node):
    obj:   Node
    index: Node
    value: Node

@dataclass
class CompoundAssignment(Node):
    op:    str
    name:  str
    value: Node

@dataclass
class Increment(Node):
    op:   str
    name: str

# -- funcoes ---------------------------------------------

@dataclass
class FunctionDecl(Node):
    name:        str
    params:      List[Tuple[str, str]]
    return_type: Optional[str]
    body:        List[Node]
    is_tool:     bool = False   # 'tool fn' — exposta a LLMs (Fase 3)

# -- controle de fluxo -----------------------------------

@dataclass
class Return(Node):
    value: Optional[Node]

@dataclass
class If(Node):
    condition: Node
    then_body: List[Node]
    else_body: Optional[List[Node]]

@dataclass
class While(Node):
    condition: Node
    body:      List[Node]

@dataclass
class For(Node):
    init:      Optional[Node]
    condition: Optional[Node]
    update:    Optional[Node]
    body:      List[Node]

@dataclass
class DoWhile(Node):
    body:      List[Node]
    condition: Node

@dataclass
class ForEach(Node):
    var_type: str
    var_name: str
    iterable: Node
    body:     List[Node]

@dataclass
class TryCatch(Node):
    try_body:     List[Node]
    catch_type:   Optional[str]
    catch_name:   Optional[str]
    catch_body:   Optional[List[Node]]
    finally_body: Optional[List[Node]]

@dataclass
class Break(Node):
    pass

@dataclass
class Continue(Node):
    pass

@dataclass
class SwitchCase(Node):
    values: List[Node]      # valores 'case' (>=1); vazio = default
    body:   List[Node]

@dataclass
class Switch(Node):
    subject:      Node
    cases:        List[SwitchCase]
    default_body: Optional[List[Node]]

# -- seguranca -------------------------------------------

@dataclass
class Assert(Node):
    condition: Node
    message:   Optional[Node]
    line:      int = 0

@dataclass
class SafetyBlock(Node):
    """Bloco 'safe { }' ou 'unsafe { }' que controla a instrumentacao."""
    safe: bool
    body: List[Node]

# -- imports ---------------------------------------------

@dataclass
class Import(Node):
    lang: str

@dataclass
class ModuleImport(Node):
    """Import de outro arquivo Cryo: import "utils.cryo" (resolvido pelo compilador)."""
    path: str

@dataclass
class Library(Node):
    name: str
    lang: str = ""   # linguagem estrangeira à qual a library pertence (ex.: "c", "go")

@dataclass
class ForeignBlock(Node):
    lang: str
    code: str

# -- expressoes ------------------------------------------

@dataclass
class BinaryExpr(Node):
    op:    str
    left:  Node
    right: Node

@dataclass
class TernaryExpr(Node):
    condition:   Node
    then_value:  Node
    else_value:  Node

@dataclass
class CastExpr(Node):
    """Conversao/asserção de tipo: 'expr as Tipo' (ex.: json_decode(s) as T)."""
    expr:        Node
    target_type: str

@dataclass
class UnwrapExpr(Node):
    """Desempacota um opcional: 'x!' (aborta se nulo)."""
    operand: Node

@dataclass
class SpawnExpr(Node):
    """Inicia uma tarefa concorrente e retorna um Future<T>: 'spawn expr'."""
    expr: Node

@dataclass
class AwaitExpr(Node):
    """Aguarda o resultado de um Future<T>: 'await f'."""
    expr: Node

@dataclass
class MapLiteral(Node):
    pairs: List[Tuple[Node, Node]]

@dataclass
class UnaryExpr(Node):
    op:      str
    operand: Node

@dataclass
class CallExpr(Node):
    callee: str
    args:   List[Node]

@dataclass
class MethodCallExpr(Node):
    obj:    Node
    method: str
    args:   List[Node]

@dataclass
class FieldAccess(Node):
    obj:   Node
    field: str

@dataclass
class IndexAccess(Node):
    obj:   Node
    index: Node

@dataclass
class ArrayLiteral(Node):
    elements: List[Node]

@dataclass
class StructInit(Node):
    struct_name: str
    fields:      List[Tuple[str, Node]]

@dataclass
class Identifier(Node):
    name: str

@dataclass
class Literal(Node):
    kind:  str
    value: Any
