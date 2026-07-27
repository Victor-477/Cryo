# ============================================================
#  Cryo Compiler - Parser  (v0.2)
# ============================================================

from typing import List, Optional, Tuple
from lexer import Token, TokenType, TYPE_TOKENS
from ast_nodes import (
    Node, Program,
    StructField, StructDecl, EnumMember, EnumDecl, SkillDecl,
    FunctionDecl, VarDecl, ConstDecl, Assignment,
    CompoundAssignment, Increment,
    Return, If, While, For, DoWhile, ForEach, TryCatch, Block,
    Break, Continue, Switch, SwitchCase, Assert, SafetyBlock,
    Import, ModuleImport, Library, ForeignBlock,
    Assignment, IndexAssignment,
    BinaryExpr, TernaryExpr, CastExpr, UnwrapExpr, TryExpr, UnaryExpr,
    SpawnExpr, AwaitExpr, CallExpr, CallValueExpr, MethodCallExpr,
    FieldAccess, IndexAccess, ArrayLiteral, MapLiteral, StructInit,
    Identifier, Literal, Lambda, MatchCase, MatchStatement,
)

COMPOUND_OPS = (
    TokenType.PLUS_ASSIGN, TokenType.MINUS_ASSIGN,
    TokenType.STAR_ASSIGN, TokenType.SLASH_ASSIGN,
    TokenType.PERCENT_ASSIGN, TokenType.AMP_ASSIGN,
    TokenType.PIPE_ASSIGN, TokenType.CARET_ASSIGN,
    TokenType.SHL_ASSIGN, TokenType.SHR_ASSIGN,
)

_BUILTIN_NAMES = {
    'print', 'len', 'has', 'keys', 'sort', 'reverse', 'slice', 'index_of',
    'map', 'filter', 'reduce', 'find', 'find_first', 'any', 'all',
    'clamp', 'sign', 'gcd', 'hypot', 'starts_with', 'ends_with', 'repeat',
    'pad_start', 'pad_end', 'concat', 'count', 'sum', 'enumerate', 'pairs',
    'now_ms', 'monotonic_ms', 'random', 'random_int', 'seed',
    'input', 'json_encode', 'json_decode', 'http_get', 'http_post', 'sleep',
    'write_bytes', 'read_file', 'args', 'http_serve', 'to_string', 'to_int', 'to_number',
    'true', 'false', 'null'
}


