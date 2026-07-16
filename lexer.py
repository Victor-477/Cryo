# ============================================================
#  Cryo Compiler - Lexer  (v0.2)
# ============================================================

from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, List


class TokenType(Enum):
    # Palavras-chave
    FN       = auto()
    RETURN   = auto()
    IMPORT   = auto()
    LIBRARY  = auto()
    IF       = auto()
    ELSE     = auto()
    FOR      = auto()
    WHILE    = auto()
    NULL     = auto()
    STRUCT   = auto()
    ENUM     = auto()
    CONST    = auto()
    NEW      = auto()
    TRY      = auto()
    CATCH    = auto()
    FINALLY  = auto()
    BREAK    = auto()
    CONTINUE = auto()
    SWITCH   = auto()
    CASE     = auto()
    DEFAULT  = auto()
    ASSERT   = auto()
    SAFE     = auto()
    UNSAFE   = auto()
    DO       = auto()
    IN       = auto()
    MAP      = auto()
    AS       = auto()
    SKILL    = auto()
    SPAWN    = auto()
    AWAIT    = auto()
    FUTURE   = auto()
    # Tipos
    TYPE_INT    = auto()
    TYPE_NUMBER = auto()
    TYPE_STRING = auto()
    TYPE_BOOL   = auto()
    TYPE_VOID   = auto()
    # Literais
    INT_LIT   = auto()
    FLOAT_LIT = auto()
    STR_LIT   = auto()
    BOOL_LIT  = auto()
    # Identificador
    IDENT = auto()
    # Aritmetica
    PLUS    = auto()
    MINUS   = auto()
    STAR    = auto()
    SLASH   = auto()
    PERCENT = auto()
    # Atribuicao
    ASSIGN       = auto()
    BODY_ASSIGN  = auto()
    PLUS_ASSIGN  = auto()
    MINUS_ASSIGN = auto()
    STAR_ASSIGN  = auto()
    SLASH_ASSIGN = auto()
    PERCENT_ASSIGN = auto()
    AMP_ASSIGN     = auto()
    PIPE_ASSIGN    = auto()
    CARET_ASSIGN   = auto()
    SHL_ASSIGN     = auto()
    SHR_ASSIGN     = auto()
    PLUS_PLUS    = auto()
    MINUS_MINUS  = auto()
    # Comparacao
    EQ  = auto()
    NEQ = auto()
    LT  = auto()
    GT  = auto()
    LEQ = auto()
    GEQ = auto()
    # Logicos
    AND = auto()
    OR  = auto()
    NOT = auto()
    # Bit a bit
    AMP   = auto()   # &
    PIPE  = auto()   # |
    CARET = auto()   # ^
    TILDE = auto()   # ~
    SHL   = auto()   # <<
    SHR   = auto()   # >>
    # Especiais
    ARROW     = auto()
    NULL_COAL = auto()
    QUESTION  = auto()
    # Pontuacao
    LPAREN    = auto()
    RPAREN    = auto()
    LBRACE    = auto()
    RBRACE    = auto()
    LBRACKET  = auto()
    RBRACKET  = auto()
    SEMICOLON = auto()
    COMMA     = auto()
    COLON     = auto()
    DOT       = auto()
    # Linguagem estrangeira
    LANG_TAG   = auto()
    LANG_BLOCK = auto()
    # EOF
    EOF = auto()


KEYWORDS = {
    'fn':      TokenType.FN,
    'return':  TokenType.RETURN,
    'import':  TokenType.IMPORT,
    'library': TokenType.LIBRARY,
    'if':      TokenType.IF,
    'else':    TokenType.ELSE,
    'for':     TokenType.FOR,
    'while':   TokenType.WHILE,
    'null':    TokenType.NULL,
    'struct':  TokenType.STRUCT,
    'enum':    TokenType.ENUM,
    'const':   TokenType.CONST,
    'new':     TokenType.NEW,
    'try':     TokenType.TRY,
    'catch':   TokenType.CATCH,
    'finally': TokenType.FINALLY,
    'break':    TokenType.BREAK,
    'continue': TokenType.CONTINUE,
    'switch':   TokenType.SWITCH,
    'case':     TokenType.CASE,
    'default':  TokenType.DEFAULT,
    'assert':   TokenType.ASSERT,
    'safe':     TokenType.SAFE,
    'unsafe':   TokenType.UNSAFE,
    'do':       TokenType.DO,
    'in':       TokenType.IN,
    'map':      TokenType.MAP,
    'as':       TokenType.AS,
    'skill':    TokenType.SKILL,
    'spawn':    TokenType.SPAWN,
    'await':    TokenType.AWAIT,
    'future':   TokenType.FUTURE,
    'int':     TokenType.TYPE_INT,
    'number':  TokenType.TYPE_NUMBER,
    'string':  TokenType.TYPE_STRING,
    'bool':    TokenType.TYPE_BOOL,
    'void':    TokenType.TYPE_VOID,
    'true':    TokenType.BOOL_LIT,
    'false':   TokenType.BOOL_LIT,
}

