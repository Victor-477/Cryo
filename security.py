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
    CastExpr, PermissionsDecl,   # 11.15
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
    # 11.15 — the natives added in 11.6-11.8 are untrusted sources too. Their
    # absence meant a program built entirely on the newer I/O audited clean.
    'read_file', 'exec',
    'http_accept',       # method/path/query/body/headers, all attacker-supplied
}
# Deliberately NOT sources: `env` and `args` are how an OPERATOR configures a
# program, and `asset` is data the author embedded. Treating them as untrusted
# made every configured path a HIGH finding on correct code — and an audit
# that is noisy on your own correct code is an audit people switch off, which
# costs more than the recall it buys. They remain covered where it matters:
# a path from any source is still confined by the capability policy (11.11).

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
    # 11.15 — the current filesystem/process natives, which the table predated
    'exec':            (0, 'HIGH', 'tainted-exec',
                        "shell command built from untrusted input — command "
                        "injection risk (use an allowlist, and grant `exec` "
                        "narrowly in the permissions block)."),
    'write_file':      (0, 'HIGH', 'tainted-path',
                        "file path coming from untrusted input — arbitrary "
                        "write risk (normalize the path and confine it with "
                        "`write` in the permissions block)."),
    'write_file_atomic': (0, 'HIGH', 'tainted-path',
                        "file path coming from untrusted input — arbitrary "
                        "write risk (normalize and confine the path)."),
    'write_bytes':     (0, 'HIGH', 'tainted-path',
                        "file path coming from untrusted input — arbitrary "
                        "write risk (normalize and confine the path)."),
    'delete_file':     (0, 'HIGH', 'tainted-path',
                        "deletion path coming from untrusted input — a "
                        "traversal here destroys data rather than leaking it."),
    'make_dir':        (0, 'MEDIUM', 'tainted-path',
                        "directory path coming from untrusted input — "
                        "normalize and confine it."),
    'read_file':       (0, 'HIGH', 'tainted-path',
                        "file path coming from untrusted input — path "
                        "traversal / arbitrary read risk (confine it with "
                        "`read` in the permissions block)."),
    'http_serve':      (1, 'HIGH', 'tainted-path',
                        "served directory coming from untrusted input — this "
                        "publishes whatever that path resolves to."),
}

# ── 11.15: time-of-check / time-of-use ──────────────────────
_TOCTOU_CHECKS = {'file_exists', 'is_dir'}
_TOCTOU_USERS = {'read_file', 'write_file', 'write_file_atomic',
                 'write_bytes', 'delete_file'}

# ── 11.15: unbounded allocation ─────────────────────────────
# callee -> index of the argument that sizes the result. A count taken from
# untrusted input turns one request into an out-of-memory abort.
_ALLOC_SIZED_BY = {
    'repeat': 1, 'pad_start': 1, 'pad_end': 1,
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
    """Variable names that can receive untrusted data, by monotonic fixpoint.

    Scoped by its CALLER: pass one function's body, not the whole program.
    Taint is tracked by name, so mixing scopes makes a variable called `path`
    in one function taint an unrelated `path` in another — which is exactly
    the false positive this scoping exists to avoid.
    """
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

    # Taint, computed PER SCOPE. Each function gets its own set, and the
    # top-level statements another; see _compute_taint for why sharing one
    # set across the program produces false positives.
    scopes = []
    top_level = [n for n in getattr(program, 'statements', [])
                 if not isinstance(n, FunctionDecl)]
    scopes.append((top_level, _compute_taint(top_level)))
    for n in _walk(program):
        if isinstance(n, FunctionDecl):
            body = n.body or []
            # a parameter is untrusted only if the analysis can see it being
            # assigned untrusted data; without inter-procedural tracking we
            # do not guess, which trades some recall for far less noise
            scopes.append((body, _compute_taint(body)))

    tainted: Set[str] = set()

    # 11.15 — paths that were tested with file_exists()/is_dir(). Collected in
    # a pre-pass so a use is flagged wherever it appears relative to the check.
    checked_paths: Set[str] = set()
    for n in _walk(program):
        if isinstance(n, CallExpr) and n.callee in _TOCTOU_CHECKS                 and n.args and isinstance(n.args[0], Identifier):
            checked_paths.add(n.args[0].name)

    for scope_nodes, scope_taint in scopes:
      tainted = scope_taint
      for node in _walk(scope_nodes):
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

        # 11.15 — unvalidated deserialization. json_decode of untrusted data
        # produces a value shaped however the ATTACKER chose; `as T` does not
        # verify it (the cast is an assertion, not a check), so every field
        # read afterwards is attacker-controlled.
        if isinstance(node, CastExpr):
            inner = node.expr
            if (isinstance(inner, CallExpr) and inner.callee == 'json_decode'
                    and inner.args and _expr_is_tainted(inner.args[0], tainted)):
                findings.append(Finding(
                    # MEDIUM, not HIGH: it is a real issue, but the usual case
                    # is a program reading its own data file, where an attacker
                    # needs write access first. --strict gates on HIGH, so
                    # rating this HIGH would break CI for ordinary code.
                    'MEDIUM', 'unvalidated-deserialization',
                    f"json_decode() of untrusted data cast to "
                    f"'{node.target_type}' — `as T` asserts a shape, it does "
                    f"not verify one. Check the fields you rely on (presence, "
                    f"type and range) before using them."))

        # 11.15 — unbounded allocation from input. A size taken from untrusted
        # data turns a single request into an out-of-memory abort.
        if isinstance(node, CallExpr) and node.callee in _ALLOC_SIZED_BY:
            argi = _ALLOC_SIZED_BY[node.callee]
            if argi < len(node.args) and _expr_is_tainted(node.args[argi], tainted):
                findings.append(Finding(
                    'MEDIUM', 'unbounded-allocation',
                    f"{node.callee}() sized by untrusted input — clamp the "
                    f"count to a maximum before allocating, or a single "
                    f"request can exhaust memory."))

        # 11.15 — permissions that grant a whole class. Legal, and sometimes
        # correct, but it defeats the point of declaring them.
        if isinstance(node, PermissionsDecl):
            for cap, values in node.grants.items():
                if '*' in values:
                    lvl = 'HIGH' if cap in ('exec', 'write') else 'MEDIUM'
                    findings.append(Finding(
                        lvl, 'broad-permission',
                        f"permissions: '{cap} = \"*\"' grants the whole "
                        f"class — name the paths, hosts or binaries actually "
                        f"needed, or the declaration proves nothing."))

        # 11.15 — time-of-check / time-of-use. `file_exists(p)` proves nothing
        # about the moment `p` is opened: between the two, the path can be
        # replaced (a symlink, a different file). Heuristic by design — it
        # matches on the same variable NAME, so it reports the shape of the
        # mistake rather than proving it.
        if isinstance(node, CallExpr) and node.callee in _TOCTOU_USERS:
            if node.args and isinstance(node.args[0], Identifier)                     and node.args[0].name in checked_paths:
                findings.append(Finding(
                    'LOW', 'toctou-path',
                    f"'{node.args[0].name}' is used by {node.callee}() after a "
                    f"file_exists()/is_dir() check — the check does not hold "
                    f"at the moment of use. Act on the operation's own result "
                    f"instead of testing first."))

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
