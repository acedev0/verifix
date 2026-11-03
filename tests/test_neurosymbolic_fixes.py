"""Comprehensive verification tests for neurosymbolic edge cases and fixes.

Tests cover:
- NoneType dereference detection and repair
- 32-bit integer overflow detection and bounds
- Modulo operator correctness vs addition bug
- Signed BitVector to Int conversion (~0 == -1)
- Empty collection detection and reporting
- Symbolic for-loop unrolling and off-by-one detection
- Strict postcondition validation (no silent pass on invalid specs)
- Subscript AugAssign handling
- End-to-end CEGIS loop repair for NoneType dereferences
"""

import ast
import pytest
import z3

from verifix.verifier.smt_engine import SMTVerifier
from verifix.verifier.symbolic_exec import SymbolicExecutor
from verifix.core.engine import VeriFixEngine
from verifix.core.types import Specification, VerificationStatus
from verifix.generator.mock_llm import ScriptedRepairClient, MockLLMClient


def test_modulo_operator_not_addition():
    """Verify that x % 2 produces x % 2 and NOT x + 2."""
    code = """
def mod_two(x: int) -> int:
    \"\"\"
    @requires x == 7
    @ensures return_val == 1
    \"\"\"
    return x % 2
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED
    assert res.is_verified


def test_bitwise_not_signed():
    """Verify that ~0 evaluates to -1 rather than unsigned 4294967295."""
    code = """
def bit_not(x: int) -> int:
    \"\"\"
    @requires x == 0
    @ensures return_val == -1
    \"\"\"
    return ~x
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED


def test_none_dereference_len_detected():
    """Verify that len(s) on Optional[list] flags NONE_DEREFERENCE with s=None."""
    code = """
from typing import Optional

def get_len(s: Optional[list[int]]) -> int:
    return len(s)
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.violation_type == "NONE_DEREFERENCE"
    assert res.counterexample.inputs["s"] is None


def test_none_dereference_subscript_detected():
    """Verify that arr[0] on Optional[list] flags NONE_DEREFERENCE."""
    code = """
from typing import Optional

def get_first(arr: Optional[list[int]]) -> int:
    return arr[0]
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.violation_type == "NONE_DEREFERENCE"
    assert res.counterexample.inputs["arr"] is None


def test_none_dereference_attribute_detected():
    """Verify that node.val on Optional object flags NONE_DEREFERENCE."""
    code = """
from typing import Optional, Any

def get_field(node: Optional[Any]) -> int:
    return node.val
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.violation_type == "NONE_DEREFERENCE"
    assert res.counterexample.inputs["node"] is None


def test_none_dereference_safe_check_passes():
    """Verify that guarding against None with 'if s is None' formally verifies."""
    code = """
from typing import Optional

def safe_len(s: Optional[list[int]]) -> int:
    \"\"\"
    @ensures return_val >= 0
    \"\"\"
    if s is None:
        return 0
    return len(s)
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED


def test_non_nullable_int_is_none_evaluates_false():
    """Verify that '0 is None' is not considered True for a non-nullable integer."""
    code = """
def is_none(x: int) -> bool:
    \"\"\"
    @ensures return_val == False
    \"\"\"
    return x is None
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    # Since an integer cannot be None, return_val is always False, satisfying the postcondition
    assert res.status == VerificationStatus.VERIFIED


def test_integer_overflow_32bit_detected():
    """Verify that adding 1 to unbounded integer flags INTEGER_OVERFLOW when enabled."""
    code = """
def add_overflow(x: int) -> int:
    \"\"\"
    @safety no_integer_overflow
    \"\"\"
    return x + 1
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.violation_type == "INTEGER_OVERFLOW"
    # Concrete counterexample input must exceed 32-bit maximum (2^31 - 1 = 2147483647)
    assert res.counterexample.inputs["x"] >= 2147483647 or res.counterexample.inputs["x"] < -2147483648


def test_integer_overflow_safe_bounded():
    """Verify that clamped/preconditioned arithmetic satisfies no_integer_overflow."""
    code = """
def safe_inc(x: int) -> int:
    \"\"\"
    @safety no_integer_overflow
    @requires x >= 0
    @requires x < 1000
    \"\"\"
    return x + 1
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED


def test_empty_collection_input_in_counterexample():
    """Verify that indexing an empty collection yields arr = [] in counterexample inputs."""
    code = """
def head(arr: list[int]) -> int:
    return arr[0]
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.inputs["arr"] == []
    assert "empty collection" in res.counterexample.description.lower()


def test_for_loop_symbolic_range_unrolling():
    """Verify that for-loops with symbolic range(n) are properly unrolled."""
    code = """
def count_steps(n: int) -> int:
    \"\"\"
    @requires n == 3
    @ensures return_val == 3
    \"\"\"
    total = 0
    for i in range(n):
        total += 1
    return total
"""
    verifier = SMTVerifier(max_loop_unroll=5)
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED


def test_for_loop_bounds_check():
    """Verify that an off-by-one loop index in range(len(arr) + 1) is detected."""
    code = """
def bad_loop_access(arr: list[int]) -> int:
    \"\"\"
    @requires len(arr) > 0
    \"\"\"
    last = 0
    for i in range(len(arr) + 1):
        last = arr[i]
    return last
"""
    verifier = SMTVerifier(max_loop_unroll=4)
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    assert res.counterexample.violation_type == "INDEX_OUT_OF_BOUNDS"


def test_subscript_augassign():
    """Verify that arr[i] += val handles Subscript AugAssign cleanly."""
    code = """
def inc_first(arr: list[int]) -> int:
    \"\"\"
    @requires len(arr) > 0
    @ensures return_val == 10
    \"\"\"
    arr[0] = 5
    arr[0] += 5
    return arr[0]
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code)
    assert res.status == VerificationStatus.VERIFIED


def test_strict_postcondition_evaluation_runtime_error():
    """Verify that unparseable postconditions do NOT silently pass as verified."""
    spec = Specification()
    spec.add_postcondition("invalid_syntax%%%bad_expr")
    code = """
def simple_fn(x: int) -> int:
    return x
"""
    verifier = SMTVerifier()
    res = verifier.verify_source(code, spec=spec)
    assert res.status == VerificationStatus.RUNTIME_ERROR
    assert "Failed to parse or evaluate" in res.details


def test_cegis_repair_none_dereference():
    """Test full CEGIS feedback loop repairing a NoneType dereference vulnerability."""
    buggy_code = """
from typing import Optional

def count_items(items: Optional[list[int]]) -> int:
    \"\"\"
    @ensures return_val >= 0
    \"\"\"
    return len(items)
"""
    engine = VeriFixEngine(llm_client=ScriptedRepairClient(), verifier=SMTVerifier(), max_iterations=3)
    res = engine.repair(buggy_code, function_name="count_items")
    assert res.success is True
    assert res.verified_patch is not None
    assert "if items is None" in res.verified_patch
