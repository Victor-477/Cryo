# ============================================================
#  Cryo Compiler - Parser  (v0.2)
# ============================================================

import re
from typing import List, Optional, Tuple
from lexer import Token, TokenType, TYPE_TOKENS
from ast_nodes import (
    Node, Program,
    StructField, StructDecl, EnumMember, EnumDecl, SkillDecl,
    TraitMethodSig, TraitDecl, ImplDecl,
    FunctionDecl, VarDecl, ConstDecl, Assignment,
    CompoundAssignment, Increment,
    Return, If, While, For, DoWhile, ForEach, TryCatch, Block,
    Break, Continue, Switch, SwitchCase, Assert, SafetyBlock,
    Import, ModuleImport, Library, ForeignBlock,
    PermissionsDecl,
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
    'http_listen', 'http_accept', 'http_respond',
    'file_exists', 'is_dir', 'list_dir', 'make_dir', 'delete_file', 'file_size', 'write_file', 'env', 'exec',
    'write_file_atomic',
    'url_decode', 'url_encode',
    'asset', 'asset_names',
    'llm_stream', 'llm_next', 'llm_token', 'llm_close',   # 11.17 streaming
    'llm_call', 'llm_try',                                # 11.19 outcomes
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
        # (T) parenthesized type
        elif self._match(TokenType.LPAREN):
            self._advance()
            inner = self._parse_type()
            self._expect(TokenType.RPAREN)
            base = f"({inner})"
        else:
            valid = set(TYPE_TOKENS) | {TokenType.IDENT}
            tok = self._cur()
            if tok.type not in valid:
                raise ParseError(
                    f"[Syntax Error] Line {tok.line}: Expected type, got {tok.type.name} ({tok.value!r})"
                )
            base = self._advance().value
            if self._match(TokenType.COLON_COLON):
                self._advance()
                member = self._expect(TokenType.IDENT).value
                base = f"{base}::{member}"
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
        is_pub = False
        if tok.type == TokenType.PUB:
            self._advance()
            is_pub = True
            tok = self._cur()

        if tok.type == TokenType.FN:
            # `fn name(...)` = declaration; `fn(...)->R var` = function type var
            if self._peek().type == TokenType.LPAREN:
                return self._var_decl()
            return self._fn(is_pub=is_pub)
        if tok.type == TokenType.TOOL:    return self._tool(is_pub=is_pub)
        if tok.type == TokenType.STRUCT:  return self._struct(is_pub=is_pub)
        if tok.type == TokenType.SCHEMA:  return self._struct(is_pub=is_pub)   # schema = struct
        if tok.type == TokenType.ENUM:    return self._enum(is_pub=is_pub)
        if tok.type == TokenType.TRAIT:   return self._trait()
        if tok.type == TokenType.IMPL:    return self._impl()
        if tok.type == TokenType.SKILL:   return self._skill()
        if tok.type == TokenType.CONST:   return self._const(is_pub=is_pub)
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
        if tok.type == TokenType.PERMISSIONS: return self._permissions()
        if tok.type == TokenType.LANG_BLOCK: return self._foreign()

        # primitive type, map, future or (type) -> var decl
        if tok.type in TYPE_TOKENS or tok.type in (TokenType.MAP, TokenType.FUTURE, TokenType.LPAREN):
            return self._var_decl()

        # identifier -> multiple possibilities
        if tok.type == TokenType.IDENT:
            if self._is_generic_var_decl_ahead():
                return self._var_decl()
            nt = self._peek()
            if nt.type == TokenType.COLON_COLON:
                p3 = self._peek(3)
                if p3.type in (TokenType.IDENT, TokenType.LBRACKET, TokenType.QUESTION):
                    return self._var_decl()
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

    def _struct(self, is_pub=False):
        sline = self._cur().line
        self._expect(TokenType.STRUCT, TokenType.SCHEMA)   # 'schema' = struct
        name = self._expect(TokenType.IDENT).value
        type_params = []
        type_bounds = {}
        if self._match(TokenType.LT):
            self._advance()
            while not self._match(TokenType.GT, TokenType.EOF):
                tp = self._expect(TokenType.IDENT).value
                type_params.append(tp)
                if self._match(TokenType.COLON):
                    self._advance()
                    bound = self._expect(TokenType.IDENT).value
                    type_bounds[tp] = bound
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
        return StructDecl(name, fields, line=sline, type_params=type_params, type_bounds=type_bounds, is_pub=is_pub)

    # ── trait / impl ────────────────────────────────────────

    def _trait(self):
        tline = self._cur().line
        self._expect(TokenType.TRAIT)
        name = self._expect(TokenType.IDENT).value
        self._expect(TokenType.LBRACE)
        methods = []
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            self._expect(TokenType.FN)
            mname = self._expect(TokenType.IDENT).value
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
            self._opt_semi()
            methods.append(TraitMethodSig(mname, params, ret))
        self._expect(TokenType.RBRACE)
        return TraitDecl(name, methods, line=tline)

    def _impl(self):
        iline = self._cur().line
        self._expect(TokenType.IMPL)
        first_ident = self._expect(TokenType.IDENT).value
        if self._match(TokenType.FOR):
            self._advance()
            trait_name = first_ident
            target_type = self._parse_type()
        else:
            trait_name = None
            target_type = first_ident
        self._expect(TokenType.LBRACE)
        methods = []
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            if self._match(TokenType.FN):
                methods.append(self._fn())
            else:
                self._advance()
        self._expect(TokenType.RBRACE)
        return ImplDecl(trait_name, target_type, methods, line=iline)

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

    def _enum(self, is_pub=False):
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
        return EnumDecl(name, members, is_pub=is_pub)

    # ── function ────────────────────────────────────────────

    def _tool(self, is_pub=False):
        self._expect(TokenType.TOOL)      # 'tool fn ...' — exposed to LLMs
        return self._fn(is_tool=True, is_pub=is_pub)

    def _fn(self, is_tool=False, is_pub=False):
        fn_line = self._cur().line
        self._expect(TokenType.FN)
        name = self._expect(TokenType.IDENT).value
        type_params = []
        type_bounds = {}
        if self._match(TokenType.LT):
            self._advance()
            while not self._match(TokenType.GT, TokenType.EOF):
                tp = self._expect(TokenType.IDENT).value
                type_params.append(tp)
                if self._match(TokenType.COLON):
                    self._advance()
                    bound = self._expect(TokenType.IDENT).value
                    type_bounds[tp] = bound
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
        return FunctionDecl(name, params, ret, body, is_tool=is_tool, line=fn_line, type_params=type_params, type_bounds=type_bounds, is_pub=is_pub)

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

    def _const(self, is_pub=False):
        self._expect(TokenType.CONST)
        vtype = self._parse_type()
        name  = self._expect(TokenType.IDENT).value
        self._expect(TokenType.ASSIGN)
        val   = self._expr()
        self._opt_semi()
        return ConstDecl(vtype, name, val, is_pub=is_pub)

    # ── import / library / foreign ──────────────────────────

    def _import(self):
        self._expect(TokenType.IMPORT)
        tok = self._cur()
        # import "file.cryo"  -> Cryo module (resolved by the compiler)
        if tok.type == TokenType.STR_LIT:
            self._advance()
            alias = None
            if self._match(TokenType.AS):
                self._advance()
                alias = self._expect(TokenType.IDENT).value
            self._opt_semi()
            return ModuleImport(tok.value, alias=alias)
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

    _PERMISSION_KEYS = ('read', 'write', 'net', 'exec', 'env')

    def _permissions(self):
        """Roadmap 11.12 — `permissions { read = "./data"; net = "host"; }`."""
        tok = self._expect(TokenType.PERMISSIONS)
        self._expect(TokenType.LBRACE)
        grants = {}
        while not self._match(TokenType.RBRACE, TokenType.EOF):
            key_tok = self._cur()
            key = str(key_tok.value)
            self._advance()
            if key not in self._PERMISSION_KEYS:
                raise ParseError(
                    f"[Syntax Error] Line {key_tok.line}: unknown permission "
                    f"'{key}' — expected one of "
                    f"{', '.join(self._PERMISSION_KEYS)}")
            self._expect(TokenType.ASSIGN)
            values = []
            while True:
                v = self._expect(TokenType.STR_LIT)
                values.append(str(v.value))
                if self._match(TokenType.COMMA):
                    self._advance()
                    continue
                break
            self._opt_semi()
            grants.setdefault(key, []).extend(values)
        self._expect(TokenType.RBRACE)
        return PermissionsDecl(grants, line=tok.line)

    def _foreign(self):
        tok = self._expect(TokenType.LANG_BLOCK)
        lang, _, code = tok.value.partition(':')
        return ForeignBlock(lang, code, params=self._struct_params())

    def _struct_params(self):
        """Roadmap 10.12 — the optional `<k = v, ...>` tail of a foreign block.

        The lexer has already consumed the block's `( ... )`, so the tail
        arrives as ordinary tokens. `<` is ALSO the less-than operator, so this
        only commits when the lookahead is unambiguous: `<>` (empty list) or
        `<IDENT =`. Anything else leaves the `<` alone for the expression
        parser, because `>C( ... )` followed by a comparison is still valid.
        """
        if not self._match(TokenType.LT):
            return []
        nxt, after = self._peek(1), self._peek(2)
        empty = nxt.type == TokenType.GT
        pair  = nxt.type == TokenType.IDENT and after.type == TokenType.ASSIGN
        if not (empty or pair):
            return []

        self._advance()                     # '<'
        params = []
        seen = set()
        while not self._match(TokenType.GT, TokenType.EOF):
            key = self._expect(TokenType.IDENT)
            self._expect(TokenType.ASSIGN)
            val = self._expect(TokenType.IDENT).value
            if key.value in seen:
                raise ParseError(
                    f"[Syntax Error] Line {key.line}: structure parameter "
                    f"'{key.value}' given twice in the same block"
                )
            seen.add(key.value)
            params.append((key.value, val))
            if self._match(TokenType.COMMA):
                self._advance()
            elif not self._match(TokenType.GT):
                t = self._cur()
                raise ParseError(
                    f"[Syntax Error] Line {t.line}: expected ',' or '>' in the "
                    f"structure parameter list, got {t.type.name} ({t.value!r})"
                )
        self._expect(TokenType.GT)
        return params

    # ── string interpolation: "total: ${x}" ──────────────

    def _string_literal(self, s: str, line: int):
        """String literal; with `${expr}` becomes concatenation with to_string(expr).

        `${expr:spec}` (11.3) applies a format spec — see _split_fmt_spec.
        """
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
            frag, spec = self._split_fmt_spec(frag, line)
            sub = Parser(_Lexer(frag).tokenize())
            expr = sub._expr()
            # _expr() stops at the first thing it cannot use and does not care
            # what follows, so "${x y}" used to compile as plain `x` with the
            # rest of the fragment dropped. Leftover tokens mean the fragment
            # was not the expression the author wrote.
            if not sub._match(TokenType.EOF):
                bad = sub._cur()
                raise ParseError(
                    f"[Syntax Error] Line {line}: interpolation "
                    f"'${{{frag}}}' has leftover input starting at "
                    f"{bad.type.name} ({bad.value!r})")
            # A nested interpolation may itself desugar to a helper (a range,
            # a lambda). Those land on the SUB-parser, which is thrown away
            # here — so adopt them, or codegen emits a call to a function that
            # was never declared.
            for fn in sub.synthetic_fns:
                if fn.name not in self.user_defined_fns:
                    self.user_defined_fns.add(fn.name)
                    self.synthetic_fns.append(fn)
            parts.append(self._fmt_apply(expr, spec, line) if spec
                         else CallExpr('to_string', [expr]))
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

    # ── format specs: "${value:>10,.2f}"  (roadmap 11.3) ─────
    #
    # A deliberate subset of the Python/Rust format mini-language, because a
    # convention people already know beats a locally invented one:
    #
    #     [[fill]align] [0] [width] [,] [.precision] [type]
    #     align : '<' left   '^' center   '>' right
    #     type  : 'f' fixed-point   'd' integer   's' string   '%' percent
    #
    # Everything is desugared here into ordinary Cryo built from existing
    # builtins — architecture rule 2. No new native, so pyro/go/node, both VMs
    # and the AOT get this with no engine changes and no parity risk.
    #
    # Padding and alignment need repeat()/pad_start()/starts_with(), which the
    # C backend does not implement (for any program, not just this feature), so
    # a spec with a WIDTH is go/node/pyro only and C reports its usual "use
    # --backend go, node or pyro". Precision and grouping avoid those three on
    # purpose, so `${x:.2f}` and `${x:,.2f}` — the common cases — do work on C.
    #
    # Hex/binary/exponent types are NOT covered — radix conversion belongs on
    # to_string, not in a format spec, and guessing at it here would be the
    # wrong place to put it.

    _FMT_SPEC = re.compile(
        r'^(?:(?P<fill>[^{}])?(?P<align>[<^>]))?'
        r'(?P<zero>0)?(?P<width>[1-9][0-9]*)?(?P<comma>,)?'
        r'(?:\.(?P<prec>[0-9]+))?(?P<type>[fds%])?$')

    def _split_fmt_spec(self, frag: str, line: int = 0):
        """Split `expr:spec` into (expr, spec); (frag, None) if there is no spec.

        The colon is ambiguous — it is also the ternary separator, `::` in a
        namespaced name, and a separator inside a map literal or a string. So a
        candidate is accepted only if BOTH halves check out: the tail matches
        the spec grammar, and the head parses as a complete expression on its
        own. `${flag ? 1 : 2}` fails the second test ("flag ? 1" does not
        parse) and is left alone, which is the case that would otherwise break
        silently — a ternary quietly reinterpreted as a width of 2.
        """
        depth = 0
        quote = None
        skip = False
        for i, ch in enumerate(frag):
            if skip:
                skip = False
                continue
            if quote:
                if ch == '\\':
                    skip = True          # the escaped char, not just the '\'
                elif ch == quote:
                    quote = None
                continue
            if ch in '"\'':
                quote = ch
            elif ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
            elif ch == ':' and depth == 0:
                if frag[i + 1:i + 2] == ':' or frag[i - 1:i] == ':':
                    continue                      # ns::name
                head, tail = frag[:i].strip(), frag[i + 1:].strip()
                if not head or not tail:
                    continue
                if not self._parses_alone(head):
                    continue    # not a spec — a ternary, most likely
                if not self._FMT_SPEC.match(tail):
                    # The head IS a complete expression, so this colon was
                    # meant as a spec separator and the spec is simply wrong.
                    # Saying so beats falling through: `${x:>4q}` used to
                    # compile silently and print x with the spec discarded.
                    raise ParseError(
                        f"[Syntax Error] Line {line}: "
                        f"unsupported format spec '{tail}' in '${{{frag}}}'. "
                        f"Expected [[fill]align][0][width][,][.prec][type] — "
                        f"align is one of < ^ >, type is one of f, d, s, %")
                return head, tail
        return frag, None

    def _parses_alone(self, text: str) -> bool:
        """True if `text` is a complete expression with nothing left over."""
        from lexer import Lexer as _Lexer
        try:
            p = Parser(_Lexer(text).tokenize())
            p._expr()
            return p._match(TokenType.EOF)
        except Exception:
            return False

    def _fmt_apply(self, expr, spec: str, line: int):
        """Wrap `expr` in the calls that `spec` describes."""
        m = self._FMT_SPEC.match(spec)
        if not m:   # unreachable via _split_fmt_spec; guards direct callers
            raise ParseError(
                f"[Syntax Error] Line {line}: unsupported format spec "
                f"'{spec}'. Expected [[fill]align][0][width][,][.prec][type], "
                f"where align is < ^ > and type is f, d, s or %")
        fill = m.group('fill')
        align = m.group('align')
        zero = m.group('zero')
        width = int(m.group('width')) if m.group('width') else 0
        comma = bool(m.group('comma'))
        prec = int(m.group('prec')) if m.group('prec') is not None else None
        kind = m.group('type') or ''

        if kind == 's' and comma:
            raise ParseError(
                f"[Syntax Error] Line {line}: format spec '{spec}': ',' groups "
                f"digits, so it does not apply to a string ('s')")
        if kind == 'd' and prec is not None:
            raise ParseError(
                f"[Syntax Error] Line {line}: format spec '{spec}': '.{prec}' "
                f"is a decimal count, so it does not apply to an integer "
                f"('d') — use 'f' for a fixed-point number")
        if prec is not None and not kind:
            # The parser has no types, so '.2' alone cannot be resolved: on a
            # number it means two decimals, on a string it means truncate to
            # two characters. Refusing beats guessing — guessing wrong turns
            # `${pi:.2}` into "3." with nothing to indicate it went wrong.
            raise ParseError(
                f"[Syntax Error] Line {line}: format spec '{spec}': '.{prec}' "
                f"needs a type — '.{prec}f' for {prec} decimals, or "
                f"'.{prec}s' to cut a string to {prec} characters")

        numeric = kind in ('f', 'd', '%') or comma
        if numeric:
            # 'f' and '%' default to 6 decimals, as in C, Python and Rust;
            # 'd' and a bare ',' are whole numbers.
            p = prec if prec is not None else (6 if kind in ('f', '%') else 0)
            val = expr
            if kind == '%':
                val = BinaryExpr('*', expr, Literal('float', 100.0))
            # to_number, not the bare value: the helper's parameter is `number`,
            # and go/c/asm are statically typed — an int argument to a float64
            # parameter does not compile in Go. This is the conversion the
            # dynamic backends would have done implicitly anyway.
            node = CallExpr(self._fmt_num_helper(),
                            [CallExpr('to_number', [val]), Literal('int', p),
                             Literal('int', 1 if comma else 0)], line=line)
            if kind == '%':
                node = BinaryExpr('+', node, Literal('string', '%'))
        else:
            node = CallExpr('to_string', [expr])
            if prec is not None:
                # '.prec' on a string truncates — the counterpart of padding
                node = CallExpr('substr', [node, Literal('int', 0),
                                           Literal('int', prec)])

        if not width:
            return node
        if zero and not align:
            # Zero padding goes AFTER the sign: -0012.50, never 00-12.50.
            # It does not regroup the inserted zeros; ',' groups the value.
            return CallExpr(self._fmt_zero_helper(),
                            [node, Literal('int', width)], line=line)
        # Default alignment follows the convention — numbers right, text left —
        # but the parser has no types, so "numeric" here means the SPEC said so
        # (a type of f/d/% or a ','). `${n:5}` on an integer therefore left-
        # aligns; write `${n:5d}` or `${n:>5}` to get the other one.
        a = align or ('>' if numeric else '<')
        return CallExpr(self._fmt_pad_helper(),
                        [node, Literal('int', width),
                         Literal('string', fill or ('0' if zero else ' ')),
                         Literal('int', {'<': 0, '^': 1, '>': 2}[a])],
                        line=line)

    def _fmt_num_helper(self) -> str:
        """number -> string with a fixed number of decimals, optionally grouped.

        Deliberately integer arithmetic after the one round(): scaling to an
        int and splitting it means the digits come from to_string(int), which
        every backend agrees on, rather than from float formatting, which they
        do not. The cost is that a magnitude beyond int64 once scaled by
        10^prec overflows — formatting is not the tool for those.
        """
        name = '__cryo_fmt_num'
        if name in self.user_defined_fns:
            return name
        self.user_defined_fns.add(name)
        v, sign, a, scale, i, n, ip, fp, out = (
            'v', '__f_sign', '__f_a', '__f_scale', '__f_i',
            '__f_n', '__f_ip', '__f_fp', '__f_out')
        body = [
            VarDecl('string', sign, Literal('string', '')),
            VarDecl('number', a, Identifier(v)),
            If(BinaryExpr('<', Identifier(a), Literal('float', 0.0)),
               [Assignment(sign, Literal('string', '-')),
                Assignment(a, BinaryExpr('-', Literal('float', 0.0),
                                         Identifier(a)))],
               None),
            VarDecl('int', scale, Literal('int', 1)),
            For(VarDecl('int', i, Literal('int', 0)),
                BinaryExpr('<', Identifier(i), Identifier('prec')),
                Increment('++', i),
                [Assignment(scale, BinaryExpr('*', Identifier(scale),
                                              Literal('int', 10)))]),
            VarDecl('int', n, CallExpr('to_int', [CallExpr('round', [
                BinaryExpr('*', Identifier(a),
                           CallExpr('to_number', [Identifier(scale)]))])])),
            VarDecl('int', ip, BinaryExpr('/', Identifier(n), Identifier(scale))),
            VarDecl('int', fp, BinaryExpr('%', Identifier(n), Identifier(scale))),
            VarDecl('string', out, CallExpr('to_string', [Identifier(ip)])),
            If(BinaryExpr('==', Identifier('group'), Literal('int', 1)),
               [Assignment(out, CallExpr(self._fmt_group_helper(),
                                         [Identifier(out)]))],
               None),
            # The fraction needs leading zeros (0.5 at prec 2 is ".50", not
            # ".5"), and the obvious pad_start() is one of the builtins the C
            # backend refuses. scale + fp always has exactly prec+1 digits —
            # fp < scale and scale is 10^prec — so dropping the leading '1'
            # leaves the zero-padded fraction, using only arithmetic and
            # substr. That keeps `${x:.2f}` and `${x:,.2f}` working on C too.
            If(BinaryExpr('>', Identifier('prec'), Literal('int', 0)),
               [Assignment(out, BinaryExpr('+', BinaryExpr('+',
                   Identifier(out), Literal('string', '.')),
                   CallExpr('substr', [
                       CallExpr('to_string', [BinaryExpr('+', Identifier(scale),
                                                         Identifier(fp))]),
                       Literal('int', 1), Identifier('prec')])))],
               None),
            Return(BinaryExpr('+', Identifier(sign), Identifier(out))),
        ]
        self.synthetic_fns.append(
            FunctionDecl(name, [('number', v), ('int', 'prec'), ('int', 'group')],
                         'string', body))
        return name

    def _fmt_group_helper(self) -> str:
        """Insert ',' every three digits. Takes a bare digit run — no sign and
        no decimal point — because index_of() is arrays-only, so a helper that
        had to *find* the parts could not be written from the builtins."""
        name = '__cryo_fmt_group'
        if name in self.user_defined_fns:
            return name
        self.user_defined_fns.add(name)
        s, out, c, i = 's', '__g_out', '__g_c', '__g_i'
        body = [
            VarDecl('string', out, Literal('string', '')),
            VarDecl('int', c, Literal('int', 0)),
            For(VarDecl('int', i, BinaryExpr('-', CallExpr('len', [Identifier(s)]),
                                             Literal('int', 1))),
                BinaryExpr('>=', Identifier(i), Literal('int', 0)),
                Increment('--', i),
                [Assignment(out, BinaryExpr('+',
                    CallExpr('substr', [Identifier(s), Identifier(i),
                                        Literal('int', 1)]),
                    Identifier(out))),
                 Assignment(c, BinaryExpr('+', Identifier(c), Literal('int', 1))),
                 If(BinaryExpr('&&',
                        BinaryExpr('==', BinaryExpr('%', Identifier(c),
                                                    Literal('int', 3)),
                                   Literal('int', 0)),
                        BinaryExpr('>', Identifier(i), Literal('int', 0))),
                    [Assignment(out, BinaryExpr('+', Literal('string', ','),
                                                Identifier(out)))],
                    None)]),
            Return(Identifier(out)),
        ]
        self.synthetic_fns.append(
            FunctionDecl(name, [('string', s)], 'string', body))
        return name

    def _fmt_pad_helper(self) -> str:
        """Pad to `width` with `fill`; align 0=left, 1=center, 2=right.

        Not pad_start/pad_end: those cannot centre, and centring needs the
        extra character to land on the same side on every backend (the right,
        as in Python)."""
        name = '__cryo_fmt_pad'
        if name in self.user_defined_fns:
            return name
        self.user_defined_fns.add(name)
        s, n, l = 's', '__p_n', '__p_l'
        body = [
            VarDecl('int', n, BinaryExpr('-', Identifier('width'),
                                         CallExpr('len', [Identifier(s)]))),
            If(BinaryExpr('<=', Identifier(n), Literal('int', 0)),
               [Return(Identifier(s))], None),
            If(BinaryExpr('==', Identifier('align'), Literal('int', 0)),
               [Return(BinaryExpr('+', Identifier(s),
                                  CallExpr('repeat', [Identifier('fill'),
                                                      Identifier(n)])))], None),
            If(BinaryExpr('==', Identifier('align'), Literal('int', 2)),
               [Return(BinaryExpr('+',
                                  CallExpr('repeat', [Identifier('fill'),
                                                      Identifier(n)]),
                                  Identifier(s)))], None),
            VarDecl('int', l, BinaryExpr('/', Identifier(n), Literal('int', 2))),
            Return(BinaryExpr('+', BinaryExpr('+',
                CallExpr('repeat', [Identifier('fill'), Identifier(l)]),
                Identifier(s)),
                CallExpr('repeat', [Identifier('fill'),
                                    BinaryExpr('-', Identifier(n),
                                               Identifier(l))]))),
        ]
        self.synthetic_fns.append(
            FunctionDecl(name, [('string', s), ('int', 'width'),
                                ('string', 'fill'), ('int', 'align')],
                         'string', body))
        return name

    def _fmt_zero_helper(self) -> str:
        """Left-pad with zeros, keeping a leading '-' in front of them."""
        name = '__cryo_fmt_zero'
        if name in self.user_defined_fns:
            return name
        self.user_defined_fns.add(name)
        s, sign, body_v = 's', '__z_sign', '__z_body'
        body = [
            If(BinaryExpr('>=', CallExpr('len', [Identifier(s)]),
                          Identifier('width')),
               [Return(Identifier(s))], None),
            VarDecl('string', sign, Literal('string', '')),
            VarDecl('string', body_v, Identifier(s)),
            If(CallExpr('starts_with', [Identifier(s), Literal('string', '-')]),
               [Assignment(sign, Literal('string', '-')),
                Assignment(body_v, CallExpr('substr', [
                    Identifier(s), Literal('int', 1),
                    BinaryExpr('-', CallExpr('len', [Identifier(s)]),
                               Literal('int', 1))]))],
               None),
            Return(BinaryExpr('+', Identifier(sign),
                              CallExpr('pad_start', [
                                  Identifier(body_v),
                                  BinaryExpr('-', Identifier('width'),
                                             CallExpr('len', [Identifier(sign)])),
                                  Literal('string', '0')]))),
        ]
        self.synthetic_fns.append(
            FunctionDecl(name, [('string', s), ('int', 'width')],
                         'string', body))
        return name

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

    def _desugar_pairs_loop(self, map_expr, t1, n1, t2, n2, body):
        """`for (k, v in m)` -> index the key list, look each key up.

        A temp for the map is only introduced when the expression could be
        re-evaluated; an identifier is bound directly. That is not just an
        optimisation: the temp used to be declared `any`, and an `any` value
        cannot be indexed or passed to keys() on the go backend, so binding one
        made the whole loop fail to compile there — which is why
        `for (k, v in pairs(m))` had never worked on go. Using the variable
        keeps its real type. The key list is typed from the key variable for
        the same reason.

        A map expression that is NOT an identifier (a call, an index) still
        needs the temp, and still hits that limitation on go until 11.31 lands.
        """
        k_type = t1 if t1 != 'any' else 'string'
        pre = []
        if isinstance(map_expr, Identifier):
            map_ref = map_expr
        else:
            map_var = f"__map_{self._gen_id()}"
            pre.append(VarDecl('any', map_var, map_expr))
            map_ref = Identifier(map_var)

        keys_var = f"__keys_{self._gen_id()}"
        idx_var  = f"__i_{self._gen_id()}"
        init_stmt = VarDecl('int', idx_var, Literal('int', 0))
        cond_expr = BinaryExpr('<', Identifier(idx_var),
                               CallExpr('len', [Identifier(keys_var)]))
        upd_stmt  = Assignment(idx_var, BinaryExpr('+', Identifier(idx_var),
                                                   Literal('int', 1)))
        v1_decl = VarDecl(k_type, n1,
                          IndexAccess(Identifier(keys_var), Identifier(idx_var)))
        v2_decl = VarDecl(t2, n2, IndexAccess(map_ref, Identifier(n1)))
        for_loop = For(init_stmt, cond_expr, upd_stmt, [v1_decl, v2_decl] + body)
        return Block(pre + [
            VarDecl(f"{k_type}[]", keys_var, CallExpr('keys', [map_ref])),
            for_loop,
        ])

    def _desugar_stream_loop(self, vtype, vname, call, body):
        """`for (string t in llm_stream(...))` -> a lazy while loop (11.17).

        Streaming deliberately does NOT use 11.5's `iter()` protocol: that
        returns a materialised collection, and waiting for every token before
        the loop body runs once is precisely what streaming exists to avoid.
        So the loop is driven by advance/read instead —

            int h = llm_stream(model, prompt, opts);
            while (llm_next(h)) { string t = llm_token(h); ... }

        The handle is an `int` on purpose: an `any` cannot cross a typed
        parameter on the go backend (11.31), and go is the only backend the
        LLM layer targets.
        """
        h = f"__stream_{self._gen_id()}"
        return Block([
            VarDecl('int', h, call),
            While(CallExpr('llm_next', [Identifier(h)], line=call.line),
                  [VarDecl(vtype if vtype != 'any' else 'string', vname,
                           CallExpr('llm_token', [Identifier(h)], line=call.line))]
                  + body),
        ])

    def _desugar_for_vars(self, vars_list, iterable, body):
        if len(vars_list) == 1:
            vtype, vname = vars_list[0]
            # 11.17 — a stream is consumed lazily, not as a collection
            if (isinstance(iterable, CallExpr)
                    and iterable.callee == 'llm_stream'
                    and 'llm_stream' not in self.user_defined_fns):
                return self._desugar_stream_loop(vtype, vname, iterable, body)
            return ForEach(vtype, vname, iterable, body)

        t1, n1 = vars_list[0]
        t2, n2 = vars_list[1]

        # enumerate(coll)
        if isinstance(iterable, CallExpr) and iterable.callee == 'enumerate' and len(iterable.args) == 1:
            coll = iterable.args[0]
            # An identifier is used directly instead of being rebound: the temp
            # was declared `any`, and an `any` value can be neither indexed nor
            # passed to len() on the go backend, so `for (i, x in enumerate(xs))`
            # did not compile there. Same defect as the map form below.
            pre_coll = []
            if isinstance(coll, Identifier):
                coll_ref = coll
            else:
                coll_var = f"__coll_{self._gen_id()}"
                pre_coll = [VarDecl('any', coll_var, coll)]
                coll_ref = Identifier(coll_var)
            idx_var  = f"__i_{self._gen_id()}"
            init_stmt = VarDecl('int', idx_var, Literal('int', 0))
            cond_expr = BinaryExpr('<', Identifier(idx_var), CallExpr('len', [coll_ref]))
            upd_stmt  = Assignment(idx_var, BinaryExpr('+', Identifier(idx_var), Literal('int', 1)))
            v1_decl = VarDecl(t1 if t1 != 'any' else 'int', n1, Identifier(idx_var))
            v2_decl = VarDecl(t2, n2, IndexAccess(coll_ref, Identifier(idx_var)))
            loop_body = [v1_decl, v2_decl] + body
            for_loop  = For(init_stmt, cond_expr, upd_stmt, loop_body)
            return Block(pre_coll + [for_loop])

        # pairs(m)  — and, since 11.5, a bare map: `for (k, v in m)`.
        # Two loop variables mean key/value. The parser has no types, so it
        # cannot tell a map from an array here; `enumerate(xs)` above stays the
        # form for an array, and a map is what the two-variable form means.
        # Handing an array to it fails in keys(), which says so.
        if isinstance(iterable, CallExpr) and iterable.callee == 'pairs' and len(iterable.args) == 1:
            return self._desugar_pairs_loop(iterable.args[0], t1, n1, t2, n2, body)
        if len(vars_list) == 2:
            return self._desugar_pairs_loop(iterable, t1, n1, t2, n2, body)

        # generic tuple iteration (3+ variables): item[0], item[1], item[2]…
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
            iterable = self._no_range()
            # range form:  for (int i in start .. end)  /  .. = (inclusive)
            if self._match(TokenType.RANGE, TokenType.RANGE_INCL):
                if len(vars_list) != 1:
                    raise ParseError(f"[Syntax Error] Line {self._cur().line}: range loop requires a single variable")
                vtype, vname = vars_list[0]
                inclusive = self._advance().type == TokenType.RANGE_INCL
                end = self._no_range()
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
            guard = self._match_guard() if self._match(TokenType.IF) else None
            self._expect(TokenType.FAT_ARROW)
            if self._match(TokenType.LBRACE):
                self._advance()
                body = []
                while not self._match(TokenType.RBRACE, TokenType.EOF):
                    body.append(self._stmt())
                self._expect(TokenType.RBRACE)
            else:
                body = [self._stmt()]
            cases.append(MatchCase(pat_name, pat_vars, body, pat_tok.line, guard))
        self._expect(TokenType.RBRACE)
        return self._lower_match_guards(subject, cases, m_tok.line)

    # ── match guards: `Ok(v) if v > 0 => ...`  (roadmap 11.4) ──

    def _match_guard(self):
        """Parse `if <expr>` sitting between a pattern and its `=>`.

        The guard's tokens are sliced out and parsed by a sub-parser rather than
        read inline, because a lambda is detected by "balanced parens followed
        by `=>`" — so `Ok(v) if (n > 0) => ...` read inline would be taken for a
        lambda parameter list. Cutting the stream at the terminating `=>` means
        the lookahead never sees it, and a real lambda inside a guard still
        works. Tightening the lambda heuristic instead would have had to tell
        `(int x)` from `(n)`, which it cannot do reliably.
        """
        from lexer import Lexer as _Lexer   # noqa: F401  (parity with above)
        if_tok = self._advance()                      # 'if'
        start = self.pos
        depth = 0
        while True:
            t = self._cur()
            if t.type == TokenType.EOF:
                raise ParseError(
                    f"[Syntax Error] Line {if_tok.line}: match guard without "
                    f"a following '=>'")
            if t.type in (TokenType.LPAREN, TokenType.LBRACKET, TokenType.LBRACE):
                depth += 1
            elif t.type in (TokenType.RPAREN, TokenType.RBRACKET, TokenType.RBRACE):
                depth -= 1
            elif t.type == TokenType.FAT_ARROW and depth == 0:
                break
            self._advance()
        toks = self.tokens[start:self.pos]
        if not toks:
            raise ParseError(
                f"[Syntax Error] Line {if_tok.line}: empty match guard — "
                f"'if' must be followed by a condition")
        toks = list(toks) + [Token(TokenType.EOF, '', if_tok.line, 0)]
        sub = Parser(toks)
        guard = sub._expr()
        if not sub._match(TokenType.EOF):
            bad = sub._cur()
            raise ParseError(
                f"[Syntax Error] Line {if_tok.line}: match guard has leftover "
                f"input starting at {bad.type.name} ({bad.value!r})")
        for fn in sub.synthetic_fns:      # a guard may contain a lambda/range
            if fn.name not in self.user_defined_fns:
                self.user_defined_fns.add(fn.name)
                self.synthetic_fns.append(fn)
        return guard

    def _lower_match_guards(self, subject, cases, line: int):
        """Rewrite guards into `if` chains, so no backend has to know about them.

        A guard needs "test, and if it fails try the next case", which `match`
        cannot express — it dispatches on the constructor. But every case with
        the SAME constructor binds the same payload, so they can be collapsed
        into one case whose body is an if/else chain over the guards. The
        subject is still evaluated exactly once.

        Falling off the end of a group lands in the `_` case's body, which is
        therefore copied into the chain's final else.
        """
        if not any(c.guard is not None for c in cases):
            return MatchStatement(subject, cases, line)   # untouched

        import copy
        order, groups = [], {}
        for c in cases:
            if c.pattern_name not in groups:
                groups[c.pattern_name] = []
                order.append(c.pattern_name)
            groups[c.pattern_name].append(c)

        wildcard = groups.get('_', [])
        wild_fallthrough = None
        for c in wildcard:
            if c.guard is None:
                wild_fallthrough = c.body

        out = []
        for name in order:
            if name == '_':
                continue                       # emitted last, below
            grp = groups[name]
            if len(grp) == 1 and grp[0].guard is None:
                out.append(grp[0])
                continue
            out.append(self._merge_guarded(name, grp, wild_fallthrough, copy))
        if wildcard:
            out.append(self._merge_guarded('_', wildcard, None, copy)
                       if len(wildcard) > 1 or wildcard[0].guard is not None
                       else wildcard[0])
        return MatchStatement(subject, out, line)

    def _merge_guarded(self, name, grp, fallthrough, copy):
        """One case per constructor, body = if/else chain over that group."""
        vars0 = grp[0].pattern_vars
        for c in grp[1:]:
            if c.pattern_vars != vars0:
                # Renaming the body would work, but silently rewriting user
                # identifiers is the kind of thing that goes wrong quietly.
                # Asking for one name costs the author nothing.
                raise ParseError(
                    f"[Syntax Error] Line {c.line}: guarded '{name}' cases in "
                    f"one match must bind the same name(s); this one binds "
                    f"{c.pattern_vars or ['nothing']} but the first binds "
                    f"{vars0 or ['nothing']}")
        for i, c in enumerate(grp[:-1]):
            if c.guard is None:
                raise ParseError(
                    f"[Syntax Error] Line {grp[i + 1].line}: this '{name}' case "
                    f"is unreachable — the unguarded '{name}' case on line "
                    f"{c.line} already matches everything. Put the guarded "
                    f"cases first")
        if grp[-1].guard is None:
            tail = grp[-1].body                       # the group's own default
            chain = grp[:-1]
        else:
            # every case guarded: fall through to the wildcard, if any
            tail = copy.deepcopy(fallthrough) if fallthrough else None
            chain = grp
        node = tail
        for c in reversed(chain):
            node = [If(c.guard, c.body, node)]
        return MatchCase(name, vars0, node or [], grp[0].line)

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

    def _expr(self):  return self._range()

    def _no_range(self):
        """An expression that stops before `..`.

        Four constructs consume the range tokens THEMSELVES — the range `for`
        (10.1), which lowers to a counted loop rather than building an array,
        and slice syntax (10.9). They must parse their operands with this, or
        `_range` below would swallow the `..` first and `for (int i in 0..n)`
        would silently start allocating an array per loop.
        """
        return self._ternary()

    def _range(self):
        """Roadmap 10.9 — `a..b` as a value: the array [a, a+1, ..., b-1].

        Binds looser than everything else, so `0..n+1` reads as `0..(n+1)`.
        Lowered to a synthetic function rather than a new native, so all six
        backends get it with no VM change (architecture rule 2), matching how
        10.1 and the comprehensions are done.
        """
        left = self._ternary()
        if not self._match(TokenType.RANGE, TokenType.RANGE_INCL):
            return left
        tok = self._advance()
        right = self._ternary()
        if tok.type == TokenType.RANGE_INCL:
            right = BinaryExpr('+', right, Literal('int', 1))
        return CallExpr(self._range_helper(), [left, right], line=tok.line)

    def _range_helper(self) -> str:
        """Declare (once) the function a range value expands to."""
        name = '__cryo_range'
        if name not in self.user_defined_fns:
            self.user_defined_fns.add(name)
            i = '__r_i'
            body = [
                VarDecl('int[]', '__r_out', ArrayLiteral([])),
                For(VarDecl('int', i, Identifier('lo')),
                    BinaryExpr('<', Identifier(i), Identifier('hi')),
                    Increment('++', i),
                    [MethodCallExpr(Identifier('__r_out'), 'push',
                                    [Identifier(i)])]),
                Return(Identifier('__r_out')),
            ]
            self.synthetic_fns.append(
                FunctionDecl(name, [('int', 'lo'), ('int', 'hi')],
                             'int[]', body))
        return name

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

    def _slice_expr(self, obj, start, lb):
        """Roadmap 10.9 — `xs[a..b]`, `xs[a..=b]`, `xs[a..]`, `xs[..b]`.

        Lowered here in the front end so all six backends get slicing for free
        (architecture rule 2). The result is always `slice(obj, start, end)`;
        the `slice` native is polymorphic over array|string because the parser
        cannot know which one `obj` is.
        """
        tok = self._advance()                       # RANGE or RANGE_INCL
        inclusive = tok.type == TokenType.RANGE_INCL

        if self._match(TokenType.RBRACKET):
            # Open end: `xs[a..]` means "through the last element", which needs
            # len(obj) — so obj is evaluated TWICE. Only safe for a plain name;
            # anything else could have side effects or be expensive.
            if not isinstance(obj, Identifier):
                raise ParseError(
                    f"[Syntax Error] Line {lb.line}: an open-ended slice `[a..]` "
                    "requires a simple variable on the left, because the value is "
                    "needed twice (once to slice, once for its length); assign it "
                    "to a variable first")
            if inclusive:
                raise ParseError(
                    f"[Syntax Error] Line {tok.line}: `..=` needs an end index; "
                    "write `[a..]` for an open-ended slice")
            end = CallExpr('len', [Identifier(obj.name)], line=lb.line)
        else:
            end = self._no_range()
            if inclusive:
                # `a..=b` includes b, and slice()'s end is exclusive.
                end = BinaryExpr('+', end, Literal('int', 1))
        self._expect(TokenType.RBRACKET)
        return CallExpr('slice', [obj, start, end], line=lb.line)

    def _postfix(self):
        expr = self._primary()
        while True:
            if self._match(TokenType.LBRACKET):
                lb = self._advance()
                # `x[..b]` — open start. Checked before parsing an index so the
                # leading `..` is not mistaken for a malformed expression.
                if self._match(TokenType.RANGE, TokenType.RANGE_INCL):
                    expr = self._slice_expr(expr, Literal('int', 0), lb)
                    continue
                idx = self._no_range()
                if self._match(TokenType.RANGE, TokenType.RANGE_INCL):
                    expr = self._slice_expr(expr, idx, lb)
                    continue
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
            if self._match(TokenType.COLON_COLON):
                self._advance()
                member = self._expect(TokenType.IDENT).value
                name = f"{name}::{member}"
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
            if self._match(TokenType.LBRACE) and (self._peek().type == TokenType.RBRACE or (self._peek().type == TokenType.IDENT and self._peek(2).type == TokenType.COLON)):
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
                return StructInit(name, fields)
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
        iterable = self._no_range()

        is_range = False
        inclusive = False
        range_end = None
        if self._match(TokenType.RANGE, TokenType.RANGE_INCL):
            is_range = True
            inclusive = self._advance().type == TokenType.RANGE_INCL
            range_end = self._no_range()

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
        iterable = self._no_range()

        is_range = False
        inclusive = False
        range_end = None
        if self._match(TokenType.RANGE, TokenType.RANGE_INCL):
            is_range = True
            inclusive = self._advance().type == TokenType.RANGE_INCL
            range_end = self._no_range()

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
        if name == 'llm' and len(args) == 3:
            self._check_llm_options(args[2], id_line)   # 11.16
        if name in ('llm_call', 'llm_stream') and len(args) == 3:
            self._check_llm_options(args[2], id_line)
        if name == 'llm_try' and 'llm_try' not in self.user_defined_fns:
            return self._llm_try(args, id_line)         # 11.19
        return CallExpr(name, args, line=id_line)

    # ── llm_try: the outcome as something you can match on (11.19) ──
    #
    #   match llm_try("model", "prompt", { "retries": 3 }) {
    #       LlmOk(text)           => print(text);
    #       LlmFailed(kind, why) if kind == "rate_limited" => backOff();
    #       LlmFailed(kind, why)  => print("failed: ${kind}");
    #   }
    #
    # Built entirely in the front end. `llm_call` is the one primitive the
    # backend adds — it returns [kind, text|detail] — and everything above it
    # is a synthetic enum plus one wrapper function per call site. The options
    # are baked into the wrapper because 11.16 already requires them to be a
    # map literal.
    #
    # The variants are NOT called Ok/Err: enum members land in the same
    # namespace as the program's own, and a project with `enum Result { Ok…`
    # is entirely likely. A silent clash is worse than a longer name.

    def _llm_try(self, args, line: int):
        if len(args) not in (2, 3):
            raise ParseError(
                f"[Syntax Error] Line {line}: llm_try(model, prompt) takes 2 "
                f"arguments, or 3 with the options map")
        if len(args) == 3:
            self._check_llm_options(args[2], line)
        self._llm_outcome_enum()
        opts = args[2] if len(args) == 3 else MapLiteral([])
        name = f"__cryo_llm_try_{self._gen_id()}"
        self.user_defined_fns.add(name)
        r = '__lt_r'
        body = [
            VarDecl('string[]', r,
                    CallExpr('llm_call', [Identifier('m'), Identifier('p'), opts],
                             line=line)),
            If(BinaryExpr('==', IndexAccess(Identifier(r), Literal('int', 0)),
                          Literal('string', '')),
               [Return(CallExpr('LlmOk',
                                [IndexAccess(Identifier(r), Literal('int', 1))],
                                line=line))],
               None),
            Return(CallExpr('LlmFailed',
                            [IndexAccess(Identifier(r), Literal('int', 0)),
                             IndexAccess(Identifier(r), Literal('int', 1))],
                            line=line)),
        ]
        self.synthetic_fns.append(
            FunctionDecl(name, [('string', 'm'), ('string', 'p')],
                         'LlmOutcome', body))
        return CallExpr(name, [args[0], args[1]], line=line)

    def _llm_outcome_enum(self):
        """Declare `enum LlmOutcome { LlmOk(string), LlmFailed(string, string) }`
        once, the first time llm_try is used."""
        if getattr(self, '_llm_enum_done', False):
            return
        self._llm_enum_done = True
        self.synthetic_fns.append(EnumDecl('LlmOutcome', [
            EnumMember('LlmOk', ['string']),
            EnumMember('LlmFailed', ['string', 'string']),
        ]))

    # ── LLM generation controls (roadmap 11.16) ──────────────
    #
    #   llm("model", prompt, { temperature: 0.2, max_tokens: 400, seed: 7 })
    #
    # Checked here, at the call site, rather than passed through to the
    # provider. An unrecognised option that reaches an HTTP API is either
    # ignored or rejected far from the line that wrote it — and a `temprature`
    # typo that silently produces default-temperature output is exactly the
    # kind of failure a program cannot notice.
    _LLM_OPTIONS = {
        'temperature': 'a number (0.0-2.0)',
        'top_p':       'a number (0.0-1.0)',
        'max_tokens':  'an int',
        'stop':        'a string, or an array of strings',
        'seed':        'an int',
        'timeout':     'an int (milliseconds)',
        'repair':      'an int (how many times to re-ask on a bad reply)',
        'retries':     'an int (transport retries, with backoff)',
    }

    def _check_llm_options(self, node, line: int):
        """The third argument of llm() must be a literal map of known options."""
        if not isinstance(node, MapLiteral):
            raise ParseError(
                f"[Syntax Error] Line {line}: the third argument of llm() is "
                f"the generation options and must be written as a map literal, "
                f"e.g. llm(model, prompt, {{ temperature: 0.2 }})")
        seen = set()
        for k, v in node.pairs:
            if isinstance(k, Literal) and k.kind == 'string':
                key = k.value
            elif isinstance(k, Identifier):
                # A bare key is not map syntax in Cryo — it parses as a
                # variable reference, so it would surface much later as
                # "undeclared variable 'temperature'", which says nothing
                # about the real mistake.
                raise ParseError(
                    f"[Syntax Error] Line {line}: llm() option names are map "
                    f"keys and must be quoted — write "
                    f"{{ \"{k.name}\": … }}, not {{ {k.name}: … }}")
            else:
                raise ParseError(
                    f"[Syntax Error] Line {line}: llm() option names must be "
                    f"written literally, so they can be checked here")
            if key not in self._LLM_OPTIONS:
                near = ', '.join(sorted(self._LLM_OPTIONS))
                raise ParseError(
                    f"[Syntax Error] Line {line}: unknown llm() option "
                    f"'{key}'. Supported: {near}")
            if key in seen:
                raise ParseError(
                    f"[Syntax Error] Line {line}: llm() option '{key}' is "
                    f"set twice")
            seen.add(key)
            self._check_llm_option_value(key, v, line)

    def _check_llm_option_value(self, key, v, line: int):
        """Reject a literal of the wrong kind; let expressions through.

        Only literals can be judged here — `seed: n` is legitimate and its type
        is not knowable at parse time — so this catches the mistakes it can
        prove and leaves the rest to the provider.
        """
        want = self._LLM_OPTIONS[key]
        if key == 'stop':
            if isinstance(v, Literal) and v.kind != 'string':
                raise ParseError(
                    f"[Syntax Error] Line {line}: llm() option 'stop' takes "
                    f"{want}")
            return
        if not isinstance(v, Literal):
            return                       # an expression: cannot judge it here
        if key in ('max_tokens', 'seed', 'timeout', 'repair', 'retries'):
            if v.kind != 'int':
                raise ParseError(
                    f"[Syntax Error] Line {line}: llm() option '{key}' takes "
                    f"{want}, got {v.kind}")
        elif key in ('temperature', 'top_p'):
            if v.kind not in ('int', 'float'):
                raise ParseError(
                    f"[Syntax Error] Line {line}: llm() option '{key}' takes "
                    f"{want}, got {v.kind}")

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
