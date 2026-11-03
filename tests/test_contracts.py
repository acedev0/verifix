"""Tests for contract extraction from docstrings, decorators, and type hints."""

import ast
from verifix.core.contract import ContractExtractor, requires, ensures, invariant
from verifix.core.types import Specification


def test_extract_docstring_contracts():
    code = '''
def calculate(x: int, y: int) -> int:
    """
    @requires x >= 0
    @requires y > 0
    @ensures return_val >= x
    @invariant x + y > 0
    """
    return x + y
'''
    fn_node, spec = ContractExtractor.from_source(code)
    assert "x >= 0" in spec.preconditions
    assert "y > 0" in spec.preconditions
    assert "return_val >= x" in spec.postconditions
    assert "x + y > 0" in spec.invariants
    assert spec.param_types["x"] == "int"
    assert spec.param_types["y"] == "int"
    assert spec.return_type == "int"


def test_extract_alt_docstring_format():
    code = '''
def compute(n: int) -> int:
    """
    Requires: n > 1
    Ensures: return_val > 0
    """
    return n * 2
'''
    fn_node, spec = ContractExtractor.from_source(code)
    assert "n > 1" in spec.preconditions
    assert "return_val > 0" in spec.postconditions


def test_extract_decorator_contracts():
    code = '''
@requires("a > 0")
@ensures("return_val > a")
def bump(a: int) -> int:
    return a + 1
'''
    fn_node, spec = ContractExtractor.from_source(code)
    assert "a > 0" in spec.preconditions
    assert "return_val > a" in spec.postconditions
