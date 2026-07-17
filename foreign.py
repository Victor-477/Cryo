# ============================================================
#  Cryo — Verificação de blocos estrangeiros e libraries
#
#  Regra da linguagem: um bloco estrangeiro `>Lang( ... )` só é
#  válido se a linguagem `Lang` tiver sido importada no programa
#  com `import >Lang<`. O mesmo vale para `library >...<`, que
#  pertence a uma linguagem estrangeira importada.
#
#  Esta verificação é semântica (não é a instrumentação --safe):
#  roda depois do parse e antes da geração de código, e é
#  independente do backend. Levanta ForeignError na 1ª violação.
# ============================================================
from dataclasses import fields
from typing import Any, Set

from ast_nodes import Node, Import, Library, ForeignBlock


class ForeignError(Exception):
    """Uso de bloco estrangeiro ou library sem o import correspondente."""
    pass


def _walk(node: Any):
    """Gera todos os nós Node contidos em 'node' (recursivo)."""
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
    """Conjunto das linguagens estrangeiras importadas (normalizadas)."""
    langs = set()
    for n in _walk(program):
        if isinstance(n, Import):
            langs.add(_norm(n.lang))
    return langs


def verify(program) -> Set[str]:
    """Verifica blocos estrangeiros e libraries contra os `import`.

    Devolve o conjunto de linguagens importadas. Levanta ForeignError,
    com mensagem acionável, na primeira violação encontrada.
    """
    imported = collect_imports(program)

    for n in _walk(program):
        # ── blocos estrangeiros: exigem o import da sua linguagem ──
        if isinstance(n, ForeignBlock):
            lang = _norm(n.lang)
            if lang not in imported:
                raise ForeignError(
                    f"bloco estrangeiro >{n.lang}( ... ) usado sem importar a "
                    f"linguagem. Adicione 'import >{n.lang}<' antes de usar "
                    f"blocos {n.lang}."
                )

        # ── libraries: pertencem a uma linguagem estrangeira importada ──
        if isinstance(n, Library):
            lang = _norm(n.lang)
            if lang:
                if lang not in imported:
                    raise ForeignError(
                        f"library >{n.lang} {n.name}< requer 'import >{n.lang}<' "
                        f"no programa."
                    )
            else:
                # library não qualificada: infere a linguagem se houver
                # exatamente uma importada; senão exige qualificação.
                if not imported:
                    raise ForeignError(
                        f"library >{n.name}< requer uma linguagem importada "
                        f"(ex.: 'import >c<' e depois 'library >c {n.name}<')."
                    )
                if len(imported) > 1:
                    langs = ", ".join(sorted(imported))
                    raise ForeignError(
                        f"library >{n.name}< é ambígua: várias linguagens "
                        f"importadas ({langs}). Qualifique com "
                        f"'library >LANG {n.name}<'."
                    )

    return imported


def resolve_library_lang(lib: Library, imported: Set[str]) -> str:
    """Linguagem efetiva de uma library: a explícita, ou a única importada."""
    lang = _norm(lib.lang)
    if lang:
        return lang
    if len(imported) == 1:
        return next(iter(imported))
    return ""
