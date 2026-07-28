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
* **Independent Modules:** Supports flat, cyclic-protected, de-duplicated module imports (`import "file.cryo"`), plus namespaces (`import "geo.cryo" as geo;` → `geo::area(...)`) and `pub` visibility.
* **Generics & Traits:** `fn max_of<T>(T a, T b)` and `struct Pair<A,B>` via compile-time monomorphization; `trait`/`impl` with static dispatch and bounds (`<T: Printable>`).
* **Slices & Ranges:** `xs[a..b]`, `xs[a..=b]`, `xs[a..]`, `xs[..b]` on arrays *and* strings, and `a..b` as a first-class value (`int[] r = 0..5`).
* **Front-End in Cryo:** `>html(`, `>javascript(` and `>CSS(` blocks composed with `<script = ..., style = ...>`, emitted as a self-contained page or as `.html` + a WebAssembly binary.

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
| 📄 [`modules.py`](modules.py) | **Module Resolver** | Resolves `import "file.cryo"` and namespaced `import "x" as ns` (`ns::fn`), with dedup, cycle detection and `pub` visibility. |
| 📄 [`generics.py`](generics.py) | **Monomorphizer** | Expands `fn f<T>` and `struct Pair<A,B>` into concrete declarations at compile time, so all six backends get generics unchanged. |
| 📄 [`traits.py`](traits.py) | **Trait Lowering** | Turns `trait`/`impl` into mangled functions with static dispatch (`Person__to_str`), including generic bounds `<T: Printable>`. |
| 📄 [`foreign.py`](foreign.py) | **Foreign Verifier** | Enforces `import >Lang<` before a `>Lang( ... )` block, and resolves the `<k = v>` structure parameters that wire a block to the rest of the program. |
| 📄 [`frontend.py`](frontend.py) | **Front-End Pages** | Composes `>html(`/`>javascript(`/`>CSS(` blocks into a document — a pure AST→text pass behind `--backend frontend`. |
| 📄 [`backends.py`](backends.py) | **Backend Selection** | Analyzes the AST to pick the lightest backend covering the features a program uses (`--backend auto`). |
| 📁 [`selfhost/`](selfhost/) | **The Compiler, in Cryo** | `lexer.cryo`, `parser.cryo`, `codegen.cryo` and the `pyroc.cryo` CLI — a Cryo→Pyro compiler written in Cryo. It runs on the Pyro VM (no Python) and reaches a fixed point: the bytecode it emits is byte-identical whether it was built by the Python front-end or by itself. |
| 📁 [`examples/`](examples/) | **Examples & Demos** | Interactive examples illustrating enums, networking, calculators, Windows update simulations, and real-time graphics. |
| 📁 [`examples/fullstack/`](examples/fullstack/) | **Full-Stack Demo** | One app, Cryo on both ends: a server using the `http_serve` builtin and a browser client compiled to WebAssembly. |
| 📁 [`examples/frontend/`](examples/frontend/) | **Front-End Demo** | A web page written entirely in Cryo — html/javascript/CSS blocks composed by name, built as one vanilla file or as `.html` + `app.wasm`. |

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

## 🔪 Slices & ranges

`..` and `..=` work in three places, and all three are lowered in the **front
end**, so every backend gets them with no VM change.

```cryo
// 1. a counted loop — allocates nothing
for (int i in 0..5) { print(i); }        // 0 1 2 3 4

// 2. slicing, on arrays AND strings — bounds clamp, never throw
int[] xs = [10, 20, 30, 40, 50];
print(xs[1..3]);       // [20, 30]      end excluded
print(xs[1..=3]);      // [20, 30, 40]  end included
print(xs[2..]);        // [30, 40, 50]  to the end
print(xs[..2]);        // [10, 20]      from the start
print(xs[3..99]);      // [40, 50]      clamped, not an error
print("hello world"[0..5]);              // hello

// 3. a range as a value — the array itself
int[] r = 0..5;        // [0, 1, 2, 3, 4]
print(sum(0..5));      // 10
print((2..6)[1]);      // 3
```

A slice is a **copy** — mutating it leaves the source untouched. `..` binds
looser than arithmetic, so `0..n+1` means `0..(n+1)`. Reversed bounds (`5..2`)
give an empty array.

> Using a range *as a value* builds an array; using it in a `for` still
> compiles to a plain counted loop, so the readable loop form stays free.

---

## 🌐 Front-end pages

A `.cryo` file can *be* a web page. Wrap a foreign block in a function — that
is what gives the block a **name** — then compose the named blocks with a
structure-parameter tail:

```cryo
import >html<
import >javascript<
import >CSS<

fn fib(int n) -> int ={ if (n < 2) { return n; } return fib(n-1) + fib(n-2); }

fn styles()   ={ >CSS( body { background: #0b0b0d; color: #e8e8ea; } ) }
fn behavior() ={ >javascript( out.textContent = cryo.fib(20n).toString(); ) }

fn page() ={
  >html( <h1>Cryo</h1><p id="out">…</p> )<script = behavior, style = styles>
}
```

`script=` must name a `>javascript(` block and `style=` a `>CSS(` block —
swapping them is a compile error, not a blank page.

Two outputs, same document structure:

```bash
python Burnout/cryoc.py app.cryo --backend frontend --emit html -o web/index.html
```

- **`--emit html`** — one self-contained vanilla file. CSS and JS inlined, no
  subresource requests, opens straight from `file://`.
- **`--emit pyro`** — `index.html` **plus `app.wasm`**: the Cryo functions in
  the file, compiled to a binary the browser executes. The author's javascript
  is deferred behind a `cryo:ready` event, so `cryo.fib(…)` is always ready.
  `int` crosses into JS as `BigInt` (hence `20n`). Serve over http — `fetch`
  cannot read `file://`.

The `<k = v>` tail is not HTML-specific: it works on any foreign language
(`>Java( ... )<util = helper>`), and every `v` must name a real declaration.

Full example: [`examples/frontend/app.cryo`](examples/frontend/app.cryo).

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
  ┌───────┼───────┬────────┬────────┐
  ▼       ▼       ▼        ▼        ▼
[ Go ] [ Node ] [ C/ASM ] [ WASM ] [ .pyro ] ──► VM, or AOT to a native binary
```

There is a second, Python-free route to the same `.pyro`: the compiler in
[`selfhost/`](selfhost/), written in Cryo and running on the Pyro VM (or built
into a native `pyroc` binary). Both routes agree byte-for-byte.

```bash
build/pyrovm.exe build/pyroc.pyro app.cryo app.pyro
```

---

## 🤝 Contributing

Contributions to the frontend grammar, AST nodes, or semantic verification rules are welcome! Ensure that all additions are verified by running:
```bash
python Burnout/tests/test_smoke.py
```
All frontend files must remain 100% compliant with standard Python 3 execution.
