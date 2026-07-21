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

from ast_nodes import Node, Import, Library, ForeignBlock


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

    return imported


def resolve_library_lang(lib: Library, imported: Set[str]) -> str:
    """Effective language of a library: the explicit one, or the only imported one."""
    lang = _norm(lib.lang)
    if lang:
        return lang
    if len(imported) == 1:
        return next(iter(imported))
    return ""
