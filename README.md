# CRYO — the language front-end

**CRYO** is the front-end: it turns `.cryo` source code into an **AST** and does
the static analysis. It is the "cold" (ergonomic, typed, safe) layer of the system.

## Contents

| File | Role |
|---|---|
| `lexer.py` | Tokenization of the source (`.cryo`) |
| `parser.py` | Syntax analysis → AST (operator precedence) |
| `ast_nodes.py` | AST node definitions |
| `security.py` | Static audit (`--audit`) |
| `examples/` | Example programs in Cryo |

## Role in the architecture

```
  .cryo ──►  CRYO (lexer → parser → AST + analysis)  ──►  AST
                                                          │
                                                          ▼
                                            PYRO (backends) → native
                                                          │
                                            Burnout (engine/CLI) orchestrates
```

CRYO is **independent** (it does not depend on PYRO or Burnout). The other two
components depend on `ast_nodes.py` from here. It will be distributed as its own
repository; PYRO and Burnout consume it as a dependency.
