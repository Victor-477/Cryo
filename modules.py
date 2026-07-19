# ============================================================
#  Cryo — Resolução de módulos (import "arquivo.cryo")
#
#  Um programa Cryo pode importar outros arquivos .cryo. O
#  resolvedor roda depois do parse e ANTES da verificação e do
#  codegen: carrega cada módulo (relativo ao arquivo que o
#  importa), recursivamente, e produz um único Program achatado.
#
#  Regras (v1):
#  - Um módulo contribui com as suas DECLARAÇÕES: funções, structs,
#    enums, consts, schemas/tools, skills, `import >Lang<` e
#    `library >...<`. Statements executáveis de topo de um módulo
#    importado são ignorados (só o programa de entrada "roda").
#  - O mesmo arquivo importado várias vezes entra UMA vez
#    (deduplicação por caminho absoluto).
#  - Ciclos de import são detectados e rejeitados.
#  - Colisão de nome (duas declarações com o mesmo nome vindas de
#    arquivos diferentes) é erro, com os dois caminhos na mensagem.
# ============================================================
import os
from typing import Dict, List, Optional, Set

from ast_nodes import (
    Program, Node, ModuleImport, Import, Library,
    FunctionDecl, StructDecl, EnumDecl, ConstDecl, SkillDecl,
)


class ModuleError(Exception):
    """Erro de resolução de módulos (arquivo ausente, ciclo, colisão)."""
    pass


# declarações que um módulo exporta
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
        raise ModuleError(f"não foi possível ler o módulo '{path}': {e}")
    return Parser(Lexer(src).tokenize()).parse()


def resolve_modules(program: Program, base_dir: str) -> Program:
    """Resolve todos os `import "arquivo.cryo"` de um Program.

    Devolve um novo Program com as declarações dos módulos importados
    (em ordem de import, profundidade primeiro) seguidas dos statements
    do programa de entrada. Sem ModuleImport no resultado.
    """
    loaded: Set[str] = set()            # abspaths já incorporados
    loading: List[str] = []             # pilha p/ detecção de ciclo
    origem: Dict[str, str] = {}         # nome da declaração -> arquivo
    decls: List[Node] = []

    def load(path: str, importer_dir: str):
        full = os.path.normpath(os.path.join(importer_dir, path))
        full = os.path.abspath(full)
        if full in loaded:
            return                       # dedup: já incorporado
        if full in loading:
            cadeia = ' -> '.join(os.path.basename(p) for p in loading + [full])
            raise ModuleError(f"ciclo de imports detectado: {cadeia}")
        if not os.path.isfile(full):
            raise ModuleError(
                f"módulo não encontrado: '{path}' (procurado em {full})")
        loading.append(full)
        mod = _parse_file(full)
        mod_dir = os.path.dirname(full)
        for n in mod.statements:
            if isinstance(n, ModuleImport):
                load(n.path, mod_dir)    # imports aninhados, relativo ao módulo
            elif isinstance(n, _DECLS):
                nome = _decl_name(n)
                if nome:
                    if nome in origem and origem[nome] != full:
                        raise ModuleError(
                            f"declaração duplicada '{nome}': definida em "
                            f"{origem[nome]} e em {full}")
                    origem.setdefault(nome, full)
                decls.append(n)
            # statements executáveis de módulo importado: ignorados
        loading.pop()
        loaded.add(full)

    # varre o programa de entrada
    rest: List[Node] = []
    for n in program.statements:
        if isinstance(n, ModuleImport):
            load(n.path, base_dir)
        else:
            nome = _decl_name(n)
            if nome and nome in origem:
                raise ModuleError(
                    f"declaração duplicada '{nome}': definida em "
                    f"{origem[nome]} e no programa principal")
            rest.append(n)

    if not decls:
        return Program(rest) if rest is not program.statements else program
    return Program(decls + rest)
