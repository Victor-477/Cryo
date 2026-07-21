# ============================================================
#  Cryo Compiler - Static Security Audit  (v0.5)
#
#  Traverses the AST and reports risk patterns before code
#  generation. Does not replace --safe mode (runtime instrumentation);
#  complements it with static analysis.
#
#  v0.5: taint analysis (untrusted source -> dangerous sink:
#  command injection, path traversal, SSRF) and detection of
#  hardcoded secrets in the source code.
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
    level:   str    # 'HIGH' | 'MEDIUM' | 'LOW'
    rule:    str
    message: str


# ── sensitive operations (callee -> level, rule, message) ──
_SENSITIVE = {
    'pyro_exec': ('HIGH', 'command-exec',
                  "pyro_exec() executes an arbitrary shell command — "
                  "never pass untrusted input as a command."),
    'pyro_write_file': ('MEDIUM', 'file-write',
                        "pyro_write_file() writes to an arbitrary path on disk — "
                        "validate the path (avoid path traversal)."),
    'pyro_open': ('MEDIUM', 'shell-open',
                  "pyro_open() opens a file/URL in the default OS app — "
                  "do not open targets from untrusted input."),
    'pyro_exit': ('LOW', 'process-exit',
                  "pyro_exit() terminates the process."),
    'http_get':  ('MEDIUM', 'net-egress',
                  "http_get() makes a network request — SSRF risk if the URL "
                  "comes from untrusted input."),
    'http_post': ('MEDIUM', 'net-egress',
                  "http_post() sends data over the network — confirm destination and content."),
    'llm':   ('LOW', 'llm-egress',
              "llm() sends the prompt to an external endpoint (CRYO_LLM_URL)."),
    'agent': ('LOW', 'llm-egress',
              "agent() exchanges data with an external LLM and executes tools in a loop."),
}


# ── taint analysis (untrusted data flow) ─────────
#
# Sources: builtins that produce untrusted data (user input,
# network, environment, LLM output). Sinks: builtins that, if
# fed with untrusted data, become a concrete risk
# (command injection, path traversal, SSRF). The analysis is
# intraprocedural-approximate (fixpoint over variable names in the
# entire program), conservative and does not replace manual review.
_TAINT_SOURCES = {
    'input', 'input_int', 'input_num', 'pyro_read',
    'pyro_args', 'pyro_env',
    'http_get', 'http_post',
    'llm', 'agent',
}

# callee -> (arg_index, level, rule, message)
_TAINT_SINKS = {
    'pyro_exec':       (0, 'HIGH', 'tainted-exec',
                        "shell command built from untrusted input "
                        "— command injection risk (sanitize/escape "
                        "or use an allowlist of permitted commands)."),
    'pyro_write_file': (0, 'HIGH', 'tainted-path',
                        "file path coming from untrusted input — "
                        "path traversal / arbitrary write risk (validate and "
                        "normalize the path; reject '..')."),
    'pyro_open':       (0, 'HIGH', 'tainted-open',
                        "target opened in OS from untrusted input — "
                        "do not open arbitrary paths/URLs."),
    'http_get':        (0, 'HIGH', 'tainted-ssrf',
                        "URL coming from untrusted input — SSRF risk "
                        "(validate the host against an allowlist)."),
    'http_post':       (0, 'HIGH', 'tainted-ssrf',
                        "URL coming from untrusted input — SSRF risk "
                        "(validate the host against an allowlist)."),
}

# ── hardcoded secrets ───────────────────────────────────────
# By value: recognized formats of real keys. By name:
# non-empty string assigned to a variable with a sensitive name.
_SECRET_VALUE = re.compile(r'(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})')
_SECRET_NAME  = re.compile(
    r'(api[_-]?key|secret|token|senha|password|passwd|access[_-]?key|'
    r'private[_-]?key|client[_-]?secret)', re.I)


def _expr_is_tainted(expr: Any, tainted: Set[str]) -> bool:
    """True if the expression contains (recursively) a call to a
    taint source or references a variable already marked as tainted."""
    for n in _walk(expr):
        if isinstance(n, CallExpr) and n.callee in _TAINT_SOURCES:
            return True
        if isinstance(n, Identifier) and n.name in tainted:
            return True
    return False


def _compute_taint(program) -> Set[str]:
    """Set of variable names that can receive untrusted data
    , via monotonic fixpoint over the entire program."""
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


# ── generic walker over dataclass nodes ─────────────────────