def _extract_identifiers(node, found):
    if node is None:
        return
    if isinstance(node, Identifier):
        found.add(node.name)
    elif isinstance(node, Literal):
        pass
    elif isinstance(node, BinaryExpr):
        _extract_identifiers(node.left, found)
        _extract_identifiers(node.right, found)
    elif isinstance(node, UnaryExpr):
        _extract_identifiers(node.expr, found)
    elif isinstance(node, (SpawnExpr, AwaitExpr, UnwrapExpr, TryExpr)):
        _extract_identifiers(node.expr, found)
    elif isinstance(node, CallExpr):
        if hasattr(node, 'callee') and isinstance(node.callee, Node):
            _extract_identifiers(node.callee, found)
        for a in node.args:
            _extract_identifiers(a, found)
    elif isinstance(node, MethodCallExpr):
        _extract_identifiers(node.obj, found)
        for a in node.args:
            _extract_identifiers(a, found)
    elif isinstance(node, FieldAccess):
        _extract_identifiers(node.obj, found)
    elif isinstance(node, IndexAccess):
        _extract_identifiers(node.obj, found)
        _extract_identifiers(node.index, found)
    elif isinstance(node, ArrayLiteral):
        for e in node.elements:
            _extract_identifiers(e, found)
    elif isinstance(node, MapLiteral):
        for k, v in node.pairs:
            _extract_identifiers(k, found)
            _extract_identifiers(v, found)
    elif isinstance(node, StructInit):
        for fname, fval in node.fields:
            _extract_identifiers(fval, found)


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos    = 0
        self.synthetic_fns = []
        self.user_defined_fns = set()
        self._gen_id_count = 0

    def _gen_id(self):
        self._gen_id_count += 1
        return self._gen_id_count

    def _cur(self):
        return self.tokens[self.pos]

    def _peek(self, offset=1):
        idx = self.pos + offset
        return self.tokens[min(idx, len(self.tokens) - 1)]

    def _advance(self):
        tok = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def _expect(self, *types):
        tok = self._cur()
        if tok.type not in types:
            names = [t.name for t in types]
            raise ParseError(
                f"[Syntax Error] Line {tok.line}: Expected {names}, "
                f"got {tok.type.name} ({tok.value!r})"
            )
        return self._advance()

    def _match(self, *types):
        return self._cur().type in types

    def _opt_semi(self):
        if self._match(TokenType.SEMICOLON):
            self._advance()

    def _parse_type(self):
        # map<K, V>
        if self._match(TokenType.MAP):
            self._advance()
            self._expect(TokenType.LT)
            k = self._parse_type()
            self._expect(TokenType.COMMA)
            v = self._parse_type()
            self._expect(TokenType.GT)
            base = f"map<{k},{v}>"
        # future<T>
        elif self._match(TokenType.FUTURE):
            self._advance()
            self._expect(TokenType.LT)
            t = self._parse_type()
            self._expect(TokenType.GT)
            base = f"future<{t}>"
        # function type: fn(T1, T2, ...) -> R   (R optional -> void)
        elif self._match(TokenType.FN):
            self._advance()
            self._expect(TokenType.LPAREN)
            ptypes = []
            while not self._match(TokenType.RPAREN):
                ptypes.append(self._parse_type())
                if self._match(TokenType.COMMA):
                    self._advance()
            self._expect(TokenType.RPAREN)
            ret = 'void'
            if self._match(TokenType.ARROW):
                self._advance()
                ret = self._parse_type()
            base = f"fn({','.join(ptypes)})->{ret}"
        else:
            valid = set(TYPE_TOKENS) | {TokenType.IDENT}
            tok = self._cur()
            if tok.type not in valid:
                raise ParseError(
                    f"[Syntax Error] Line {tok.line}: Expected type, got {tok.type.name} ({tok.value!r})"
                )
            base = self._advance().value
            if self._match(TokenType.LT):
                self._advance()
                targs = []
                while not self._match(TokenType.GT, TokenType.EOF):
                    targs.append(self._parse_type())
                    if self._match(TokenType.COMMA):
                        self._advance()
                self._expect(TokenType.GT)
                base = f"{base}<{','.join(targs)}>"
        # array suffix [] (applies to any base: primitive, map, future)
        while self._match(TokenType.LBRACKET) and self._peek().type == TokenType.RBRACKET:
            self._advance()
            self._expect(TokenType.RBRACKET)
            base = base + '[]'
        # optional: T?
        if self._match(TokenType.QUESTION):
            self._advance()
            base = base + '?'
        return base

    # ── program ────────────────────────────────────────────

    def parse(self):
        stmts = []
        while not self._match(TokenType.EOF):
            stmts.append(self._stmt())
        if self.synthetic_fns:
            stmts.extend(self.synthetic_fns)
        return Program(stmts)

    # ── statements ──────────────────────────────────────────

    def _stmt(self):
        tok = self._cur()
        _line = tok.line
        node = self._stmt_inner(tok)
        # annotate source line in the node (for debugging table)
        try:
            if getattr(node, 'line', 0) in (0, None):
                node.line = _line
        except Exception:
            pass
        return node

    def _stmt_inner(self, tok):
        if tok.type == TokenType.FN:
            # `fn name(...)` = declaration; `fn(...)->R var` = function type var
            if self._peek().type == TokenType.LPAREN:
                return self._var_decl()
            return self._fn()
        if tok.type == TokenType.TOOL:    return self._tool()
        if tok.type == TokenType.STRUCT:  return self._struct()
        if tok.type == TokenType.SCHEMA:  return self._struct()   # schema = struct
        if tok.type == TokenType.ENUM:    return self._enum()
        if tok.type == TokenType.SKILL:   return self._skill()
        if tok.type == TokenType.CONST:   return self._const()
        if tok.type == TokenType.IMPORT:  return self._import()
        if tok.type == TokenType.LIBRARY: return self._library()
        if tok.type == TokenType.RETURN:  return self._return()
        if tok.type == TokenType.IF:      return self._if()
        if tok.type == TokenType.WHILE:   return self._while()
        if tok.type == TokenType.DO:      return self._do_while()
        if tok.type == TokenType.FOR:     return self._for()
        if tok.type == TokenType.TRY:     return self._try()
        if tok.type == TokenType.SWITCH:  return self._switch()
        if tok.type == TokenType.MATCH:   return self._match_stmt()
        if tok.type == TokenType.ASSERT:  return self._assert()
        if tok.type in (TokenType.SAFE, TokenType.UNSAFE): return self._safety()
        if tok.type == TokenType.BREAK:
            self._advance(); self._opt_semi(); return Break()
        if tok.type == TokenType.CONTINUE:
            self._advance(); self._opt_semi(); return Continue()
        if tok.type == TokenType.LANG_BLOCK: return self._foreign()

        # primitive type, map or future -> var decl
        if tok.type in TYPE_TOKENS or tok.type in (TokenType.MAP, TokenType.FUTURE):
            return self._var_decl()

        # identifier -> multiple possibilities
        if tok.type == TokenType.IDENT:
            if self._is_generic_var_decl_ahead():
                return self._var_decl()
            nt = self._peek()
            # CustomType varName  or  CustomType[] varName  or  CustomType? varName
            if nt.type == TokenType.IDENT:
                return self._var_decl()
            if nt.type == TokenType.LBRACKET and self._peek(2).type == TokenType.RBRACKET:
                return self._var_decl()
            if nt.type == TokenType.QUESTION and self._peek(2).type == TokenType.IDENT:
                return self._var_decl()
            if nt.type == TokenType.ASSIGN:
                return self._assign()
            if nt.type in COMPOUND_OPS:
                return self._compound()
            if nt.type in (TokenType.PLUS_PLUS, TokenType.MINUS_MINUS):
                return self._increment()
            # expression (possible target of indexed assignment: m[k] = v)
            expr = self._postfix()
            if self._match(TokenType.ASSIGN) and isinstance(expr, IndexAccess):
                self._advance()
                val = self._expr()
                self._opt_semi()
                return IndexAssignment(expr.obj, expr.index, val)
            self._opt_semi()
            return expr

        raise ParseError(
            f"[Syntax Error] Line {tok.line}: Unexpected token {tok.type.name} ({tok.value!r})"
        )

    # ── struct ──────────────────────────────────────────────

    def _struct(self):
        sline = self._cur().line
        self._expect(TokenType.STRUCT, TokenType.SCHEMA)   # 'schema' = struct
        name = self._expect(TokenType.IDENT).value
        type_params = []
        if self._match(TokenType.LT):
            self._advance()
            while not self._match(TokenType.GT, TokenType.EOF):
                tp = self._expect(TokenType.IDENT).value
                type_params.append(tp)
                if self._match(TokenType.COMMA):
                    self._advance()
            self._expect(TokenType.GT)
        self._expect(TokenType.LBRACE)
        fields = []
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            ftype = self._parse_type()
            fname = self._expect(TokenType.IDENT).value
            self._opt_semi()
            fields.append(StructField(ftype, fname))
        self._expect(TokenType.RBRACE)
        return StructDecl(name, fields, line=sline, type_params=type_params)

    # ── skill (LLM nativa) ──────────────────────────────────

    def _skill(self):
        self._expect(TokenType.SKILL)
        name = self._expect(TokenType.IDENT).value
        self._expect(TokenType.LBRACE)
        fields = []
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            key = self._expect(TokenType.IDENT).value
            self._expect(TokenType.COLON)
            val = self._expr()
            fields.append((key, val))
            # optional separator
            if self._match(TokenType.SEMICOLON, TokenType.COMMA):
                self._advance()
        self._expect(TokenType.RBRACE)
        return SkillDecl(name, fields)

    # ── enum ────────────────────────────────────────────────

    def _enum(self):
        self._expect(TokenType.ENUM)
        name = self._expect(TokenType.IDENT).value
        self._expect(TokenType.LBRACE)
        members = []
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            mname_tok = self._expect(TokenType.IDENT)
            mname = mname_tok.value
            mline = mname_tok.line
            fields = []
            if self._match(TokenType.LPAREN):
                self._advance()
                while not self._match(TokenType.RPAREN, TokenType.EOF):
                    fields.append(self._parse_type())
                    if self._match(TokenType.COMMA):
                        self._advance()
                self._expect(TokenType.RPAREN)
            members.append(EnumMember(mname, fields, mline))
            if self._match(TokenType.COMMA):
                self._advance()
        self._expect(TokenType.RBRACE)
        return EnumDecl(name, members)

    # ── function ────────────────────────────────────────────

    def _tool(self):
        self._expect(TokenType.TOOL)      # 'tool fn ...' — exposed to LLMs
        return self._fn(is_tool=True)

    def _fn(self, is_tool=False):
        fn_line = self._cur().line
        self._expect(TokenType.FN)
        name = self._expect(TokenType.IDENT).value
        type_params = []
        if self._match(TokenType.LT):
            self._advance()
            while not self._match(TokenType.GT, TokenType.EOF):
                tp = self._expect(TokenType.IDENT).value
                type_params.append(tp)
                if self._match(TokenType.COMMA):
                    self._advance()
            self._expect(TokenType.GT)
        self.user_defined_fns.add(name)
        self._expect(TokenType.LPAREN)
        params = []
        while not self._match(TokenType.RPAREN):
            ptype = self._parse_type()
            pname = self._expect(TokenType.IDENT).value
            params.append((ptype, pname))
            if self._match(TokenType.COMMA):
                self._advance()
        self._expect(TokenType.RPAREN)
        ret = None
        if self._match(TokenType.ARROW):
            self._advance()
            ret = self._parse_type()
        self._expect(TokenType.BODY_ASSIGN)
        body = self._body()
        return FunctionDecl(name, params, ret, body, is_tool=is_tool, line=fn_line, type_params=type_params)

    def _body(self):
        stmts = []
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            stmts.append(self._stmt())
        self._expect(TokenType.RBRACE)
        return stmts

    def _block(self):
        self._expect(TokenType.LBRACE)
        return self._body()

    # ── const ───────────────────────────────────────────────

    def _const(self):
        self._expect(TokenType.CONST)
        vtype = self._parse_type()
        name  = self._expect(TokenType.IDENT).value
        self._expect(TokenType.ASSIGN)
        val   = self._expr()
        self._opt_semi()
        return ConstDecl(vtype, name, val)

    # ── import / library / foreign ──────────────────────────

    def _import(self):
        self._expect(TokenType.IMPORT)
        tok = self._cur()
        # import "file.cryo"  -> Cryo module (resolved by the compiler)
        if tok.type == TokenType.STR_LIT:
            self._advance()
            self._opt_semi()
            return ModuleImport(tok.value)
        # import >Lang<          -> enables foreign language
        tag = self._expect(TokenType.LANG_TAG)
        self._opt_semi()
        return Import(tag.value)

    def _library(self):
        self._expect(TokenType.LIBRARY)
        tag = self._expect(TokenType.LANG_TAG)
        self._opt_semi()
        # The library can be qualified by the foreign language:
        #   library >c math<   |   library >go:fmt<   |   library >math<
        raw = tag.value.strip()
        if ':' in raw:
            lang, _, name = raw.partition(':')
        elif raw.split()[1:]:                 # has space -> "lang name"
            parts = raw.split()
            lang, name = parts[0], ' '.join(parts[1:])
        else:
            lang, name = '', raw
        return Library(name=name.strip(), lang=lang.strip())

    def _foreign(self):
        tok = self._expect(TokenType.LANG_BLOCK)
        lang, _, code = tok.value.partition(':')
        return ForeignBlock(lang, code)

    # ── string interpolation: "total: ${x}" ──────────────

    def _string_literal(self, s: str, line: int):
        """String literal; with `${expr}` becomes concatenation with to_string(expr)."""
        if '${' not in s:
            return Literal('string', s)
        from lexer import Lexer as _Lexer   # local import (no cycle)
        parts = []
        i = 0
        while True:
            j = s.find('${', i)
            if j < 0:
                if i < len(s):
                    parts.append(Literal('string', s[i:]))
                break
            if j > i:
                parts.append(Literal('string', s[i:j]))
            # finds the matching '}' respecting nested braces
            depth, k = 1, j + 2
            while k < len(s) and depth:
                if s[k] == '{':
                    depth += 1
                elif s[k] == '}':
                    depth -= 1
                k += 1
            if depth:
                raise ParseError(
                    f"[Syntax Error] Line {line}: interpolation '${{' without closing '}}'")
            frag = s[j + 2:k - 1].strip()
            if not frag:
                raise ParseError(
                    f"[Syntax Error] Line {line}: empty interpolation '${{}}'")
            expr = Parser(_Lexer(frag).tokenize())._expr()
            parts.append(CallExpr('to_string', [expr]))
            i = k
        if not parts:
            return Literal('string', '')
        node = parts[0]
        if not isinstance(node, Literal):
            # ensures string context from the 1st term ("" + to_string(x))
            node = BinaryExpr('+', Literal('string', ''), node)
        for p in parts[1:]:
            node = BinaryExpr('+', node, p)
        return node

    # ── return ──────────────────────────────────────────────

    def _return(self):
        self._expect(TokenType.RETURN)
        if self._match(TokenType.SEMICOLON, TokenType.RBRACE):
            self._opt_semi()
            return Return(None)
        val = self._expr()
        self._opt_semi()
        return Return(val)

    # ── if ──────────────────────────────────────────────────

    def _if(self):
        self._expect(TokenType.IF)
        self._expect(TokenType.LPAREN)
        cond = self._expr()
        self._expect(TokenType.RPAREN)
        then = self._block()
        else_ = None
        if self._match(TokenType.ELSE):
            self._advance()
            else_ = [self._if()] if self._match(TokenType.IF) else self._block()
        return If(cond, then, else_)

    # ── while ───────────────────────────────────────────────

    def _while(self):
        self._expect(TokenType.WHILE)
        self._expect(TokenType.LPAREN)
        cond = self._expr()
        self._expect(TokenType.RPAREN)
        return While(cond, self._block())

    # ── do / while ──────────────────────────────────────────

    def _do_while(self):
        self._expect(TokenType.DO)
        body = self._block()
        self._expect(TokenType.WHILE)
        self._expect(TokenType.LPAREN)
        cond = self._expr()
        self._expect(TokenType.RPAREN)
        self._opt_semi()
        return DoWhile(body, cond)

    # ── for  (classic or for-each) ─────────────────────────

    def _is_foreach(self) -> bool:
        """Returns True if the current for(...) loop header is a for-each / iterator loop (contains 'in' before ';' or header-closing ')')."""
        save = self.pos
        depth = 0
        res = False
        while self.pos < len(self.tokens):
            t = self.tokens[self.pos].type
            if t == TokenType.LPAREN:
                depth += 1
            elif t == TokenType.RPAREN:
                depth -= 1
                if depth < 0:
                    break
            elif t == TokenType.SEMICOLON:
                if depth == 0:
                    break
            elif t == TokenType.IN:
                if depth <= 1:
                    res = True
                    break
            elif t == TokenType.EOF:
                break
            self.pos += 1
        self.pos = save
        return res

    def _parse_for_vars(self):
        """Parses loop variables before 'in':
        (int i) or (int i, string v) or (i, v) or ((i, v)) etc."""
        has_outer_paren = False
        if self._match(TokenType.LPAREN):
            self._advance()
            has_outer_paren = True

        vars_list = []
        while True:
            vtype = 'any'
            save = self.pos
            try:
                parsed_t = self._parse_type()
                if self._match(TokenType.IDENT):
                    vtype = parsed_t
                    vname = self._advance().value
                else:
                    self.pos = save
                    vname = self._expect(TokenType.IDENT).value
            except ParseError:
                self.pos = save
                vname = self._expect(TokenType.IDENT).value

            vars_list.append((vtype, vname))
            if self._match(TokenType.COMMA):
                self._advance()
            else:
                break

        if has_outer_paren and self._match(TokenType.RPAREN):
            self._advance()
        return vars_list

    def _desugar_for_vars(self, vars_list, iterable, body):
        if len(vars_list) == 1:
            vtype, vname = vars_list[0]
            return ForEach(vtype, vname, iterable, body)

        t1, n1 = vars_list[0]
        t2, n2 = vars_list[1]

        # enumerate(coll)
        if isinstance(iterable, CallExpr) and iterable.callee == 'enumerate' and len(iterable.args) == 1:
            coll = iterable.args[0]
            coll_var = f"__coll_{self._gen_id()}"
            idx_var  = f"__i_{self._gen_id()}"
            init_stmt = VarDecl('int', idx_var, Literal('int', 0))
            cond_expr = BinaryExpr('<', Identifier(idx_var), CallExpr('len', [Identifier(coll_var)]))
            upd_stmt  = Assignment(idx_var, BinaryExpr('+', Identifier(idx_var), Literal('int', 1)))
            v1_decl = VarDecl(t1 if t1 != 'any' else 'int', n1, Identifier(idx_var))
            v2_decl = VarDecl(t2, n2, IndexAccess(Identifier(coll_var), Identifier(idx_var)))
            loop_body = [v1_decl, v2_decl] + body
            for_loop  = For(init_stmt, cond_expr, upd_stmt, loop_body)
            return Block([VarDecl('any', coll_var, coll), for_loop])

        # pairs(m)
        if isinstance(iterable, CallExpr) and iterable.callee == 'pairs' and len(iterable.args) == 1:
            map_expr = iterable.args[0]
            map_var  = f"__map_{self._gen_id()}"
            keys_var = f"__keys_{self._gen_id()}"
            idx_var  = f"__i_{self._gen_id()}"
            init_stmt = VarDecl('int', idx_var, Literal('int', 0))
            cond_expr = BinaryExpr('<', Identifier(idx_var), CallExpr('len', [Identifier(keys_var)]))
            upd_stmt  = Assignment(idx_var, BinaryExpr('+', Identifier(idx_var), Literal('int', 1)))
            v1_decl = VarDecl(t1 if t1 != 'any' else 'string', n1, IndexAccess(Identifier(keys_var), Identifier(idx_var)))
            v2_decl = VarDecl(t2, n2, IndexAccess(Identifier(map_var), Identifier(n1)))
            loop_body = [v1_decl, v2_decl] + body
            for_loop  = For(init_stmt, cond_expr, upd_stmt, loop_body)
            return Block([
                VarDecl('any', map_var, map_expr),
                VarDecl('any', keys_var, CallExpr('keys', [Identifier(map_var)])),
                for_loop
            ])

        # generic tuple iteration
        coll_var = f"__coll_{self._gen_id()}"
        item_var = f"__item_{self._gen_id()}"
        decls = []
        for idx, (vt, vn) in enumerate(vars_list):
            decls.append(VarDecl(vt, vn, IndexAccess(Identifier(item_var), Literal('int', idx))))
        loop_body = decls + body
        foreach_loop = ForEach('any', item_var, Identifier(coll_var), loop_body)
        return Block([VarDecl('any', coll_var, iterable), foreach_loop])

    def _for(self):
        self._expect(TokenType.FOR)
        self._expect(TokenType.LPAREN)

        if self._is_foreach():
            vars_list = self._parse_for_vars()
            self._expect(TokenType.IN)
            iterable = self._expr()
            # range form:  for (int i in start .. end)  /  .. = (inclusive)
            if self._match(TokenType.RANGE, TokenType.RANGE_INCL):
                if len(vars_list) != 1:
                    raise ParseError(f"[Syntax Error] Line {self._cur().line}: range loop requires a single variable")
                vtype, vname = vars_list[0]
                inclusive = self._advance().type == TokenType.RANGE_INCL
                end = self._expr()
                self._expect(TokenType.RPAREN)
                body = self._block()
                return self._desugar_range(vtype, vname, iterable, end, inclusive, body)
            self._expect(TokenType.RPAREN)
            body = self._block()
            return self._desugar_for_vars(vars_list, iterable, body)

        init = None
        if not self._match(TokenType.SEMICOLON):
            if self._match(*TYPE_TOKENS):
                init = self._var_decl(semi=False)
            elif self._match(TokenType.IDENT) and self._peek().type in COMPOUND_OPS:
                init = self._compound(semi=False)
            elif self._match(TokenType.IDENT) and self._peek().type == TokenType.ASSIGN:
                init = self._assign(semi=False)
            else:
                init = self._expr()
        self._expect(TokenType.SEMICOLON)

        cond = None
        if not self._match(TokenType.SEMICOLON):
            cond = self._expr()
        self._expect(TokenType.SEMICOLON)

        update = None
        if not self._match(TokenType.RPAREN):
            if self._match(TokenType.IDENT) and self._peek().type in COMPOUND_OPS:
                update = self._compound(semi=False)
            elif self._match(TokenType.IDENT) and self._peek().type in (TokenType.PLUS_PLUS, TokenType.MINUS_MINUS):
                update = self._increment()
            elif self._match(TokenType.IDENT) and self._peek().type == TokenType.ASSIGN:
                update = self._assign(semi=False)
            else:
                update = self._expr()

        self._expect(TokenType.RPAREN)
        return For(init, cond, update, self._block())

    def _desugar_range(self, vtype, vname, start, end, inclusive, body):
        """Lower `for (int i in a .. b)` to the equivalent counted loop.

        Exclusive `..` uses `i < b`; inclusive `..=` uses `i <= b`. The bound is
        part of the loop condition (re-evaluated each iteration), exactly as if
        the programmer had written the classic `for (int i = a; i < b; ...)`.
        Range loops require an integer loop variable."""
        if vtype != 'int':
            raise ParseError(
                f"[Syntax Error] Line {self._cur().line}: range loop variable must be "
                f"'int', got '{vtype}'")
        op = '<=' if inclusive else '<'
        init   = VarDecl('int', vname, start)
        cond   = BinaryExpr(op, Identifier(vname), end)
        update = Assignment(vname, BinaryExpr('+', Identifier(vname), Literal('int', 1)))
        return For(init, cond, update, body)

    # ── try ─────────────────────────────────────────────────

    def _try(self):
        self._expect(TokenType.TRY)
        try_body = self._block()
        catch_type = catch_name = catch_body = finally_body = None
        if self._match(TokenType.CATCH):
            self._advance()
            self._expect(TokenType.LPAREN)
            catch_type = self._parse_type()
            catch_name = self._expect(TokenType.IDENT).value
            self._expect(TokenType.RPAREN)
            catch_body = self._block()
        if self._match(TokenType.FINALLY):
            self._advance()
            finally_body = self._block()
        return TryCatch(try_body, catch_type, catch_name, catch_body, finally_body)

    # ── switch ──────────────────────────────────────────────

    def _switch(self):
        self._expect(TokenType.SWITCH)
        self._expect(TokenType.LPAREN)
        subject = self._expr()
        self._expect(TokenType.RPAREN)
        self._expect(TokenType.LBRACE)
        cases = []
        default_body = None
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            if self._match(TokenType.CASE):
                values = []
                while self._match(TokenType.CASE):
                    self._advance()
                    values.append(self._expr())
                    self._expect(TokenType.COLON)
                body = self._case_body()
                cases.append(SwitchCase(values, body))
            elif self._match(TokenType.DEFAULT):
                self._advance()
                self._expect(TokenType.COLON)
                default_body = self._case_body()
            else:
                tok = self._cur()
                raise ParseError(
                    f"[Syntax Error] Line {tok.line}: Expected 'case' or 'default' in switch, "
                    f"got {tok.type.name} ({tok.value!r})"
                )
        self._expect(TokenType.RBRACE)
        return Switch(subject, cases, default_body)

    def _case_body(self):
        stmts = []
        while not self._match(TokenType.CASE, TokenType.DEFAULT,
                              TokenType.RBRACE, TokenType.EOF):
            stmts.append(self._stmt())
        return stmts

    # ── match ───────────────────────────────────────────────

    def _match_stmt(self):
        m_tok = self._expect(TokenType.MATCH)
        subject = self._expr()
        self._expect(TokenType.LBRACE)
        cases = []
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            pat_tok = self._expect(TokenType.IDENT)
            pat_name = pat_tok.value
            pat_vars = []
            if self._match(TokenType.LPAREN):
                self._advance()
                while not self._match(TokenType.RPAREN, TokenType.EOF):
                    pat_vars.append(self._expect(TokenType.IDENT).value)
                    if self._match(TokenType.COMMA):
                        self._advance()
                self._expect(TokenType.RPAREN)
            self._expect(TokenType.FAT_ARROW)
            if self._match(TokenType.LBRACE):
                self._advance()
                body = []
                while not self._match(TokenType.RBRACE, TokenType.EOF):
                    body.append(self._stmt())
                self._expect(TokenType.RBRACE)
            else:
                body = [self._stmt()]
            cases.append(MatchCase(pat_name, pat_vars, body, pat_tok.line))
        self._expect(TokenType.RBRACE)
        return MatchStatement(subject, cases, m_tok.line)

    # ── assert ──────────────────────────────────────────────

    def _assert(self):
        tok = self._expect(TokenType.ASSERT)
        self._expect(TokenType.LPAREN)
        cond = self._expr()
        msg = None
        if self._match(TokenType.COMMA):
            self._advance()
            msg = self._expr()
        self._expect(TokenType.RPAREN)
        self._opt_semi()
        return Assert(cond, msg, tok.line)

    # ── safe / unsafe ───────────────────────────────────────

    def _safety(self):
        is_safe = self._cur().type == TokenType.SAFE
        self._advance()
        body = self._block()
        return SafetyBlock(is_safe, body)

    # ── var decl / assign / compound / increment ────────────

    def _var_decl(self, semi=True):
        vtype = self._parse_type()
        name  = self._expect(TokenType.IDENT).value
        val   = None
        if self._match(TokenType.ASSIGN):
            self._advance()
            val = self._expr()
        if semi:
            self._opt_semi()
        return VarDecl(vtype, name, val)

    def _assign(self, semi=True):
        name = self._expect(TokenType.IDENT).value
        self._expect(TokenType.ASSIGN)
        val  = self._expr()
        if semi:
            self._opt_semi()
        return Assignment(name, val)

    def _compound(self, semi=True):
        name = self._expect(TokenType.IDENT).value
        op   = self._advance().value
        val  = self._expr()
        if semi:
            self._opt_semi()
        return CompoundAssignment(op, name, val)

    def _increment(self):
        name = self._expect(TokenType.IDENT).value
        op   = self._advance().value
        self._opt_semi()
        return Increment(op, name)

    # ── expressoes (precedencia crescente) ──────────────────

    def _expr(self):  return self._ternary()

    def _ternary(self):
        cond = self._cast()
        if self._match(TokenType.QUESTION):
            self._advance()
            then_v = self._ternary()
            self._expect(TokenType.COLON)
            else_v = self._ternary()
            return TernaryExpr(cond, then_v, else_v)
        return cond

    def _cast(self):
        expr = self._or()
        while self._match(TokenType.AS):
            self._advance()
            expr = CastExpr(expr, self._parse_type())
        return expr

    def _or(self):
        left = self._and()
        while self._match(TokenType.OR):
            left = BinaryExpr(self._advance().value, left, self._and())
        return left

    def _and(self):
        left = self._null_coal()
        while self._match(TokenType.AND):
            left = BinaryExpr(self._advance().value, left, self._null_coal())
        return left

    def _null_coal(self):
        left = self._bitor()
        while self._match(TokenType.NULL_COAL):
            left = BinaryExpr(self._advance().value, left, self._bitor())
        return left

    def _bitor(self):
        left = self._bitxor()
        while self._match(TokenType.PIPE):
            left = BinaryExpr(self._advance().value, left, self._bitxor())
        return left

    def _bitxor(self):
        left = self._bitand()
        while self._match(TokenType.CARET):
            left = BinaryExpr(self._advance().value, left, self._bitand())
        return left

    def _bitand(self):
        left = self._equality()
        while self._match(TokenType.AMP):
            left = BinaryExpr(self._advance().value, left, self._equality())
        return left

    def _equality(self):
        left = self._compare()
        while self._match(TokenType.EQ, TokenType.NEQ):
            left = BinaryExpr(self._advance().value, left, self._compare())
        return left

    def _compare(self):
        left = self._shift()
        while self._match(TokenType.LT, TokenType.GT, TokenType.LEQ, TokenType.GEQ):
            left = BinaryExpr(self._advance().value, left, self._shift())
        return left

    def _shift(self):
        left = self._add()
        while self._match(TokenType.SHL, TokenType.SHR):
            left = BinaryExpr(self._advance().value, left, self._add())
        return left

    def _add(self):
        left = self._mul()
        while self._match(TokenType.PLUS, TokenType.MINUS):
            left = BinaryExpr(self._advance().value, left, self._mul())
        return left

    def _mul(self):
        left = self._unary()
        while self._match(TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            left = BinaryExpr(self._advance().value, left, self._unary())
        return left

    def _unary(self):
        if self._match(TokenType.MINUS, TokenType.NOT, TokenType.TILDE):
            return UnaryExpr(self._advance().value, self._unary())
        if self._match(TokenType.SPAWN):
            self._advance(); return SpawnExpr(self._unary())
        if self._match(TokenType.AWAIT):
            self._advance(); return AwaitExpr(self._unary())
        return self._postfix()

    def _call_args(self):
        """Parse `expr, expr, ...)` after an already-consumed '(' — the ')' is
        consumed too. Arguments MUST be separated by commas: a missing comma
        used to be accepted silently, so `f(a)(b)` parsed as `f(a, b)` and
        `f(1 2)` as `f(1, 2)` — wrong code with no diagnostic."""
        args = []
        while not self._match(TokenType.RPAREN):
            args.append(self._expr())
            if self._match(TokenType.COMMA):
                self._advance()
            elif not self._match(TokenType.RPAREN):
                tok = self._cur()
                raise ParseError(
                    f"[Syntax Error] Line {tok.line}: expected ',' or ')' between call "
                    f"arguments, got {tok.type.name} ({tok.value!r})")
        self._expect(TokenType.RPAREN)
        return args

    def _postfix(self):
        expr = self._primary()
        while True:
            if self._match(TokenType.LBRACKET):
                self._advance()
                idx = self._expr()
                self._expect(TokenType.RBRACKET)
                expr = IndexAccess(expr, idx)
            elif self._match(TokenType.LPAREN):
                # calling the RESULT of an expression: `f(a)(b)`, `pick(x)(y)`.
                # `name(args)` is handled in _primary; this is the chained form.
                lp = self._advance()
                expr = CallValueExpr(expr, self._call_args(), line=lp.line)
            elif self._match(TokenType.DOT):
                self._advance()
                member = self._expect(TokenType.IDENT).value
                if self._match(TokenType.LPAREN):
                    self._advance()
                    expr = MethodCallExpr(expr, member, self._call_args())
                else:
                    expr = FieldAccess(expr, member)
            elif self._match(TokenType.NOT):
                # optional unwrapping: x!
                self._advance()
                expr = UnwrapExpr(expr)
            elif self._match(TokenType.QUESTION) and not self._starts_expr(self._peek(1)):
                # error propagation: expr?  (only when '?' DOES NOT open a
                # ternary — i.e., the next token does not start an expression)
                q = self._advance()
                expr = TryExpr(expr, q.line)
            else:
                break
        return expr

    # tokens that can start an expression ('then' branch of a ternary).
    # If the token after '?' is here, the '?' is ternary, not propagation.
    _EXPR_START = frozenset({
        TokenType.INT_LIT, TokenType.FLOAT_LIT, TokenType.STR_LIT,
        TokenType.BOOL_LIT, TokenType.NULL, TokenType.IDENT,
        TokenType.LPAREN, TokenType.LBRACKET, TokenType.LBRACE,
        TokenType.NEW, TokenType.MINUS, TokenType.NOT, TokenType.TILDE,
        TokenType.SPAWN, TokenType.AWAIT,
    })

    def _starts_expr(self, tok) -> bool:
        return tok.type in self._EXPR_START

    def _is_generic_var_decl_ahead(self):
        saved = self.pos
        try:
            if self._cur().type != TokenType.IDENT:
                return False
            self._advance()
            if not self._match(TokenType.LT):
                return False
            self._advance()
            while not self._match(TokenType.GT, TokenType.EOF):
                self._parse_type()
                if self._match(TokenType.COMMA):
                    self._advance()
            if not self._match(TokenType.GT):
                return False
            self._advance()
            while self._match(TokenType.LBRACKET) and self._peek().type == TokenType.RBRACKET:
                self._advance()
                self._advance()
            if self._match(TokenType.QUESTION):
                self._advance()
            return self._match(TokenType.IDENT)
        except Exception:
            return False
        finally:
            self.pos = saved

    def _type_args_ahead(self):
        saved = self.pos
        try:
            if not self._match(TokenType.LT):
                return False
            self._advance()
            while not self._match(TokenType.GT, TokenType.EOF):
                self._parse_type()
                if self._match(TokenType.COMMA):
                    self._advance()
            if not self._match(TokenType.GT):
                return False
            self._advance()
            return self._match(TokenType.LPAREN) or self._match(TokenType.LBRACE)
        except Exception:
            return False
        finally:
            self.pos = saved

    def _primary(self):
        tok = self._cur()

        if tok.type == TokenType.INT_LIT:
            self._advance(); return Literal('int', int(tok.value))
        if tok.type == TokenType.FLOAT_LIT:
            self._advance(); return Literal('float', float(tok.value))
        if tok.type == TokenType.STR_LIT:
            self._advance(); return self._string_literal(tok.value, tok.line)
        if tok.type == TokenType.BOOL_LIT:
            self._advance(); return Literal('bool', tok.value == 'true')
        if tok.type == TokenType.NULL:
            self._advance(); return Literal('null', None)

        # array literal or list comprehension
        if tok.type == TokenType.LBRACKET:
            self._advance()
            if self._match(TokenType.RBRACKET):
                self._advance()
                return ArrayLiteral([])
            first = self._expr()
            if self._match(TokenType.FOR):
                return self._parse_list_comprehension(first)
            elems = [first]
            if self._match(TokenType.COMMA):
                self._advance()
            while not self._match(TokenType.RBRACKET):
                elems.append(self._expr())
                if self._match(TokenType.COMMA):
                    self._advance()
            self._expect(TokenType.RBRACKET)
            return ArrayLiteral(elems)

        # map literal or map comprehension
        if tok.type == TokenType.LBRACE:
            self._advance()
            if self._match(TokenType.RBRACE):
                self._advance()
                return MapLiteral([])
            first_k = self._expr()
            self._expect(TokenType.COLON)
            first_v = self._expr()
            if self._match(TokenType.FOR):
                return self._parse_map_comprehension(first_k, first_v)
            pairs = [(first_k, first_v)]
            if self._match(TokenType.COMMA):
                self._advance()
            while not self._match(TokenType.RBRACE):
                k = self._expr()
                self._expect(TokenType.COLON)
                v = self._expr()
                pairs.append((k, v))
                if self._match(TokenType.COMMA):
                    self._advance()
            self._expect(TokenType.RBRACE)
            return MapLiteral(pairs)

        # new StructName { campo: val, ... }
        if tok.type == TokenType.NEW:
            self._advance()
            sname = self._expect(TokenType.IDENT).value
            type_args = []
            if self._match(TokenType.LT):
                self._advance()
                while not self._match(TokenType.GT, TokenType.EOF):
                    type_args.append(self._parse_type())
                    if self._match(TokenType.COMMA):
                        self._advance()
                self._expect(TokenType.GT)
            self._expect(TokenType.LBRACE)
            fields = []
            while not self._match(TokenType.RBRACE):
                fname = self._expect(TokenType.IDENT).value
                self._expect(TokenType.COLON)
                fval  = self._expr()
                fields.append((fname, fval))
                if self._match(TokenType.COMMA):
                    self._advance()
            self._expect(TokenType.RBRACE)
            return StructInit(sname, fields, type_args=type_args)

        # identifier or function call or map builtin
        if tok.type in (TokenType.IDENT, TokenType.MAP):
            id_line = tok.line
            name = self._advance().value
            type_args = []
            if self._type_args_ahead():
                self._expect(TokenType.LT)
                while not self._match(TokenType.GT, TokenType.EOF):
                    type_args.append(self._parse_type())
                    if self._match(TokenType.COMMA):
                        self._advance()
                self._expect(TokenType.GT)
                if self._match(TokenType.LBRACE):
                    self._advance()
                    fields = []
                    while not self._match(TokenType.RBRACE):
                        fname = self._expect(TokenType.IDENT).value
                        self._expect(TokenType.COLON)
                        fval  = self._expr()
                        fields.append((fname, fval))
                        if self._match(TokenType.COMMA):
                            self._advance()
                    self._expect(TokenType.RBRACE)
                    return StructInit(name, fields, type_args=type_args)
            if self._match(TokenType.LPAREN):
                self._advance()
                args = self._call_args()
                res = self._maybe_desugar_call(name, args, id_line)
                if type_args and isinstance(res, CallExpr):
                    res.type_args = type_args
                return res
            return Identifier(name, line=id_line)

        if tok.type == TokenType.LPAREN:
            # lambda: ( params ) => body   (otherwise, grouping)
            if self._lambda_ahead():
                return self._lambda()
            self._advance()
            expr = self._expr()
            self._expect(TokenType.RPAREN)
            return expr

        raise ParseError(
            f"[Syntax Error] Line {tok.line}: Unexpected token in expression: "
            f"{tok.type.name} ({tok.value!r})"
        )

    def _parse_list_comprehension(self, elem_expr):
        self._expect(TokenType.FOR)
        has_paren = False
        if self._match(TokenType.LPAREN):
            self._advance()
            has_paren = True

        vars_list = self._parse_for_vars()
        self._expect(TokenType.IN)
        iterable = self._expr()

        is_range = False
        inclusive = False
        range_end = None
        if self._match(TokenType.RANGE, TokenType.RANGE_INCL):
            is_range = True
            inclusive = self._advance().type == TokenType.RANGE_INCL
            range_end = self._expr()

        if has_paren:
            self._expect(TokenType.RPAREN)

        cond = None
        if self._match(TokenType.IF):
            self._advance()
            cond = self._expr()

        self._expect(TokenType.RBRACKET)

        push_stmt = MethodCallExpr(Identifier("__res"), "push", [elem_expr])
        body = [push_stmt]
        if cond:
            body = [If(cond, body, None)]

        if is_range:
            vtype, vname = vars_list[0]
            for_loop = self._desugar_range(vtype, vname, iterable, range_end, inclusive, body)
        else:
            for_loop = self._desugar_for_vars(vars_list, iterable, body)

        found = set()
        _extract_identifiers(elem_expr, found)
        _extract_identifiers(iterable, found)
        if range_end:
            _extract_identifiers(range_end, found)
        if cond:
            _extract_identifiers(cond, found)

        loop_vars = {vn for _vt, vn in vars_list}
        captured = sorted(found - loop_vars - _BUILTIN_NAMES)

        fn_name = f"__list_comp_{self._gen_id()}"
        params = [("any", name) for name in captured]
        init_res = VarDecl('any[]', '__res', ArrayLiteral([]))
        return_stmt = Return(Identifier("__res"))
        if isinstance(for_loop, Block):
            fn_body = [init_res] + for_loop.body + [return_stmt]
        else:
            fn_body = [init_res, for_loop, return_stmt]

        fn_decl = FunctionDecl(fn_name, params, "any[]", fn_body)
        self.synthetic_fns.append(fn_decl)

        return CallExpr(fn_name, [Identifier(name) for name in captured])

    def _parse_map_comprehension(self, k_expr, v_expr):
        self._expect(TokenType.FOR)
        has_paren = False
        if self._match(TokenType.LPAREN):
            self._advance()
            has_paren = True

        vars_list = self._parse_for_vars()
        self._expect(TokenType.IN)
        iterable = self._expr()

        is_range = False
        inclusive = False
        range_end = None
        if self._match(TokenType.RANGE, TokenType.RANGE_INCL):
            is_range = True
            inclusive = self._advance().type == TokenType.RANGE_INCL
            range_end = self._expr()

        if has_paren:
            self._expect(TokenType.RPAREN)

        cond = None
        if self._match(TokenType.IF):
            self._advance()
            cond = self._expr()

        self._expect(TokenType.RBRACE)

        set_stmt = IndexAssignment(Identifier("__res"), k_expr, v_expr)
        body = [set_stmt]
        if cond:
            body = [If(cond, body, None)]

        if is_range:
            vtype, vname = vars_list[0]
            for_loop = self._desugar_range(vtype, vname, iterable, range_end, inclusive, body)
        else:
            for_loop = self._desugar_for_vars(vars_list, iterable, body)

        found = set()
        _extract_identifiers(k_expr, found)
        _extract_identifiers(v_expr, found)
        _extract_identifiers(iterable, found)
        if range_end:
            _extract_identifiers(range_end, found)
        if cond:
            _extract_identifiers(cond, found)

        loop_vars = {vn for _vt, vn in vars_list}
        captured = sorted(found - loop_vars - _BUILTIN_NAMES)

        fn_name = f"__map_comp_{self._gen_id()}"
        params = [("any", name) for name in captured]
        init_res = VarDecl('map<any,any>', '__res', MapLiteral([]))
        return_stmt = Return(Identifier("__res"))
        if isinstance(for_loop, Block):
            fn_body = [init_res] + for_loop.body + [return_stmt]
        else:
            fn_body = [init_res, for_loop, return_stmt]

        fn_decl = FunctionDecl(fn_name, params, "map<any,any>", fn_body)
        self.synthetic_fns.append(fn_decl)

        return CallExpr(fn_name, [Identifier(name) for name in captured])

    def _lambda_ahead(self) -> bool:
        """True if the current '(' opens a lambda parameter list —
        i.e. the corresponding ')' is followed by '=>'."""
        depth = 0
        i = self.pos
        n = len(self.tokens)
        while i < n:
            t = self.tokens[i].type
            if t == TokenType.LPAREN:
                depth += 1
            elif t == TokenType.RPAREN:
                depth -= 1
                if depth == 0:
                    nxt = self.tokens[i + 1].type if i + 1 < n else TokenType.EOF
                    return nxt == TokenType.FAT_ARROW
            elif t == TokenType.EOF:
                break
            i += 1
        return False

    def _lambda(self):
        line = self._cur().line
        self._expect(TokenType.LPAREN)
        params = []
        while not self._match(TokenType.RPAREN):
            ptype = self._parse_type()
            pname = self._expect(TokenType.IDENT).value
            params.append((ptype, pname))
            if self._match(TokenType.COMMA):
                self._advance()
        self._expect(TokenType.RPAREN)
        self._expect(TokenType.FAT_ARROW)
        # body: `=> { stmts }` (block) or `=> expr` (implicit return)
        if self._match(TokenType.LBRACE):
            body = self._block()
        else:
            body = [Return(self._expr())]
        return Lambda(params, None, body, line=line)

    def _maybe_desugar_call(self, name, args, id_line):
        if name not in self.user_defined_fns:
            if name == 'map' and len(args) == 2:
                return self._desugar_map(args[0], args[1])
            if name == 'filter' and len(args) == 2:
                return self._desugar_filter(args[0], args[1])
            if name == 'reduce' and len(args) == 3:
                return self._desugar_reduce(args[0], args[1], args[2])
            if name == 'find_first' and len(args) == 2:
                return self._desugar_find_first(args[0], args[1])
            if name == 'any' and len(args) == 2:
                return self._desugar_any(args[0], args[1])
            if name == 'all' and len(args) == 2:
                return self._desugar_all(args[0], args[1])
        return CallExpr(name, args, line=id_line)

    def _extract_fn_info(self, f, len_params=1):
        if isinstance(f, Lambda) and len(f.params) == len_params:
            if len_params == 1:
                elem_type = f.params[0][0] or 'any'
                return elem_type, f"{elem_type}[]"
            elif len_params == 2:
                acc_type = f.params[0][0] or 'any'
                elem_type = f.params[1][0] or 'any'
                return acc_type, elem_type, f"{elem_type}[]"
        if len_params == 1:
            return 'any', 'any[]'
        else:
            return 'any', 'any', 'any[]'

    def _desugar_map(self, arr, f):
        elem_t, arr_t = self._extract_fn_info(f, 1)
        fn_name = f"__map_{self._gen_id()}"
        init_res = VarDecl(arr_t, '__res', ArrayLiteral([]))
        loop_var = f"__x_{self._gen_id()}"
        push_stmt = MethodCallExpr(Identifier("__res"), "push", [CallValueExpr(Identifier("f"), [Identifier(loop_var)])])
        for_loop = ForEach(elem_t, loop_var, Identifier("arr"), [push_stmt])
        return_stmt = Return(Identifier("__res"))
        fn_decl = FunctionDecl(fn_name, [(arr_t, "arr"), (f"fn({elem_t})->{elem_t}", "f")], arr_t, [init_res, for_loop, return_stmt])
        self.synthetic_fns.append(fn_decl)
        return CallExpr(fn_name, [arr, f])

    def _desugar_filter(self, arr, f):
        elem_t, arr_t = self._extract_fn_info(f, 1)
        fn_name = f"__filter_{self._gen_id()}"
        init_res = VarDecl(arr_t, '__res', ArrayLiteral([]))
        loop_var = f"__x_{self._gen_id()}"
        cond_call = CallValueExpr(Identifier("f"), [Identifier(loop_var)])
        if_stmt = If(cond_call, [MethodCallExpr(Identifier("__res"), "push", [Identifier(loop_var)])], None)
        for_loop = ForEach(elem_t, loop_var, Identifier("arr"), [if_stmt])
        return_stmt = Return(Identifier("__res"))
        fn_decl = FunctionDecl(fn_name, [(arr_t, "arr"), (f"fn({elem_t})->bool", "f")], arr_t, [init_res, for_loop, return_stmt])
        self.synthetic_fns.append(fn_decl)
        return CallExpr(fn_name, [arr, f])

    def _desugar_reduce(self, arr, f, init):
        acc_t, elem_t, arr_t = self._extract_fn_info(f, 2)
        fn_name = f"__reduce_{self._gen_id()}"
        init_acc = VarDecl(acc_t, '__acc', Identifier("init"))
        loop_var = f"__x_{self._gen_id()}"
        call_f = CallValueExpr(Identifier("f"), [Identifier("__acc"), Identifier(loop_var)])
        assign_acc = Assignment("__acc", call_f)
        for_loop = ForEach(elem_t, loop_var, Identifier("arr"), [assign_acc])
        return_stmt = Return(Identifier("__acc"))
        fn_decl = FunctionDecl(fn_name, [(arr_t, "arr"), (f"fn({acc_t},{elem_t})->{acc_t}", "f"), (acc_t, "init")], acc_t, [init_acc, for_loop, return_stmt])
        self.synthetic_fns.append(fn_decl)
        return CallExpr(fn_name, [arr, f, init])

    def _desugar_find_first(self, arr, f):
        elem_t, arr_t = self._extract_fn_info(f, 1)
        fn_name = f"__find_first_{self._gen_id()}"
        loop_var = f"__x_{self._gen_id()}"
        cond_call = CallValueExpr(Identifier("f"), [Identifier(loop_var)])
        if_stmt = If(cond_call, [Return(Identifier(loop_var))], None)
        for_loop = ForEach(elem_t, loop_var, Identifier("arr"), [if_stmt])
        return_stmt = Return(Literal('null', None))
        fn_decl = FunctionDecl(fn_name, [(arr_t, "arr"), (f"fn({elem_t})->bool", "f")], "any", [for_loop, return_stmt])
        self.synthetic_fns.append(fn_decl)
        return CallExpr(fn_name, [arr, f])

    def _desugar_any(self, arr, f):
        elem_t, arr_t = self._extract_fn_info(f, 1)
        fn_name = f"__any_{self._gen_id()}"
        loop_var = f"__x_{self._gen_id()}"
        cond_call = CallValueExpr(Identifier("f"), [Identifier(loop_var)])
        if_stmt = If(cond_call, [Return(Literal('bool', True))], None)
        for_loop = ForEach(elem_t, loop_var, Identifier("arr"), [if_stmt])
        return_stmt = Return(Literal('bool', False))
        fn_decl = FunctionDecl(fn_name, [(arr_t, "arr"), (f"fn({elem_t})->bool", "f")], "bool", [for_loop, return_stmt])
        self.synthetic_fns.append(fn_decl)
        return CallExpr(fn_name, [arr, f])

    def _desugar_all(self, arr, f):
        elem_t, arr_t = self._extract_fn_info(f, 1)
        fn_name = f"__all_{self._gen_id()}"
        loop_var = f"__x_{self._gen_id()}"
        cond_call = CallValueExpr(Identifier("f"), [Identifier(loop_var)])
        not_cond = UnaryExpr("!", cond_call)
        if_stmt = If(not_cond, [Return(Literal('bool', False))], None)
        for_loop = ForEach(elem_t, loop_var, Identifier("arr"), [if_stmt])
        return_stmt = Return(Literal('bool', True))
        fn_decl = FunctionDecl(fn_name, [(arr_t, "arr"), (f"fn({elem_t})->bool", "f")], "bool", [for_loop, return_stmt])
        self.synthetic_fns.append(fn_decl)
        return CallExpr(fn_name, [arr, f])
