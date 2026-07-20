# ============================================================
#  Cryo Compiler - Auditoria de Seguranca Estatica  (v0.5)
#
#  Percorre a AST e reporta padroes de risco antes da geracao
#  de codigo. Nao substitui o modo --safe (instrumentacao em
#  tempo de execucao); complementa-o com analise estatica.
#
#  v0.5: analise de taint (fonte nao confiavel -> sink perigoso:
#  injecao de comando, path traversal, SSRF) e deteccao de
#  segredos embutidos no codigo-fonte.
# ============================================================
import re
from dataclasses import dataclass, fields, is_dataclass
from typing import List, Any, Set
from ast_nodes import (
    Node, ForeignBlock, SafetyBlock, Import, Library,
    BinaryExpr, CallExpr, Literal, VarDecl, ConstDecl,
    Assignment, CompoundAssignment, ForEach, Identifier,
    FunctionDecl,
)


@dataclass
class Finding:
    level:   str    # 'ALTO' | 'MEDIO' | 'BAIXO'
    rule:    str
    message: str


# ── operacoes sensiveis (callee -> nivel, regra, mensagem) ──
_SENSITIVE = {
    'pyro_exec': ('ALTO', 'command-exec',
                  "pyro_exec() executa um comando de shell arbitrário — "
                  "nunca passe entrada não confiável como comando."),
    'pyro_write_file': ('MEDIO', 'file-write',
                        "pyro_write_file() grava em caminho arbitrário no disco — "
                        "valide o caminho (evite path traversal)."),
    'pyro_open': ('MEDIO', 'shell-open',
                  "pyro_open() abre um arquivo/URL no app padrão do SO — "
                  "não abra alvos vindos de entrada não confiável."),
    'pyro_exit': ('BAIXO', 'process-exit',
                  "pyro_exit() encerra o processo."),
    'http_get':  ('MEDIO', 'net-egress',
                  "http_get() faz requisição de rede — risco de SSRF se a URL "
                  "vier de entrada não confiável."),
    'http_post': ('MEDIO', 'net-egress',
                  "http_post() envia dados pela rede — confirme destino e conteúdo."),
    'llm':   ('BAIXO', 'llm-egress',
              "llm() envia o prompt a um endpoint externo (CRYO_LLM_URL)."),
    'agent': ('BAIXO', 'llm-egress',
              "agent() troca dados com um LLM externo e executa tools em ciclo."),
}


# ── analise de taint (fluxo de dados nao confiaveis) ─────────
#
# Fontes: builtins que produzem dado nao confiavel (entrada do
# usuario, rede, ambiente, saida de LLM). Sinks: builtins que, se
# alimentados com dado nao confiavel, viram um risco concreto
# (injecao de comando, path traversal, SSRF). A analise e
# intraprocedural-aproximada (fixpoint sobre nomes de variaveis no
# programa inteiro), conservadora e sem substituir revisao manual.
_TAINT_SOURCES = {
    'input', 'input_int', 'input_num', 'pyro_read',
    'pyro_args', 'pyro_env',
    'http_get', 'http_post',
    'llm', 'agent',
}

# callee -> (indice_do_arg, nivel, regra, mensagem)
_TAINT_SINKS = {
    'pyro_exec':       (0, 'ALTO', 'tainted-exec',
                        "comando de shell construído a partir de entrada não "
                        "confiável — risco de injeção de comando (sanitize/escape "
                        "ou use uma lista de comandos permitidos)."),
    'pyro_write_file': (0, 'ALTO', 'tainted-path',
                        "caminho de arquivo vindo de entrada não confiável — "
                        "risco de path traversal / escrita arbitrária (valide e "
                        "normalize o caminho; recuse '..')."),
    'pyro_open':       (0, 'ALTO', 'tainted-open',
                        "alvo aberto no SO vindo de entrada não confiável — "
                        "não abra caminhos/URLs arbitrários."),
    'http_get':        (0, 'ALTO', 'tainted-ssrf',
                        "URL vinda de entrada não confiável — risco de SSRF "
                        "(valide o host contra uma allowlist)."),
    'http_post':       (0, 'ALTO', 'tainted-ssrf',
                        "URL vinda de entrada não confiável — risco de SSRF "
                        "(valide o host contra uma allowlist)."),
}

# ── segredos embutidos ───────────────────────────────────────
# Por valor: formatos reconhecidos de chaves reais. Por nome:
# string nao vazia atribuida a uma variavel com nome sensivel.
_SECRET_VALUE = re.compile(r'(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})')
_SECRET_NAME  = re.compile(
    r'(api[_-]?key|secret|token|senha|password|passwd|access[_-]?key|'
    r'private[_-]?key|client[_-]?secret)', re.I)


def _expr_is_tainted(expr: Any, tainted: Set[str]) -> bool:
    """True se a expressao contem (recursivamente) uma chamada a uma
    fonte de taint ou referencia uma variavel ja marcada como tainted."""
    for n in _walk(expr):
        if isinstance(n, CallExpr) and n.callee in _TAINT_SOURCES:
            return True
        if isinstance(n, Identifier) and n.name in tainted:
            return True
    return False


def _compute_taint(program) -> Set[str]:
    """Conjunto de nomes de variaveis que podem receber dado nao
    confiavel, por fixpoint monotonico sobre o programa inteiro."""
    tainted: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for n in _walk(program):
            if isinstance(n, (VarDecl, ConstDecl, Assignment, CompoundAssignment)):
                val = getattr(n, 'value', None)
                if val is not None and n.name not in tainted \
                        and _expr_is_tainted(val, tainted):
                    tainted.add(n.name)
                    changed = True
            elif isinstance(n, ForEach):
                if n.var_name not in tainted \
                        and _expr_is_tainted(n.iterable, tainted):
                    tainted.add(n.var_name)
                    changed = True
    return tainted


