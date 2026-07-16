# ============================================================
#  Cryo Compiler - Auditoria de Seguranca Estatica  (v0.4)
#
#  Percorre a AST e reporta padroes de risco antes da geracao
#  de codigo. Nao substitui o modo --safe (instrumentacao em
#  tempo de execucao); complementa-o com analise estatica.
# ============================================================
from dataclasses import dataclass, fields, is_dataclass
from typing import List, Any
from ast_nodes import (
    Node, ForeignBlock, SafetyBlock, Import, Library,
    BinaryExpr, CallExpr, Literal, VarDecl, FunctionDecl,
)


@dataclass
class Finding:
    level:   str    # 'ALTO' | 'MEDIO' | 'BAIXO'
    rule:    str
    message: str


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
