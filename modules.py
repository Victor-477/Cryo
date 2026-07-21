# ============================================================
#  Cryo — Module resolution (import "file.cryo")
#
#  A Cryo program can import other .cryo files. The
#  resolver runs after parsing and BEFORE checking and
#  codegen: loads each module (relative to the file that
#  imports it), recursively, and produces a single flattened Program.
#
#  Rules (v1):
#  - A module contributes its DECLARATIONS: functions, structs,
#    enums, consts, schemas/tools, skills, `import >Lang<` and
#    `library >...<`. Top-level executable statements of an imported
#    module are ignored (only the entry program "runs").
#  - The same imported file multiple times enters ONCE
#    (deduplication by absolute path).
#  - Import cycles are detected and rejected.
#  - Name collision (two declarations with the same name coming from
#    different files) is an error, with both paths in the message.
# ============================================================
import os
from typing import Dict, List, Optional, Set

from ast_nodes import (
    Program, Node, ModuleImport, Import, Library,
    FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl,
)


class ModuleError(Exception):
    """Module resolution error (missing file, cycle, collision)."""
    pass


# declarations that a module exports
_DECLS = (FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl,
          Import, Library)


def _decl_name(n: Node) -> Optional[str]:
    return getattr(n, 'name', None) if isinstance(
        n, (FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl)) else None


def _parse_file(path: str) -> Program:
    from lexer import Lexer
    from parser import Parser
    try:
        with open(path, 'r', encoding='utf-8') as f:
            src = f.read()
    except OSError as e:
        raise ModuleError(f"[Module Error] could not read module '{path}': {e}")
    return Parser(Lexer(src).tokenize()).parse()


def resolve_modules(program: Program, base_dir: str) -> Program:
    """Resolves all `import "file.cryo"` of a Program.

    Returns a new Program with the declarations of the imported modules
    (in import order, depth-first) followed by the statements
    of the entry program. Without ModuleImport in the result.
    """
    loaded: Set[str] = set()            # abspaths already incorporated
    loading: List[str] = []             # stack for cycle detection
    origem: Dict[str, str] = {}         # declaration name -> file
    decls: List[Node] = []

    def load(path: str, importer_dir: str):
        full = os.path.normpath(os.path.join(importer_dir, path))
        full = os.path.abspath(full)
        if full in loaded:
            return                       # dedup: already incorporated
        if full in loading:
            cadeia = ' -> '.join(os.path.basename(p) for p in loading + [full])
            raise ModuleError(f"[Module Error] import cycle detected: {cadeia}")
        if not os.path.isfile(full):
            raise ModuleError(
                f"[Module Error] module not found: '{path}' (looked in {full})")
        loading.append(full)
        mod = _parse_file(full)
        mod_dir = os.path.dirname(full)
        for n in mod.statements:
            if isinstance(n, ModuleImport):
                load(n.path, mod_dir)    # nested imports, relative to the module
            elif isinstance(n, _DECLS):
                name = _decl_name(n)
                if name:
                    if name in origem and origem[name] != full:
                        raise ModuleError(
                            f"[Module Error] duplicate declaration '{name}': defined in "
                            f"{origem[name]} and in {full}")
                    origem.setdefault(name, full)
                decls.append(n)
            # executable statements of imported module: ignored
        loading.pop()
        loaded.add(full)

    # scan the entry program
    rest: List[Node] = []
    for n in program.statements:
        if isinstance(n, ModuleImport):
            load(n.path, base_dir)
        else:
            name = _decl_name(n)
            if name and name in origem:
                raise ModuleError(
                    f"[Module Error] duplicate declaration '{name}': defined in "
                    f"{origem[name]} and in the main program")
            rest.append(n)

    if not decls:
        return Program(rest) if rest is not program.statements else program
    return Program(decls + rest)
