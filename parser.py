# ============================================================
#  Cryo Compiler - Parser  (v0.2)
# ============================================================

from typing import List, Optional, Tuple
from lexer import Token, TokenType, TYPE_TOKENS
from ast_nodes import (
    Node, Program,
    StructField, StructDecl, EnumDecl, SkillDecl,
    FunctionDecl, VarDecl, ConstDecl, Assignment,
    CompoundAssignment, Increment,
    Return, If, While, For, DoWhile, ForEach, TryCatch,
    Break, Continue, Switch, SwitchCase, Assert, SafetyBlock,
    Import, Library, ForeignBlock,
    Assignment, IndexAssignment,
    BinaryExpr, TernaryExpr, CastExpr, UnwrapExpr, UnaryExpr,
    SpawnExpr, AwaitExpr, CallExpr, MethodCallExpr,
    FieldAccess, IndexAccess, ArrayLiteral, MapLiteral, StructInit,
    Identifier, Literal,
)

COMPOUND_OPS = (
    TokenType.PLUS_ASSIGN, TokenType.MINUS_ASSIGN,
    TokenType.STAR_ASSIGN, TokenType.SLASH_ASSIGN,
    TokenType.PERCENT_ASSIGN, TokenType.AMP_ASSIGN,
    TokenType.PIPE_ASSIGN, TokenType.CARET_ASSIGN,
    TokenType.SHL_ASSIGN, TokenType.SHR_ASSIGN,
)


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos    = 0

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
                f"[Parser] Linha {tok.line}: Esperado {names}, "
                f"obtido {tok.type.name} ({tok.value!r})"
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
        else:
            valid = set(TYPE_TOKENS) | {TokenType.IDENT}
            tok = self._cur()
            if tok.type not in valid:
                raise ParseError(
                    f"[Parser] Linha {tok.line}: Tipo esperado, obtido {tok.type.name} ({tok.value!r})"
                )
            base = self._advance().value
        # sufixo de array [] (aplica-se a qualquer base: primitivo, map, future)
        while self._match(TokenType.LBRACKET) and self._peek().type == TokenType.RBRACKET:
            self._advance()
            self._expect(TokenType.RBRACKET)
            base = base + '[]'
        # opcional: T?
        if self._match(TokenType.QUESTION):
            self._advance()
            base = base + '?'
        return base

    # ── programa ────────────────────────────────────────────

    def parse(self):
        stmts = []
        while not self._match(TokenType.EOF):
            stmts.append(self._stmt())
        return Program(stmts)

    # ── statements ──────────────────────────────────────────

    def _stmt(self):
        tok = self._cur()

        if tok.type == TokenType.FN:      return self._fn()
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
        if tok.type == TokenType.ASSERT:  return self._assert()
        if tok.type in (TokenType.SAFE, TokenType.UNSAFE): return self._safety()
        if tok.type == TokenType.BREAK:
            self._advance(); self._opt_semi(); return Break()
        if tok.type == TokenType.CONTINUE:
            self._advance(); self._opt_semi(); return Continue()
        if tok.type == TokenType.LANG_BLOCK: return self._foreign()

        # tipo primitivo, map ou future -> var decl
        if tok.type in TYPE_TOKENS or tok.type in (TokenType.MAP, TokenType.FUTURE):
            return self._var_decl()

        # identificador -> varias possibilidades
        if tok.type == TokenType.IDENT:
            nt = self._peek()
            # CustomType varName  ou  CustomType[] varName  ou  CustomType? varName
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
            # expressao (possivel alvo de atribuicao indexada: m[k] = v)
            expr = self._postfix()
            if self._match(TokenType.ASSIGN) and isinstance(expr, IndexAccess):
                self._advance()
                val = self._expr()
                self._opt_semi()
                return IndexAssignment(expr.obj, expr.index, val)
            self._opt_semi()
            return expr

        raise ParseError(
            f"[Parser] Linha {tok.line}: Token inesperado {tok.type.name} ({tok.value!r})"
        )

    # ── struct ──────────────────────────────────────────────

    def _struct(self):
        self._expect(TokenType.STRUCT, TokenType.SCHEMA)   # 'schema' = struct
        name = self._expect(TokenType.IDENT).value
        self._expect(TokenType.LBRACE)
        fields = []
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            ftype = self._parse_type()
            fname = self._expect(TokenType.IDENT).value
            self._opt_semi()
            fields.append(StructField(ftype, fname))
        self._expect(TokenType.RBRACE)
        return StructDecl(name, fields)

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
            # separador opcional
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
            members.append(self._expect(TokenType.IDENT).value)
            if self._match(TokenType.COMMA):
                self._advance()
        self._expect(TokenType.RBRACE)
        return EnumDecl(name, members)

    # ── funcao ──────────────────────────────────────────────

    def _tool(self):
        self._expect(TokenType.TOOL)      # 'tool fn ...' — exposta a LLMs
        return self._fn(is_tool=True)

    def _fn(self, is_tool=False):
        self._expect(TokenType.FN)
        name = self._expect(TokenType.IDENT).value
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
        return FunctionDecl(name, params, ret, body, is_tool=is_tool)

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
        tag = self._expect(TokenType.LANG_TAG)
        self._opt_semi()
        return Import(tag.value)

    def _library(self):
        self._expect(TokenType.LIBRARY)
        tag = self._expect(TokenType.LANG_TAG)
        self._opt_semi()
        # A library pode ser qualificada pela linguagem estrangeira:
        #   library >c math<   |   library >go:fmt<   |   library >math<
        raw = tag.value.strip()
        if ':' in raw:
            lang, _, name = raw.partition(':')
        elif raw.split()[1:]:                 # há espaço -> "lang nome"
            parts = raw.split()
            lang, name = parts[0], ' '.join(parts[1:])
        else:
            lang, name = '', raw
        return Library(name=name.strip(), lang=lang.strip())

    def _foreign(self):
        tok = self._expect(TokenType.LANG_BLOCK)
        lang, _, code = tok.value.partition(':')
        return ForeignBlock(lang, code)

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

    # ── for  (clássico ou for-each) ─────────────────────────

    def _is_foreach(self) -> bool:
        """Detecta 'for (TIPO nome in expr)' — tenta parsear um tipo + nome + 'in'
        sem consumir (save/restore), cobrindo map<>, future<>, arrays e opcionais."""
        save = self.pos
        result = False
        try:
            self._parse_type()
            if self._match(TokenType.IDENT):
                self._advance()
                result = self._match(TokenType.IN)
        except ParseError:
            result = False
        self.pos = save
        return result

    def _for(self):
        self._expect(TokenType.FOR)
        self._expect(TokenType.LPAREN)

        if self._is_foreach():
            vtype = self._parse_type()
            vname = self._expect(TokenType.IDENT).value
            self._expect(TokenType.IN)
            iterable = self._expr()
            self._expect(TokenType.RPAREN)
            return ForEach(vtype, vname, iterable, self._block())

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
                    f"[Parser] Linha {tok.line}: Esperado 'case' ou 'default' em switch, "
                    f"obtido {tok.type.name} ({tok.value!r})"
                )
        self._expect(TokenType.RBRACE)
        return Switch(subject, cases, default_body)

    def _case_body(self):
        stmts = []
        while not self._match(TokenType.CASE, TokenType.DEFAULT,
                              TokenType.RBRACE, TokenType.EOF):
            stmts.append(self._stmt())
        return stmts

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

    def _postfix(self):
        expr = self._primary()
        while True:
            if self._match(TokenType.LBRACKET):
                self._advance()
                idx = self._expr()
                self._expect(TokenType.RBRACKET)
                expr = IndexAccess(expr, idx)
            elif self._match(TokenType.DOT):
                self._advance()
                member = self._expect(TokenType.IDENT).value
                if self._match(TokenType.LPAREN):
                    self._advance()
                    args = []
                    while not self._match(TokenType.RPAREN):
                        args.append(self._expr())
                        if self._match(TokenType.COMMA):
                            self._advance()
                    self._expect(TokenType.RPAREN)
                    expr = MethodCallExpr(expr, member, args)
                else:
                    expr = FieldAccess(expr, member)
            elif self._match(TokenType.NOT):
                # desempacotamento de opcional: x!
                self._advance()
                expr = UnwrapExpr(expr)
            else:
                break
        return expr

    def _primary(self):
        tok = self._cur()

        if tok.type == TokenType.INT_LIT:
            self._advance(); return Literal('int', int(tok.value))
        if tok.type == TokenType.FLOAT_LIT:
            self._advance(); return Literal('float', float(tok.value))
        if tok.type == TokenType.STR_LIT:
            self._advance(); return Literal('string', tok.value)
        if tok.type == TokenType.BOOL_LIT:
            self._advance(); return Literal('bool', tok.value == 'true')
        if tok.type == TokenType.NULL:
            self._advance(); return Literal('null', None)

        # array literal
        if tok.type == TokenType.LBRACKET:
            self._advance()
            elems = []
            while not self._match(TokenType.RBRACKET):
                elems.append(self._expr())
                if self._match(TokenType.COMMA):
                    self._advance()
            self._expect(TokenType.RBRACKET)
            return ArrayLiteral(elems)

        # map literal: { chave: valor, ... }  ou  {}
        if tok.type == TokenType.LBRACE:
            self._advance()
            pairs = []
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
            return StructInit(sname, fields)

        # identificador ou chamada de funcao
        if tok.type == TokenType.IDENT:
            name = self._advance().value
            if self._match(TokenType.LPAREN):
                self._advance()
                args = []
                while not self._match(TokenType.RPAREN):
                    args.append(self._expr())
                    if self._match(TokenType.COMMA):
                        self._advance()
                self._expect(TokenType.RPAREN)
                return CallExpr(name, args)
            return Identifier(name)

        if tok.type == TokenType.LPAREN:
            self._advance()
            expr = self._expr()
            self._expect(TokenType.RPAREN)
            return expr

        raise ParseError(
            f"[Parser] Linha {tok.line}: Token inesperado em expressao: "
            f"{tok.type.name} ({tok.value!r})"
        )
