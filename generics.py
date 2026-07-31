"""
Cryo Language - Monomorphization Pass (Generics)
Lowers generic templates (FunctionDecl / StructDecl with type_params) and
their instantiations (CallExpr / StructInit / VarDecl with type_args)
into concrete, non-generic AST nodes before codegen.
"""
from typing import Dict, List, Set, Tuple
from ast_nodes import (
    Program, Node, FunctionDecl, StructDecl, StructField,
    VarDecl, ConstDecl, Assignment, IndexAssignment, CompoundAssignment,
    Increment, Return, If, While, For, DoWhile, ForEach, Block,
    BinaryExpr, TernaryExpr, UnaryExpr,
    CallExpr, MethodCallExpr, FieldAccess, IndexAccess,
    ArrayLiteral, MapLiteral, StructInit, Lambda, carry_meta
)


def _mangle_type(t: str) -> str:
    """Mangles type string for unique concrete names, e.g. 'int[]' -> 'arr_int'."""
    s = t.replace(' ', '')
    s = s.replace('[]', '_arr')
    s = s.replace('?', '_opt')
    s = s.replace('<', '_')
    s = s.replace('>', '_')
    s = s.replace(',', '_')
    s = s.replace('->', '_to_')
    s = s.replace('(', '_').replace(')', '_')
    return s.strip('_')


def _mangle_name(base: str, targs: List[str]) -> str:
    mangled_args = "_".join(_mangle_type(t) for t in targs)
    return f"{base}__{mangled_args}"


class _TypeSubst:
    def __init__(self, mapping: Dict[str, str]):
        self.mapping = mapping

    def subst_type(self, t: str) -> str:
        if not t:
            return t
        if t in self.mapping:
            return self.mapping[t]
        if t.endswith('[]'):
            base = self.subst_type(t[:-2])
            return f"{base}[]"
        if t.endswith('?'):
            base = self.subst_type(t[:-1])
            return f"{base}?"
        if t.startswith('future<') and t.endswith('>'):
            inner = t[7:-1]
            sub = self.subst_type(inner)
            return f"future<{sub}>"
        if t.startswith('map<') and t.endswith('>'):
            inner = t[4:-1]
            parts = [p.strip() for p in inner.split(',')]
            if len(parts) == 2:
                k = self.subst_type(parts[0])
                v = self.subst_type(parts[1])
                return f"map<{k},{v}>"
        if '<' in t and t.endswith('>'):
            idx = t.index('<')
            base_name = t[:idx]
            if base_name in ('future', 'map'):
                return t
            inner = t[idx+1:-1]
            parts = [self.subst_type(p.strip()) for p in inner.split(',')]
            return _mangle_name(base_name, parts)
        return t