# ── walker generico sobre nós dataclass ─────────────────────

def _walk(node: Any):
    """Gera todos os nós Node contidos em 'node' (recursivo)."""
    if isinstance(node, Node):
        yield node
        for f in fields(node):
            yield from _walk(getattr(node, f.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


# ── regras ──────────────────────────────────────────────────

def audit_ast(program) -> List[Finding]:
    findings: List[Finding] = []
    n_unsafe = 0
    n_foreign = 0
    used_input = False

    # taint: nomes de variaveis que podem carregar dado nao confiavel
    tainted = _compute_taint(program)

    for node in _walk(program):
        # Blocos de linguagem estrangeira: superficie de injecao,
        # ignoram totalmente a instrumentacao de seguranca do Cryo.
        if isinstance(node, ForeignBlock):
            n_foreign += 1
            findings.append(Finding(
                'ALTO', 'foreign-block',
                f"Bloco estrangeiro >{node.lang}< embute código não verificado "
                f"pelo compilador — revise manualmente."))

        # Blocos 'unsafe': desligam checagens de overflow/divisao.
        if isinstance(node, SafetyBlock) and not node.safe:
            n_unsafe += 1
            findings.append(Finding(
                'MEDIO', 'unsafe-block',
                "Bloco 'unsafe' desativa a instrumentação de segurança "
                "(overflow, divisão por zero)."))

        # Dependencias externas.
        if isinstance(node, Library):
            findings.append(Finding(
                'BAIXO', 'external-lib',
                f"Dependência externa 'library >{node.name}<' — confie na origem."))
        if isinstance(node, Import):
            findings.append(Finding(
                'BAIXO', 'foreign-import',
                f"Import de runtime estrangeiro >{node.lang}<."))

        # Divisao/modulo por literal zero (erro estatico obvio).
        if isinstance(node, BinaryExpr) and node.op in ('/', '%'):
            r = node.right
            if isinstance(r, Literal) and r.kind in ('int', 'float') \
                    and float(r.value) == 0.0:
                findings.append(Finding(
                    'ALTO', 'div-by-zero',
                    f"Divisão/módulo por zero literal ('{node.op} 0')."))

        # Entrada externa nao confiavel.
        if isinstance(node, CallExpr) and node.callee in (
                'input', 'input_int', 'input_num'):
            used_input = True

        # Operacoes sensiveis (maquina / rede / LLM) — superficie de risco.
        if isinstance(node, CallExpr) and node.callee in _SENSITIVE:
            level, rule, msg = _SENSITIVE[node.callee]
            findings.append(Finding(level, rule, msg))

        # Fluxo de taint: dado nao confiavel chegando a um sink perigoso.
        if isinstance(node, CallExpr) and node.callee in _TAINT_SINKS:
            argi, level, rule, msg = _TAINT_SINKS[node.callee]
            if argi < len(node.args) and _expr_is_tainted(node.args[argi], tainted):
                findings.append(Finding(level, rule, msg))

        # Segredos embutidos no codigo-fonte.
        if isinstance(node, (VarDecl, ConstDecl)):
            val = getattr(node, 'value', None)
            if isinstance(val, Literal) and val.kind == 'string':
                sval = str(val.value)
                if _SECRET_VALUE.search(sval):
                    findings.append(Finding(
                        'ALTO', 'hardcoded-secret',
                        "Segredo embutido no código-fonte (formato de chave "
                        "reconhecido) — mova para variável de ambiente/secret."))
                elif sval and _SECRET_NAME.search(node.name):
                    findings.append(Finding(
                        'MEDIO', 'hardcoded-secret',
                        f"Possível segredo embutido em '{node.name}' — evite "
                        f"credenciais no código; use variável de ambiente."))

    if used_input:
        findings.append(Finding(
            'BAIXO', 'untrusted-input',
            "Uso de input(): trate os dados externos como não confiáveis "
            "(valide faixas, tamanhos e formatos)."))

    return findings


def format_audit(findings: List[Finding], src: str) -> str:
    order = {'ALTO': 0, 'MEDIO': 1, 'BAIXO': 2}
    findings = sorted(findings, key=lambda f: order.get(f.level, 3))
    icon = {'ALTO': '⛔', 'MEDIO': '⚠️ ', 'BAIXO': 'ℹ️ '}
    lines = [
        "",
        "╔══ Auditoria de Segurança Cryo ═══════════════════════",
        f"║ Fonte: {src}",
        f"║ Achados: {len(findings)}",
        "╚══════════════════════════════════════════════════════",
    ]
    if not findings:
        lines.append("  ✓ Nenhum padrão de risco detectado.")
    else:
        for f in findings:
            lines.append(f"  {icon.get(f.level, '')} [{f.level}] {f.rule}")
            lines.append(f"        {f.message}")
    n_alto  = sum(1 for f in findings if f.level == 'ALTO')
    n_medio = sum(1 for f in findings if f.level == 'MEDIO')
    lines.append("")
    lines.append(f"  Resumo: {n_alto} ALTO · {n_medio} MEDIO · "
                 f"{len(findings) - n_alto - n_medio} BAIXO")
    lines.append("")
    return '\n'.join(lines)
