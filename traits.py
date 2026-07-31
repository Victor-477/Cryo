"""
Cryo Language - Traits & Interfaces Lowering Pass (Phase 10.7)
Lower structural contracts (trait/impl) into monomorphic, top-level
functions and direct call expressions before codegen.
"""
from typing import Dict, List, Optional, Set, Tuple
from ast_nodes import (
    Program, Node, FunctionDecl, StructDecl, TraitDecl, ImplDecl, TraitMethodSig,
    VarDecl, ConstDecl, Assignment, IndexAssignment, CompoundAssignment,
    Increment, Return, If, While, For, DoWhile, ForEach, Block,
    BinaryExpr, TernaryExpr, UnaryExpr,
    CallExpr, MethodCallExpr, FieldAccess, IndexAccess,
    ArrayLiteral, MapLiteral, StructInit, Lambda, Identifier, carry_meta
)


def lower_traits(program: Program) -> Program:
    traits: Dict[str, TraitDecl] = {}
    impls: Dict[Tuple[str, str], ImplDecl] = {}  # (trait_name, target_type) -> ImplDecl
    type_methods: Dict[Tuple[str, str], str] = {} # (target_type, method_name) -> mangled_fn_name
    
    non_trait_stmts: List[Node] = []
    generated_functions: List[FunctionDecl] = []
    
    # 1. Collect traits and impls
    for stmt in program.statements:
        if isinstance(stmt, TraitDecl):
            traits[stmt.name] = stmt
        elif isinstance(stmt, ImplDecl):
            if stmt.trait_name is not None:
                impls[(stmt.trait_name, stmt.target_type)] = stmt
            else:
                # Plain struct methods without a trait
                for method in stmt.methods:
                    mangled_name = f"{stmt.target_type}__{method.name}"
                    type_methods[(stmt.target_type, method.name)] = mangled_name
                    fn_params = [(stmt.target_type, "this")] + method.params
                    generated_fn = FunctionDecl(
                        name=mangled_name,
                        params=fn_params,
                        return_type=method.return_type,
                        body=method.body,
                        is_tool=method.is_tool,
                        line=method.line,
                        type_params=method.type_params,
                        type_bounds=method.type_bounds
                    )
                    generated_functions.append(generated_fn)
        else:
            non_trait_stmts.append(stmt)
            
    # 2. Validate impls and generate mangled functions
    for (trait_name, target_type), impl in impls.items():
        if trait_name not in traits:
            raise RuntimeError(f"[Traits Error] Line {impl.line}: Unknown trait '{trait_name}' in impl for '{target_type}'")
            
        trait = traits[trait_name]
        trait_methods = {m.name: m for m in trait.methods}
        impl_methods = {m.name: m for m in impl.methods}
        
        # Verify required methods
        for req_name, req_sig in trait_methods.items():
            if req_name not in impl_methods:
                raise RuntimeError(
                    f"[Traits Error] Line {impl.line}: Type '{target_type}' does not implement method '{req_name}' required by trait '{trait_name}'"
                )
            imp_sig = impl_methods[req_name]
            if len(imp_sig.params) != len(req_sig.params):
                raise RuntimeError(
                    f"[Traits Error] Line {imp_sig.line}: Method '{req_name}' in impl '{trait_name}' for '{target_type}' expects {len(req_sig.params)} parameters, got {len(imp_sig.params)}"
                )
                
        # Generate mangled top-level functions for each impl method
        for method in impl.methods:
            mangled_name = f"{target_type}__{method.name}"
            type_methods[(target_type, method.name)] = mangled_name
            
            # Method receiver parameter `this`
            fn_params = [(target_type, "this")] + method.params
            generated_fn = FunctionDecl(
                name=mangled_name,
                params=fn_params,
                return_type=method.return_type,
                body=method.body,
                is_tool=method.is_tool,
                line=method.line,
                type_params=method.type_params,
                type_bounds=method.type_bounds
            )
            generated_functions.append(generated_fn)

    # 3. The iteration protocol (roadmap 11.5).
    #
    # A type becomes iterable by implementing a trait method named `iter` that
    # takes no arguments and returns a collection. `for (x in obj)` then means
    # `for (x in obj.iter())`.
    #
    # The rewrite happens here rather than in the parser because it needs the
    # receiver's TYPE, and the parser has none. It is deliberately conservative:
    # only an identifier with a declared type, or a struct literal, is rewritten.
    # Anything else is left alone rather than guessed at — and a wrong guess
    # would surface as a compile error (calling iter() on an array), never as a
    # silently different loop.
    iterable_types = {t for (t, m) in type_methods if m == 'iter'}
    # name -> declared type, for the function currently being transformed.
    # Flat rather than scoped: a shadowed name could only mislead if the shadow
    # is also a struct implementing `iter`, and the result is a loud error.
    var_types: Dict[str, str] = {}

    def collect_var_types(stmts: List[Node], into: Dict[str, str]) -> None:
        for s in stmts:
            if isinstance(s, VarDecl) and s.var_type:
                into[s.name] = s.var_type
            elif isinstance(s, ConstDecl) and s.var_type:
                into[s.name] = s.var_type
            elif isinstance(s, If):
                collect_var_types(s.then_body, into)
                if s.else_body:
                    collect_var_types(s.else_body, into)
            elif isinstance(s, (While, Block)):
                collect_var_types(s.body, into)
            elif isinstance(s, For):
                if s.init is not None:
                    collect_var_types([s.init], into)
                collect_var_types(s.body, into)
            elif isinstance(s, ForEach):
                if s.var_type:
                    into[s.var_name] = s.var_type
                collect_var_types(s.body, into)
            elif isinstance(s, DoWhile):
                collect_var_types(s.body, into)

    def iterable_call(expr: Node) -> Optional[Node]:
        """`expr.iter()` if expr's type implements the protocol, else None."""
        target = None
        if isinstance(expr, Identifier):
            target = var_types.get(expr.name)
        elif isinstance(expr, StructInit):
            target = expr.struct_name
        if target and target in iterable_types:
            return CallExpr(type_methods[(target, 'iter')], [expr], type_args=[])
        return None

    # 4. Transform AST to rewrite trait method calls
    def transform_node(node: Node) -> Node:
        """_transform_raw, with the source position carried across.

        See ast_nodes.carry_meta: the rebuilds below pass `line=` on only a few
        node classes, which emptied the .pyro debug section that stack traces
        and line breakpoints read.
        """
        out = _transform_raw(node)
        if out is not node and isinstance(node, Node) and isinstance(out, Node):
            carry_meta(node, out)
        return out

    def _transform_raw(node: Node) -> Node:
        if node is None:
            return None

        if isinstance(node, VarDecl):
            return VarDecl(node.var_type, node.name, transform_node(node.value))
            
        if isinstance(node, ConstDecl):
            return ConstDecl(node.var_type, node.name, transform_node(node.value))

        if isinstance(node, FunctionDecl):
            # Declared types for this function: parameters, then its locals.
            # Saved and restored so a nested declaration cannot leak outward.
            nonlocal var_types
            saved = var_types
            var_types = dict(saved)
            for ptype, pname in node.params:
                var_types[pname] = ptype
            collect_var_types(node.body, var_types)
            new_body = [transform_node(s) for s in node.body]
            var_types = saved
            return FunctionDecl(node.name, node.params, node.return_type, new_body, is_tool=node.is_tool, line=node.line, type_params=node.type_params, type_bounds=node.type_bounds)

        if isinstance(node, MethodCallExpr):
            new_obj = transform_node(node.obj)
            new_args = [transform_node(a) for a in node.args]
            # Explicit Trait syntax: TraitName.method(instance, ...)
            if isinstance(new_obj, Identifier) and new_obj.name in traits:
                mname = node.method
                for (ttype, mn), mangled_fn in type_methods.items():
                    if mn == mname:
                        return CallExpr(mangled_fn, new_args, type_args=[])
            # Object method syntax: instance.method(...)
            for (ttype, mn), mangled_fn in type_methods.items():
                if node.method == mn:
                    return CallExpr(mangled_fn, [new_obj] + new_args, type_args=[])
            return MethodCallExpr(new_obj, node.method, new_args)

        if isinstance(node, CallExpr):
            new_args = [transform_node(a) for a in node.args]
            # Explicit Trait syntax: Printable.to_str(p)
            if '.' in node.callee:
                parts = node.callee.split('.', 1)
                tname, mname = parts[0], parts[1]
                if tname in traits and new_args:
                    for (ttype, mn), mangled_fn in type_methods.items():
                        if mn == mname:
                            return CallExpr(mangled_fn, new_args, line=node.line, type_args=node.type_args)
            return CallExpr(node.callee, new_args, line=node.line, type_args=node.type_args)

        if isinstance(node, StructInit):
            new_fields = [(fn, transform_node(fv)) for fn, fv in node.fields]
            return StructInit(node.struct_name, new_fields, type_args=node.type_args)

        if isinstance(node, Assignment):
            return Assignment(node.name, transform_node(node.value))

        if isinstance(node, CompoundAssignment):
            return CompoundAssignment(node.op, node.name, transform_node(node.value))

        if isinstance(node, IndexAssignment):
            return IndexAssignment(transform_node(node.obj), transform_node(node.index), transform_node(node.value))

        if isinstance(node, Return):
            return Return(transform_node(node.value))

        if isinstance(node, If):
            return If(transform_node(node.condition),
                      [transform_node(s) for s in node.then_body],
                      [transform_node(s) for s in node.else_body] if node.else_body else None)

        if isinstance(node, While):
            return While(transform_node(node.condition), [transform_node(s) for s in node.body])

        if isinstance(node, For):
            return For(transform_node(node.init), transform_node(node.condition), transform_node(node.update), [transform_node(s) for s in node.body])

        if isinstance(node, DoWhile):
            return DoWhile([transform_node(s) for s in node.body], transform_node(node.condition))

        if isinstance(node, ForEach):
            it = transform_node(node.iterable)
            it = iterable_call(it) or it          # 11.5 iteration protocol
            return ForEach(node.var_type, node.var_name, it,
                           [transform_node(s) for s in node.body])

        if isinstance(node, Block):
            return Block([transform_node(s) for s in node.body])

        if isinstance(node, BinaryExpr):
            return BinaryExpr(node.op, transform_node(node.left), transform_node(node.right))

        if isinstance(node, TernaryExpr):
            return TernaryExpr(transform_node(node.condition), transform_node(node.then_value), transform_node(node.else_value))

        if isinstance(node, UnaryExpr):
            return UnaryExpr(node.op, transform_node(node.operand))

        if isinstance(node, FieldAccess):
            return FieldAccess(transform_node(node.obj), node.field)

        if isinstance(node, IndexAccess):
            return IndexAccess(transform_node(node.obj), transform_node(node.index))

        if isinstance(node, ArrayLiteral):
            return ArrayLiteral([transform_node(e) for e in node.elements])

        if isinstance(node, MapLiteral):
            return MapLiteral([(transform_node(k), transform_node(v)) for k, v in node.pairs])

        if isinstance(node, Lambda):
            return Lambda(node.params, node.return_type, [transform_node(s) for s in node.body], line=node.line)

        return node

    # Module-level declarations are visible to top-level loops (11.1 made a
    # top-level `var` module state), so seed the map with them.
    collect_var_types(non_trait_stmts, var_types)
    transformed_stmts = [transform_node(stmt) for stmt in non_trait_stmts]
    final_statements = generated_functions + transformed_stmts
    return Program(final_statements)
