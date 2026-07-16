# CRYO — front-end da linguagem

O **CRYO** é o front-end: transforma código-fonte `.cryo` em uma **AST** e faz a
análise estática. É a camada "fria" (ergonômica, tipada, segura) do sistema.

## Conteúdo

| Arquivo | Papel |
|---|---|
| `lexer.py` | Tokenização do código-fonte (`.cryo`) |
| `parser.py` | Análise sintática → AST (precedência de operadores) |
| `ast_nodes.py` | Definição dos nós da AST |
| `security.py` | Auditoria estática (`--audit`) |
| `examples/` | Programas de exemplo em Cryo |

## Papel na arquitetura

```
  .cryo ──►  CRYO (lexer → parser → AST + análise)  ──►  AST
                                                          │
                                                          ▼
                                            PYRO (backends) → nativo
                                                          │
                                            Burnout (motor/CLI) orquestra
```

CRYO é **independente** (não depende de PYRO nem de Burnout). Os outros dois
componentes dependem de `ast_nodes.py` daqui. Será distribuído como repositório
próprio; PYRO e Burnout o consomem como dependência.
