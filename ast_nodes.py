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

# -- declared types ------------------------------------

@dataclass
class StructField(Node):
    field_type: str
    name:       str

@dataclass
class StructDecl(Node):
    name:   str
    fields: List[StructField]
    line:   int = 0

@dataclass
class EnumMember:
    name:   str
    fields: List[str]  # List of associated types, e.g: ["int"], or empty
    line:   int = 0

@dataclass
class EnumDecl(Node):
    name:    str
    members: List[EnumMember]
    line:    int = 0

@dataclass
class MatchCase(Node):
    pattern_name: str         # ex: "Ok", "Err", "_"
    pattern_vars: List[str]   # ex: ["v"]
    body:         List[Node]  # Block instructions
    line:         int = 0

@dataclass
class MatchStatement(Node):
    subject: Node
    cases:   List[MatchCase]
    line:    int = 0

@dataclass
class SkillDecl(Node):
    """Native LLM skill: name + configuration (compiled in the binary).
    Replaces SKILL.md files with native and compact data."""
    name:   str
    fields: List[Tuple[str, 'Node']]   # key -> value (desc, model, tools, ...)

# -- variables / constants ------------------------------

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

# -- functions ---------------------------------------------

@dataclass
class FunctionDecl(Node):
    name:        str
    params:      List[Tuple[str, str]]
    return_type: Optional[str]
    body:        List[Node]
    is_tool:     bool = False   # 'tool fn' — exposed to LLMs (Phase 3)
    line:        int = 0

# -- flow control -----------------------------------

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
    values: List[Node]      # case values (>=1); empty = default
    body:   List[Node]

@dataclass
class Switch(Node):
    subject:      Node
    cases:        List[SwitchCase]
    default_body: Optional[List[Node]]

# -- security -------------------------------------------

@dataclass
class Assert(Node):
    condition: Node
    message:   Optional[Node]
    line:      int = 0

@dataclass
class SafetyBlock(Node):
    """'safe { }' or 'unsafe { }' block that controls instrumentation."""
    safe: bool
    body: List[Node]

# -- imports ---------------------------------------------

@dataclass
class Import(Node):
    lang: str

@dataclass
class ModuleImport(Node):
    """Import of another Cryo file: import "utils.cryo" (resolved by the compiler)."""
    path: str

@dataclass
class Library(Node):
    name: str
    lang: str = ""   # foreign language to which the library belongs (e.g.: "c", "go")

@dataclass
class ForeignBlock(Node):
    lang: str
    code: str

# -- expressions ------------------------------------------

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
    """Type conversion/assertion: 'expr as Type' (e.g.: json_decode(s) as T)."""
    expr:        Node
    target_type: str

@dataclass
class UnwrapExpr(Node):
    """Unwraps an optional: 'x!' (aborts if null)."""
    operand: Node

@dataclass
class TryExpr(Node):
    """Error propagation: 'expr?'. If expr is Ok(v)/non-null, it evaluates to v;
    if it is Err(e)/null, the function returns early with this error/null (Phase 8.3)."""
    operand: Node
    line:    int = 0

@dataclass
class SpawnExpr(Node):
    """Starts a concurrent task and returns a Future<T>: 'spawn expr'."""
    expr: Node

@dataclass
class AwaitExpr(Node):
    """Awaits the result of a Future<T>: 'await f'."""
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
    line:   int = 0

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
class Lambda(Node):
    params:      List[Tuple[str, str]]   # [(type, name), ...]
    return_type: Optional[str]           # None -> inferred by backend
    body:        List[Node]              # `=> expr` becomes [Return(expr)]
    line:        int = 0

@dataclass
class Identifier(Node):
    name: str
    line: int = 0

@dataclass
class Literal(Node):
    kind:  str
    value: Any