def monomorphize(program: Program) -> Program:
    fn_templates: Dict[str, FunctionDecl] = {}
    struct_templates: Dict[str, StructDecl] = {}
    
    non_generic_stmts: List[Node] = []
    
    for stmt in program.statements:
        if isinstance(stmt, FunctionDecl) and stmt.type_params:
            fn_templates[stmt.name] = stmt
        elif isinstance(stmt, StructDecl) and stmt.type_params:
            struct_templates[stmt.name] = stmt
        else:
            non_generic_stmts.append(stmt)
            
    instantiated_fns: Dict[Tuple[str, Tuple[str, ...]], str] = {}
    instantiated_structs: Dict[Tuple[str, Tuple[str, ...]], str] = {}
    
    generated_fn_decls: List[FunctionDecl] = []
    generated_struct_decls: List[StructDecl] = []
    
    worklist_fns: Set[Tuple[str, Tuple[str, ...]]] = set()
    worklist_structs: Set[Tuple[str, Tuple[str, ...]]] = set()

    def request_fn_instantiation(name: str, targs: Tuple[str, ...]) -> str:
        if (name, targs) not in instantiated_fns:
            if name not in fn_templates:
                raise RuntimeError(f"[Generics Error] Unknown generic function template '{name}'")
            if len(targs) != len(fn_templates[name].type_params):
                raise RuntimeError(f"[Generics Error] Generic function '{name}' expects {len(fn_templates[name].type_params)} type arguments, got {len(targs)}")
            mangled = _mangle_name(name, list(targs))
            instantiated_fns[(name, targs)] = mangled
            worklist_fns.add((name, targs))
            return mangled
        return instantiated_fns[(name, targs)]

    def request_struct_instantiation(name: str, targs: Tuple[str, ...]) -> str:
        if (name, targs) not in instantiated_structs:
            if name not in struct_templates:
                raise RuntimeError(f"[Generics Error] Unknown generic struct template '{name}'")
            if len(targs) != len(struct_templates[name].type_params):
                raise RuntimeError(f"[Generics Error] Generic struct '{name}' expects {len(struct_templates[name].type_params)} type arguments, got {len(targs)}")
            mangled = _mangle_name(name, list(targs))
            instantiated_structs[(name, targs)] = mangled
            worklist_structs.add((name, targs))
            return mangled
        return instantiated_structs[(name, targs)]

    def transform_type_str(t: str, subst: _TypeSubst) -> str:
        t_sub = subst.subst_type(t)
        if '<' in t_sub and t_sub.endswith('>'):
            idx = t_sub.index('<')
            sname = t_sub[:idx]
            inner = t_sub[idx+1:-1]
            targs = tuple(p.strip() for p in inner.split(','))
            if sname in struct_templates:
                return request_struct_instantiation(sname, targs)
        return t_sub

    def transform_node(node: Node, subst: _TypeSubst) -> Node:
        """_transform_raw, with the source position carried across.

        The substitution below rebuilds nodes and passes `line=` on only a few
        of them, so every statement in a function body reached the code
        generator with no line and the .pyro debug section came out nearly
        empty. See ast_nodes.carry_meta. Doing it in one wrapper rather than at
        each of the ~25 constructor calls means a node type added later cannot
        reintroduce it.
        """
        out = _transform_raw(node, subst)
        if out is not node and isinstance(node, Node) and isinstance(out, Node):
            carry_meta(node, out)
        return out

    def _transform_raw(node: Node, subst: _TypeSubst) -> Node:
        if node is None:
            return None

        if isinstance(node, VarDecl):
            new_type = transform_type_str(node.var_type, subst)
            new_val = transform_node(node.value, subst)
            return VarDecl(new_type, node.name, new_val)
            
        if isinstance(node, ConstDecl):
            new_type = transform_type_str(node.var_type, subst)
            new_val = transform_node(node.value, subst)
            return ConstDecl(new_type, node.name, new_val)

        if isinstance(node, FunctionDecl):
            new_params = [(transform_type_str(pt, subst), pn) for pt, pn in node.params]
            new_ret = transform_type_str(node.return_type, subst) if node.return_type else None
            new_body = [transform_node(s, subst) for s in node.body]
            return FunctionDecl(node.name, new_params, new_ret, new_body, is_tool=node.is_tool, line=node.line, type_params=[])

        if isinstance(node, CallExpr):
            new_args = [transform_node(a, subst) for a in node.args]
            callee_name = node.callee
            if node.type_args:
                subst_targs = tuple(transform_type_str(ta, subst) for ta in node.type_args)
                callee_name = request_fn_instantiation(node.callee, subst_targs)
            return CallExpr(callee_name, new_args, line=node.line, type_args=[])

        if isinstance(node, StructInit):
            new_fields = [(fn, transform_node(fv, subst)) for fn, fv in node.fields]
            sname = node.struct_name
            if node.type_args:
                subst_targs = tuple(transform_type_str(ta, subst) for ta in node.type_args)
                sname = request_struct_instantiation(node.struct_name, subst_targs)
            return StructInit(sname, new_fields, type_args=[])

        if isinstance(node, Assignment):
            return Assignment(node.name, transform_node(node.value, subst))

        if isinstance(node, CompoundAssignment):
            return CompoundAssignment(node.op, node.name, transform_node(node.value, subst))

        if isinstance(node, Increment):
            return Increment(node.op, node.name)

        if isinstance(node, IndexAssignment):
            return IndexAssignment(transform_node(node.obj, subst), transform_node(node.index, subst), transform_node(node.value, subst))

        if isinstance(node, Return):
            return Return(transform_node(node.value, subst))

        if isinstance(node, If):
            return If(transform_node(node.condition, subst),
                      [transform_node(s, subst) for s in node.then_body],
                      [transform_node(s, subst) for s in node.else_body] if node.else_body else None)

        if isinstance(node, While):
            return While(transform_node(node.condition, subst), [transform_node(s, subst) for s in node.body])

        if isinstance(node, For):
            return For(transform_node(node.init, subst), transform_node(node.condition, subst), transform_node(node.update, subst), [transform_node(s, subst) for s in node.body])

        if isinstance(node, DoWhile):
            return DoWhile([transform_node(s, subst) for s in node.body], transform_node(node.condition, subst))

        if isinstance(node, ForEach):
            new_vt = transform_type_str(node.var_type, subst)
            return ForEach(new_vt, node.var_name, transform_node(node.iterable, subst), [transform_node(s, subst) for s in node.body])

        if isinstance(node, Block):
            return Block([transform_node(s, subst) for s in node.body])

        if isinstance(node, BinaryExpr):
            return BinaryExpr(node.op, transform_node(node.left, subst), transform_node(node.right, subst))

        if isinstance(node, TernaryExpr):
            return TernaryExpr(transform_node(node.condition, subst), transform_node(node.then_value, subst), transform_node(node.else_value, subst))

        if isinstance(node, UnaryExpr):
            return UnaryExpr(node.op, transform_node(node.operand, subst))

        if isinstance(node, MethodCallExpr):
            return MethodCallExpr(transform_node(node.obj, subst), node.method, [transform_node(a, subst) for a in node.args])

        if isinstance(node, FieldAccess):
            return FieldAccess(transform_node(node.obj, subst), node.field)

        if isinstance(node, IndexAccess):
            return IndexAccess(transform_node(node.obj, subst), transform_node(node.index, subst))

        if isinstance(node, ArrayLiteral):
            return ArrayLiteral([transform_node(e, subst) for e in node.elements])

        if isinstance(node, MapLiteral):
            return MapLiteral([(transform_node(k, subst), transform_node(v, subst)) for k, v in node.pairs])

        if isinstance(node, Lambda):
            new_params = [(transform_type_str(pt, subst), pn) for pt, pn in node.params]
            new_ret = transform_type_str(node.return_type, subst) if node.return_type else None
            return Lambda(new_params, new_ret, [transform_node(s, subst) for s in node.body], line=node.line)

        return node

    empty_subst = _TypeSubst({})
    transformed_stmts = [transform_node(stmt, empty_subst) for stmt in non_generic_stmts]
    
    depth = 0
    while (worklist_fns or worklist_structs):
        depth += 1
        if depth > 64:
            raise RuntimeError("[Generics Error] Exceeded maximum generic monomorphization depth")
        
        curr_structs = list(worklist_structs)
        worklist_structs.clear()
        for sname, targs in curr_structs:
            tmpl = struct_templates[sname]
            mapping = dict(zip(tmpl.type_params, targs))
            subst = _TypeSubst(mapping)
            mangled_name = instantiated_structs[(sname, targs)]
            new_fields = [StructField(transform_type_str(f.field_type, subst), f.name) for f in tmpl.fields]
            generated_struct_decls.append(StructDecl(mangled_name, new_fields, line=tmpl.line, type_params=[]))
            
        curr_fns = list(worklist_fns)
        worklist_fns.clear()
        for fname, targs in curr_fns:
            tmpl = fn_templates[fname]
            mapping = dict(zip(tmpl.type_params, targs))
            subst = _TypeSubst(mapping)
            mangled_name = instantiated_fns[(fname, targs)]
            new_params = [(transform_type_str(pt, subst), pn) for pt, pn in tmpl.params]
            new_ret = transform_type_str(tmpl.return_type, subst) if tmpl.return_type else None
            new_body = [transform_node(s, subst) for s in tmpl.body]
            generated_fn_decls.append(FunctionDecl(mangled_name, new_params, new_ret, new_body, is_tool=tmpl.is_tool, line=tmpl.line, type_params=[]))
            
    final_statements = generated_struct_decls + generated_fn_decls + transformed_stmts
    return Program(final_statements)