def _walk(node: Any):
    """Yields all Node elements contained in 'node' (recursive)."""
    if isinstance(node, Node):
        yield node
        for f in fields(node):
            yield from _walk(getattr(node, f.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


# ── rules ──────────────────────────────────────────────────

def audit_ast(program) -> List[Finding]:
    findings: List[Finding] = []
    n_unsafe = 0
    n_foreign = 0
    used_input = False

    # taint: variable names that can carry untrusted data
    tainted = _compute_taint(program)

    for node in _walk(program):
        # Foreign language blocks: injection surface,
        # completely ignore Cryo's security instrumentation.
        if isinstance(node, ForeignBlock):
            n_foreign += 1
            findings.append(Finding(
                'HIGH', 'foreign-block',
                f"Foreign block >{node.lang}< embeds unverified code "
                f"by the compiler — review manually."))

        # Unsafe blocks: turn off overflow/division checks.
        if isinstance(node, SafetyBlock) and not node.safe:
            n_unsafe += 1
            findings.append(Finding(
                'MEDIUM', 'unsafe-block',
                "Unsafe block disables security instrumentation "
                "(overflow, division by zero)."))

        # External dependencies.
        if isinstance(node, Library):
            findings.append(Finding(
                'LOW', 'external-lib',
                f"External dependency 'library >{node.name}<' — trust the origin."))
        if isinstance(node, Import):
            findings.append(Finding(
                'LOW', 'foreign-import',
                f"Foreign runtime import >{node.lang}<."))

        # Division/modulo by literal zero (obvious static error).
        if isinstance(node, BinaryExpr) and node.op in ('/', '%'):
            r = node.right
            if isinstance(r, Literal) and r.kind in ('int', 'float') \
                    and float(r.value) == 0.0:
                findings.append(Finding(
                    'HIGH', 'div-by-zero',
                    f"Division/modulo by literal zero ('{node.op} 0')."))

        # Untrusted external input.
        if isinstance(node, CallExpr) and node.callee in (
                'input', 'input_int', 'input_num'):
            used_input = True

        # Sensitive operations (machine / network / LLM) — risk surface.
        if isinstance(node, CallExpr) and node.callee in _SENSITIVE:
            level, rule, msg = _SENSITIVE[node.callee]
            findings.append(Finding(level, rule, msg))

        # Taint flow: untrusted data reaching a dangerous sink.
        if isinstance(node, CallExpr) and node.callee in _TAINT_SINKS:
            argi, level, rule, msg = _TAINT_SINKS[node.callee]
            if argi < len(node.args) and _expr_is_tainted(node.args[argi], tainted):
                findings.append(Finding(level, rule, msg))

        # Hardcoded secrets in the source code.
        if isinstance(node, (VarDecl, ConstDecl)):
            val = getattr(node, 'value', None)
            if isinstance(val, Literal) and val.kind == 'string':
                sval = str(val.value)
                if _SECRET_VALUE.search(sval):
                    findings.append(Finding(
                        'HIGH', 'hardcoded-secret',
                        "Hardcoded secret in the source code (recognized key format) "
                        "— move to environment variable/secret."))
                elif sval and _SECRET_NAME.search(node.name):
                    findings.append(Finding(
                        'MEDIUM', 'hardcoded-secret',
                        f"Possible hardcoded secret in '{node.name}' — avoid "
                        f"credentials in code; use environment variables."))

    if used_input:
        findings.append(Finding(
            'LOW', 'untrusted-input',
            "Use of input(): treat external data as untrusted "
            "(validate ranges, sizes and formats)."))

    return findings


def format_audit(findings: List[Finding], src: str) -> str:
    order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    findings = sorted(findings, key=lambda f: order.get(f.level, 3))
    icon = {'HIGH': '⛔', 'MEDIUM': '⚠️ ', 'LOW': 'ℹ️ '}
    lines = [
        "",
        "╔══ Cryo Security Audit ═══════════════════════",
        f"║ Source: {src}",
        f"║ Findings: {len(findings)}",
        "╚══════════════════════════════════════════════════════",
    ]
    if not findings:
        lines.append("  ✓ No risk pattern detected.")
    else:
        for f in findings:
            lines.append(f"  {icon.get(f.level, '')} [{f.level}] {f.rule}")
            lines.append(f"        {f.message}")
    n_alto  = sum(1 for f in findings if f.level == 'HIGH')
    n_medio = sum(1 for f in findings if f.level == 'MEDIUM')
    lines.append("")
    lines.append(f"  Summary: {n_alto} HIGH · {n_medio} MEDIUM · "
                 f"{len(findings) - n_alto - n_medio} LOW")
    lines.append("")
    return '\n'.join(lines)
