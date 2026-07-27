"""
Example: Embedding Pyro VM in Python Application
"""
import os
import sys

# Insert embedding path
embed_py_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "Burnout", "embed", "python"))
sys.path.insert(0, embed_py_dir)

from vm import VM

print("=== LibPyro Python Embedding Example ===")

vm = VM(sandbox=False)

@vm.native("py_square")
def py_square(x: int) -> int:
    return x * x

vm.set_global("version", "1.0.0")
print(f"Global version: {vm.get_global('version')}")

vm.eval('int val = 8; print(py_square(val));')
print("Python Embedding Completed Successfully.")
