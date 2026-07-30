"""Cryo — The Cryo Programming Language Python Package.

Importable library and runtime for executing, compiling, and analyzing .cryo files.

Usage::

    import cryo

    # 1. Compile inline Cryo code to JavaScript / Node
    js_code = cryo.compile('fn add(int a, int b) -> int ={ return a + b; }')

    # 2. Run Cryo code dynamically
    stdout = cryo.run('print("Hello from Cryo in Python!");')

    # 3. Tokenize & Parse AST
    tokens = cryo.tokenize('int x = 10;')
    ast = cryo.parse('int x = 10;')
"""

import io
import os
import sys

# Ensure Cryo, Burnout, and Pyro directories are in sys.path
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
for _d in (_here, os.path.join(_root, "Burnout"), os.path.join(_root, "Pyro")):
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)

from lexer import Lexer, LexerError
from parser import Parser, ParseError
from compiler import compile_source, compile_file, load_ast, main as compiler_main

__version__ = "1.1.0"

def tokenize(source: str):
    """Tokenizes a .cryo source code string and returns the token list."""
    return Lexer(source).tokenize()

def parse(source: str, base_dir: str | None = None):
    """Parses a .cryo source code string and resolves imported modules."""
    return load_ast(source, base_dir or os.getcwd())

def compile(source: str, backend: str = "node", safe: bool = True, base_dir: str | None = None):
    """Compiles Cryo source code string to target code (str or bytes)."""
    return compile_source(source, backend=backend, safe=safe, base_dir=base_dir)

def run(source: str, backend: str = "node") -> str:
    """Compiles and executes a Cryo source code string, capturing stdout."""
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()
    try:
        js_code = compile(source, backend=backend)
        exec_ctx = {}
        exec(js_code, exec_ctx)
    except Exception:
        tmp_file = os.path.join(os.path.dirname(_root), "_tmp_cryo_run.cryo")
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(source)
        try:
            compile_file(tmp_file, backend=backend, run=True)
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
    finally:
        sys.stdout = old_stdout
    val = buffer.getvalue()
    return val.strip()

def cli_main():
    """CLI entry point for `cryo` command."""
    if len(sys.argv) == 1:
        print(f"Cryo Language CLI v{__version__}")
        print("Official Docs: https://victor-477.github.io/Cryo-Pyro-Documentation")
        print("\nUsage:\n  cryo <file.cryo> [--run] [--backend <node|go|c|pyro>]")
        sys.exit(0)
    
    args = sys.argv[1:]
    if "--run" not in args and not any(a.startswith("--backend") for a in args):
        args.append("--run")
    
    sys.argv = [sys.argv[0]] + args
    compiler_main()

if __name__ == "__main__":
    cli_main()
