# ============================================================
#  Cryo — Verification of foreign blocks and libraries
#
#  Language rule: a foreign block `>Lang( ... )` is only
#  valid if the language `Lang` has been imported in the program
#  with `import >Lang<`. The same goes for `library >...<`, which
#  belongs to an imported foreign language.
#
#  This check is semantic (it is not --safe instrumentation):
#  it runs after parse and before code generation, and is
#  backend independent. Raises ForeignError on the 1st violation.
# ============================================================
from dataclasses import fields
from typing import Any, Set

from ast_nodes import (
    Node, Import, Library, ForeignBlock, FunctionDecl, VarDecl, ConstDecl,
)


class ForeignError(Exception):
    """Use of foreign block or library without the corresponding import."""
    pass


def _walk(node: Any):
    """Yields all Node elements contained in 'node' (recursive)."""
    if isinstance(node, Node):
        yield node
        for f in fields(node):
            yield from _walk(getattr(node, f.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


def _norm(lang: str) -> str:
    return (lang or "").strip().lower()


def collect_imports(program) -> Set[str]:
    """Set of imported foreign languages (normalized)."""
    langs = set()
    for n in _walk(program):
        if isinstance(n, Import):
            langs.add(_norm(n.lang))
    return langs


def verify(program) -> Set[str]:
    """Checks foreign blocks and libraries against `import`.

    Returns the set of imported languages. Raises ForeignError,
    with actionable message, on the first violation found.
    """
    imported = collect_imports(program)

    for n in _walk(program):
        # ── foreign blocks: require import of their language ──
        if isinstance(n, ForeignBlock):
            lang = _norm(n.lang)
            if lang not in imported:
                raise ForeignError(
                    f"foreign block >{n.lang}( ... ) used without importing the "
                    f"language. Add 'import >{n.lang}<' before using "
                    f"{n.lang} blocks."
                )

        # ── libraries: belong to an imported foreign language ──
        if isinstance(n, Library):
            lang = _norm(n.lang)
            if lang:
                if lang not in imported:
                    raise ForeignError(
                        f"library >{n.lang} {n.name}< requires 'import >{n.lang}<' "
                        f"in the program."
                    )
            else:
                # unqualified library: infers the language if there is
                # exactly one imported; otherwise requires qualification.
                if not imported:
                    raise ForeignError(
                        f"library >{n.name}< requires an imported language "
                        f"(e.g.: 'import >c<' and then 'library >c {n.name}<')."
                    )
                if len(imported) > 1:
                    langs = ", ".join(sorted(imported))
                    raise ForeignError(
                        f"library >{n.name}< is ambiguous: multiple languages "
                        f"imported ({langs}). Qualify with "
                        f"'library >LANG {n.name}<'."
                    )

    verify_struct_params(program)
    return imported


def _declared_names(program) -> Set[str]:
    """Every top-level name a structure parameter is allowed to point at."""
    names = set()
    for n in _walk(program):
        if isinstance(n, (FunctionDecl, VarDecl, ConstDecl)):
            nm = getattr(n, 'name', None)
            if nm:
                names.add(nm)
    return names


def verify_struct_params(program) -> None:
    """Roadmap 10.12 — every `<k = v>` on a foreign block must resolve.

    A structure parameter wires a foreign block to something outside it, so a
    typo in `v` would otherwise be discovered only by the *foreign* toolchain
    (javac, gcc, the browser) — long after Cryo could have given a useful
    message, and in a language the Cryo author may not read. Checking it here
    keeps the error in Cryo's own terms.
    """
    declared = _declared_names(program)
    for n in _walk(program):
        if not isinstance(n, ForeignBlock) or not n.params:
            continue
        for key, val in n.params:
            if val not in declared:
                # 11.24 — was a three-character prefix match, which finds
                # `total` from `totl` but not `length` from `lenght`. The
                # shared helper is edit-distance based and catches both.
                import diagnostics as _dx
                h = _dx.hint(val, declared)
                hint = f" {h[0].upper()}{h[1:]}" if h else ""
                raise ForeignError(
                    f">{n.lang}( ... )<{key} = {val}> refers to '{val}', which "
                    f"is not declared in this program. A structure parameter "
                    f"must name a function, variable or constant that exists."
                    f"{hint}"
                )


def resolve_library_lang(lib: Library, imported: Set[str]) -> str:
    """Effective language of a library: the explicit one, or the only imported one."""
    lang = _norm(lib.lang)
    if lang:
        return lang
    if len(imported) == 1:
        return next(iter(imported))
    return ""
