"""Edge case, boundary condition, and stress tests for VeriFix."""

import ast
import pytest
import z3

from verifix.verifier.symbolic_exec import SymbolicExecutor
from verifix.verifier.smt_engine import SMTVerifier
from verifix.core.engine import VeriFixEngine
from verifix.generator.mock_llm import MockLLMClient
from verifix.core.types import VerificationStatus, Specification
from verifix.patches.ast_patcher import ASTPatcher


def test_empty_function_and_no_spec():
    code = """
def do_nothing(x: int):
    pass
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED


def test_bitwise_operations():
    code = """
def bit_mask(x: int) -> int:
    \"\"\"
    @requires x >= 0
    @ensures return_val >= 0
    \"\"\"
    return (x & 15) | 1
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED


def test_nested_if_else_branches():
    code = """
def classify(x: int) -> int:
    \"\"\"
    @ensures return_val >= 0
    \"\"\"
    if x > 100:
        return 3
    elif x > 50:
        return 2
    elif x > 0:
        return 1
    else:
        return 0
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED


def test_negative_index_detected():
    code = """
def bad_lookup(arr: list[int], i: int) -> int:
    \"\"\"
    @requires i < len(arr)
    \"\"\"
    # Vulnerability: i could be negative! (In Python -1 wraps around, but in bounds safety it must be checked)
    return arr[i]
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample.violation_type == "INDEX_OUT_OF_BOUNDS"
    assert res.counterexample.inputs["i"] < 0


def test_max_iterations_exhausted():
    """Verify behavior when LLM proposals never verify."""
    buggy_code = """
def bad_math(x: int) -> int:
    \"\"\"
    @ensures return_val > x
    \"\"\"
    return x - 1
"""
    flawed_patch = """
```python
def bad_math(x: int) -> int:
    return x - 2
```
"""
    mock_llm = MockLLMClient(responses=[flawed_patch, flawed_patch, flawed_patch])
    engine = VeriFixEngine(llm_client=mock_llm, verifier=SMTVerifier(), max_iterations=3)

    res = engine.repair(buggy_code, function_name="bad_math")
    assert res.success is False
    assert res.iterations == 3
    assert res.verified_patch is None


def test_multi_function_target_selection():
    code = """
def func_a(x: int) -> int:
    return x // 0

def func_b(x: int) -> int:
    \"\"\"
    @ensures return_val == x + 1
    \"\"\"
    return x + 1
"""
    verifier = SMTVerifier()
    res_b = verifier.verify_source(code, function_name="func_b")
    assert res_b.status == VerificationStatus.VERIFIED

    res_a = verifier.verify_source(code, function_name="func_a")
    assert res_a.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res_a.counterexample.violation_type == "DIVISION_BY_ZERO"


def test_ast_patcher_splicing():
    module_code = '''
import math

def func_one(x: int) -> int:
    return x * 2

def target_func(a: int, b: int) -> int:
    return a - b

def func_three(y: int) -> int:
    return y + 10
'''
    new_target = '''
def target_func(a: int, b: int) -> int:
    if a < b:
        return b - a
    return a - b
'''
    patched_module = ASTPatcher.replace_function_in_source(module_code, "target_func", new_target)
    assert "func_one" in patched_module
    assert "func_three" in patched_module
    assert "if a < b:" in patched_module
    # Check that patched module parses as valid Python
    tree = ast.parse(patched_module)
    assert len(tree.body) == 4  # import + 3 functions


def test_ast_patcher_class_method_indentation():
    """Verify that replacing a method inside a class maintains valid indentation and AST."""
    class_code = '''
class BankService:
    def __init__(self, name: str):
        self.name = name

    def withdraw(self, balance: int, amount: int) -> int:
        return balance - amount

    def deposit(self, balance: int, amount: int) -> int:
        return balance + amount
'''
    new_method = '''
def withdraw(self, balance: int, amount: int) -> int:
    if amount <= balance:
        return balance - amount
    return balance
'''
    patched = ASTPatcher.replace_function_in_source(class_code, "withdraw", new_method)
    assert "if amount <= balance:" in patched
    # Critical: parsing must not raise IndentationError!
    tree = ast.parse(patched)
    cls_node = [n for n in tree.body if isinstance(n, ast.ClassDef)][0]
    methods = [n.name for n in cls_node.body if isinstance(n, ast.FunctionDef)]
    assert methods == ["__init__", "withdraw", "deposit"]


def test_cli_typing_inspection():
    """Verify that cli functions can be inspected with get_type_hints without NameError."""
    import typing
    import verifix.cli
    hints = typing.get_type_hints(verifix.cli.run_patch)
    assert "return" in hints

