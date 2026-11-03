"""Tests for SMT formal verification with Z3."""

from verifix.verifier.smt_engine import SMTVerifier
from verifix.core.types import VerificationStatus


def test_verifier_proves_valid_specification():
    code = '''
def max_of_two(a: int, b: int) -> int:
    """
    @ensures return_val >= a
    @ensures return_val >= b
    @ensures return_val == a or return_val == b
    """
    if a >= b:
        return a
    else:
        return b
'''
    verifier = SMTVerifier()
    res = verifier.verify_source(code)

    assert res.status == VerificationStatus.VERIFIED
    assert res.is_verified
    assert res.counterexample is None
    assert "Mathematically verified" in (res.proof_summary or "")


def test_verifier_catches_postcondition_violation():
    code = '''
def flawed_max(a: int, b: int) -> int:
    """
    @ensures return_val >= a
    @ensures return_val >= b
    """
    # Flawed: always returns a
    return a
'''
    verifier = SMTVerifier()
    res = verifier.verify_source(code)

    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample is not None
    # Solver should find a case where b > a, so return_val >= b fails!
    a_val = res.counterexample.inputs["a"]
    b_val = res.counterexample.inputs["b"]
    assert b_val > a_val


def test_verifier_catches_boundary_off_by_one():
    code = '''
def strictly_greater(x: int) -> int:
    """
    @requires x >= 0
    @ensures return_val > x
    """
    # Bug: when x == 0, returns 0, which is not strictly greater!
    if x > 0:
        return x + 1
    return 0
'''
    verifier = SMTVerifier()
    res = verifier.verify_source(code)

    assert res.status == VerificationStatus.COUNTEREXAMPLE_FOUND
    assert res.counterexample.inputs["x"] == 0