TYPE_TOKENS = (
    TokenType.TYPE_INT, TokenType.TYPE_NUMBER,
    TokenType.TYPE_STRING, TokenType.TYPE_BOOL,
    TokenType.TYPE_VOID,
)


@dataclass
class Token:
    type:  TokenType
    value: str
    line:  int
    col:   int

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.col})"


class LexerError(Exception):
    pass


class Lexer:
    def __init__(self, source):
        # Ignora BOM UTF-8 no inicio (comum em editores/PowerShell no Windows)
        if source.startswith('﻿'):
            source = source[1:]
        self.source = source
        self.pos    = 0
        self.line   = 1
        self.col    = 1

    def _error(self, msg):
        raise LexerError(f"[Lexer] Linha {self.line}, Col {self.col}: {msg}")

    def _peek(self, offset=0):
        idx = self.pos + offset
        return self.source[idx] if idx < len(self.source) else None

    def _advance(self):
        ch = self.source[self.pos]
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _skip(self):
        while self.pos < len(self.source):
            ch = self._peek()
            if ch in ' \t\r\n':
                self._advance()
            elif ch == '/' and self._peek(1) == '/':
                while self.pos < len(self.source) and self._peek() != '\n':
                    self._advance()
            elif ch == '/' and self._peek(1) == '*':
                self._advance(); self._advance()
                while self.pos < len(self.source):
                    if self._peek() == '*' and self._peek(1) == '/':
                        self._advance(); self._advance(); break
                    self._advance()
            else:
                break

    def _read_ident(self):
        start = self.pos
        while self.pos < len(self.source) and (self.source[self.pos].isalnum() or self.source[self.pos] == '_'):
            self._advance()
        return self.source[start:self.pos]

    def _read_number(self, sl, sc):
        # Prefixos: 0x (hex), 0b (binario), 0o (octal). Underscores permitidos.
        if self._peek() == '0' and self._peek(1) in ('x', 'X', 'b', 'B', 'o', 'O'):
            self._advance()                      # '0'
            base_ch = self._advance().lower()    # 'x' / 'b' / 'o'
            base = {'x': 16, 'b': 2, 'o': 8}[base_ch]
            digits = '0123456789abcdef'[:base]
            raw = ''
            while self.pos < len(self.source):
                c = self._peek()
                if c == '_':
                    self._advance(); continue
                if c is not None and c.lower() in digits:
                    raw += self._advance()
                else:
                    break
            if not raw:
                self._error(f"Literal numerico invalido apos '0{base_ch}'")
            return Token(TokenType.INT_LIT, str(int(raw, base)), sl, sc)

        num = ''; is_float = False
        while self.pos < len(self.source):
            c = self._peek()
            if c == '_':
                self._advance(); continue
            if c.isdigit() or (c == '.' and not is_float):
                if c == '.':
                    is_float = True
                num += self._advance()
            else:
                break
        return Token(TokenType.FLOAT_LIT if is_float else TokenType.INT_LIT, num, sl, sc)

    def _read_string(self, sl, sc):
        quote = self._advance()
        s = ''
        while self.pos < len(self.source) and self._peek() != quote:
            if self._peek() == '\\':
                self._advance()
                esc = self._advance()
                s += {'n': '\n', 't': '\t', '\\': '\\', '"': '"', "'": "'"}.get(esc, esc)
            else:
                s += self._advance()
        if self.pos >= len(self.source):
            self._error("String nao terminada")
        self._advance()
        return Token(TokenType.STR_LIT, s, sl, sc)

    def _read_lang(self, sl, sc):
        self._advance()  # consume '>'
        lang = ''
        while self.pos < len(self.source) and self._peek() not in '<(':
            lang += self._advance()
        lang = lang.strip()
        if self._peek() == '<':
            self._advance()
            return Token(TokenType.LANG_TAG, lang, sl, sc)
        elif self._peek() == '(':
            self._advance()
            code = ''; depth = 1
            while self.pos < len(self.source) and depth > 0:
                ch = self._peek()
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        self._advance(); break
                code += self._advance()
            return Token(TokenType.LANG_BLOCK, lang + ':' + code, sl, sc)
        else:
            self._error(f"Esperado '<' ou '(' apos '{lang}'")

    def tokenize(self):
        tokens = []
        while True:
            self._skip()
            if self.pos >= len(self.source):
                tokens.append(Token(TokenType.EOF, '', self.line, self.col))
                break
            sl, sc = self.line, self.col
            ch = self._peek()

            if ch == '>' and self._peek(1) and (self._peek(1).isalpha() or self._peek(1) == '_'):
                tokens.append(self._read_lang(sl, sc))
            elif ch.isalpha() or ch == '_':
                name = self._read_ident()
                tokens.append(Token(KEYWORDS.get(name, TokenType.IDENT), name, sl, sc))
            elif ch.isdigit():
                tokens.append(self._read_number(sl, sc))
            elif ch in ('"', "'"):
                tokens.append(self._read_string(sl, sc))
            elif ch == '+':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.PLUS_ASSIGN, '+=', sl, sc))
                elif self._peek() == '+':
                    self._advance(); tokens.append(Token(TokenType.PLUS_PLUS, '++', sl, sc))
                else:
                    tokens.append(Token(TokenType.PLUS, '+', sl, sc))
            elif ch == '-':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.MINUS_ASSIGN, '-=', sl, sc))
                elif self._peek() == '-':
                    self._advance(); tokens.append(Token(TokenType.MINUS_MINUS, '--', sl, sc))
                elif self._peek() == '>':
                    self._advance(); tokens.append(Token(TokenType.ARROW, '->', sl, sc))
                else:
                    tokens.append(Token(TokenType.MINUS, '-', sl, sc))
            elif ch == '*':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.STAR_ASSIGN, '*=', sl, sc))
                else:
                    tokens.append(Token(TokenType.STAR, '*', sl, sc))
            elif ch == '/':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.SLASH_ASSIGN, '/=', sl, sc))
                else:
                    tokens.append(Token(TokenType.SLASH, '/', sl, sc))
            elif ch == '%':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.PERCENT_ASSIGN, '%=', sl, sc))
                else:
                    tokens.append(Token(TokenType.PERCENT, '%', sl, sc))
            elif ch == '=':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.EQ, '==', sl, sc))
                elif self._peek() == '{':
                    self._advance(); tokens.append(Token(TokenType.BODY_ASSIGN, '={', sl, sc))
                else:
                    tokens.append(Token(TokenType.ASSIGN, '=', sl, sc))
            elif ch == '!':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.NEQ, '!=', sl, sc))
                else:
                    tokens.append(Token(TokenType.NOT, '!', sl, sc))
            elif ch == '<':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.LEQ, '<=', sl, sc))
                elif self._peek() == '<':
                    self._advance()
                    if self._peek() == '=':
                        self._advance(); tokens.append(Token(TokenType.SHL_ASSIGN, '<<=', sl, sc))
                    else:
                        tokens.append(Token(TokenType.SHL, '<<', sl, sc))
                else:
                    tokens.append(Token(TokenType.LT, '<', sl, sc))
            elif ch == '>':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.GEQ, '>=', sl, sc))
                elif self._peek() == '>':
                    self._advance()
                    if self._peek() == '=':
                        self._advance(); tokens.append(Token(TokenType.SHR_ASSIGN, '>>=', sl, sc))
                    else:
                        tokens.append(Token(TokenType.SHR, '>>', sl, sc))
                else:
                    tokens.append(Token(TokenType.GT, '>', sl, sc))
            elif ch == '&':
                self._advance()
                if self._peek() == '&':
                    self._advance(); tokens.append(Token(TokenType.AND, '&&', sl, sc))
                elif self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.AMP_ASSIGN, '&=', sl, sc))
                else:
                    tokens.append(Token(TokenType.AMP, '&', sl, sc))
            elif ch == '|':
                self._advance()
                if self._peek() == '|':
                    self._advance(); tokens.append(Token(TokenType.OR, '||', sl, sc))
                elif self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.PIPE_ASSIGN, '|=', sl, sc))
                else:
                    tokens.append(Token(TokenType.PIPE, '|', sl, sc))
            elif ch == '^':
                self._advance()
                if self._peek() == '=':
                    self._advance(); tokens.append(Token(TokenType.CARET_ASSIGN, '^=', sl, sc))
                else:
                    tokens.append(Token(TokenType.CARET, '^', sl, sc))
            elif ch == '~':
                self._advance(); tokens.append(Token(TokenType.TILDE, '~', sl, sc))
            elif ch == '?':
                self._advance()
                if self._peek() == '?':
                    self._advance(); tokens.append(Token(TokenType.NULL_COAL, '??', sl, sc))
                else:
                    tokens.append(Token(TokenType.QUESTION, '?', sl, sc))
            elif ch == '(':
                self._advance(); tokens.append(Token(TokenType.LPAREN,    '(', sl, sc))
            elif ch == ')':
                self._advance(); tokens.append(Token(TokenType.RPAREN,    ')', sl, sc))
            elif ch == '{':
                self._advance(); tokens.append(Token(TokenType.LBRACE,    '{', sl, sc))
            elif ch == '}':
                self._advance(); tokens.append(Token(TokenType.RBRACE,    '}', sl, sc))
            elif ch == '[':
                self._advance(); tokens.append(Token(TokenType.LBRACKET,  '[', sl, sc))
            elif ch == ']':
                self._advance(); tokens.append(Token(TokenType.RBRACKET,  ']', sl, sc))
            elif ch == ';':
                self._advance(); tokens.append(Token(TokenType.SEMICOLON, ';', sl, sc))
            elif ch == ',':
                self._advance(); tokens.append(Token(TokenType.COMMA,     ',', sl, sc))
            elif ch == ':':
                self._advance(); tokens.append(Token(TokenType.COLON,     ':', sl, sc))
            elif ch == '.':
                self._advance(); tokens.append(Token(TokenType.DOT,       '.', sl, sc))
            else:
                self._error(f"Caractere inesperado '{ch}'")
        return tokens
