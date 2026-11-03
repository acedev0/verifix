"""Tests for vulnerability and safety rule checking."""

import ast
import pytest
import z3

from verifix.verifier.smt_engine import SMTVerifier
from verifix.core.types import VerificationStatus


def test_division_by_zero_detected():
    code = """
def divide(x: int, y: int) -> int:
    return x // y
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)

    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.violation_type == "DIVISION_BY_ZERO"
    assert res.counterexample.inputs["y"] == 0


def test_safe_division_passes():
    code = """
def safe_divide(x: int, y: int) -> int:
    \"\"\"
    @requires y != 0
    \"\"\"
    return x // y
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)

    assert res.status == VerificationStatus.VERIFIED


def test_array_bounds_violation_detected():
    code = """
def get_elem(arr: list[int], idx: int) -> int:
    return arr[idx]
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)

    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.violation_type == "INDEX_OUT_OF_BOUNDS"


def test_safe_array_access():
    code = """
def safe_get_elem(arr: list[int], idx: int) -> int:
    \"\"\"
    @requires idx >= 0
    @requires idx < len(arr)
    \"\"\"
    return arr[idx]
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)

    assert res.status == VerificationStatus.VERIFIED


def test_assertion_failure_detected():
    code = """
def check_positive(x: int) -> int:
    assert x > 0
    return x
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)

    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.violation_type == "ASSERTION_FAILED"
    # Counterexample must be x <= 0
    assert res.counterexample.inputs["x"] <= 0
