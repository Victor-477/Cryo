# ❄️ Cryo — The Ergonomic, Safe, & Strongly-Typed Frontend

[![Language](https://img.shields.io/badge/Language-Cryo-blue.svg)](https://github.com/Victor-477/Pyro_Cryo)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Compiler](https://img.shields.io/badge/Compiler-Burnout-orange.svg)](../Burnout)

**Cryo** is the frontend and specification layer of the language system. It represents the "cold" (typed, safe, ergonomic) frontend, translating `.cryo` source files into a strongly-typed Abstract Syntax Tree (AST) while performing strict lexical, syntax, and semantic checks.

---

## 🚀 Key Features

* **Strict Type Inference & Checking:** Strong static typing checking with supports for primitive types (`int`, `number`, `string`, `bool`), optionals (`T?`), and structured records (`struct`).
* **Algebraic Data Types (ADTs) & Pattern Matching:** Powerful algebraic enums with positional data fields (e.g., `Result { Ok(int), Err(string), Empty }`) and pattern matching statements (`match`).
* **First-Class Functions & Lambdas:** Functions as first-class citizens, complete with lambdas/closures (`(int x) -> int => x * 2`).
* **Error Propagation & Handling:** Simple `try/catch` exception blocks and the `?` error propagation operator (similar to Rust/Swift).
* **Static Taint Analysis & Security Audit:** Built-in vulnerability scanner checking for shell injections (`tainted-exec`), path traversals (`tainted-path`), SSRF (`tainted-ssrf`), and hardcoded secrets.
* **Independent Modules:** Supports flat, cyclic-protected, de-duplicated module imports (`import "file.cryo"`).

---

## 📂 Directory Layout

| File | Component | Responsibility |
| :--- | :--- | :--- |
| 📄 [`lexer.py`](lexer.py) | **Lexer** | Tokenizes Cryo source code into structured token sequences, detecting numeric literals (hex, bin, octal), string templates, and comments. |
| 📄 [`parser.py`](parser.py) | **Parser** | Builds a robust AST from token sequences, implementing strict operator precedence parsing. |
| 📄 [`ast_nodes.py`](ast_nodes.py) | **AST Nodes** | Defines the structured representation of declarations, statements, and expressions. |
| 📄 [`semantic.py`](semantic.py) | **Semantic Analyzer** | Resolves scoping, verifies symbol declarations, arity, and type signatures, and ensures exhaustiveness of match patterns. |
| 📄 [`security.py`](security.py) | **Security Auditor** | Analyzes the AST using data-flow (taint) algorithms to detect security threats and sensitive operation leaks. |
| 📄 [`format.py`](format.py) | **Formatter** | Implements an idempotent, safe, and customizable source code formatter (`cryoc fmt`). |
| 📁 [`examples/`](examples/) | **Examples & Demos** | Interactive examples illustrating enums, networking, calculators, Windows update simulations, and real-time graphics. |

---

## 🎨 Cryo Syntax Showcase

Here is a snippet showing some of the advanced features supported by Cryo's parser and syntax system:

```cryo
// Algebraic Data Type (ADT)
enum Result {
    Ok(int),
    Err(string),
    Empty
}

// Struct Definition
struct User {
    string name;
    int id;
}

// Function with optional return and pattern matching
fn process(Result r) -> string? ={
    match r {
        Ok(val) => {
            print("Successfully processed value: " + val);
            return "Success";
        }
        Err(msg) => {
            print("Error encountered: " + msg);
            return null;
        }
        Empty => {
            return "No data";
        }
    }
}
```

---

## ⚙️ Architecture Integration

Cryo serves as the ergonomic entry point. It has no dependencies on the backend VM or compiler orchestration modules:

```text
  [ .cryo Source ]
          │
          ▼
   ┌──────────────┐
   │  Cryo Lang   │  ──► (Lexer → Parser → Semantic check → AST)
   └──────────────┘
          │
          ▼
     [ AST Node ]
          │
          ▼
   ┌──────────────┐
   │   Burnout    │  ──► (Orchestrates CodeGen backends)
   └──────────────┘
          │
  ┌───────┼───────┐
  ▼       ▼       ▼
[ Go ] [ Node ] [ C / ASM ] ──► (Native targets)
```

---

## 🤝 Contributing

Contributions to the frontend grammar, AST nodes, or semantic verification rules are welcome! Ensure that all additions are verified by running:
```bash
python Burnout/tests/test_smoke.py
```
All frontend files must remain 100% compliant with standard Python 3 execution.
