"""
VeriFix: Neurosymbolic Code Vulnerability & Bug Patcher
Combining LLM Generative Patching with SMT (Z3) Formal Verification.
"""

from verifix.core.types import (
    Specification,
    VerificationResult,
    VerificationStatus,
    Counterexample,
    PatchCandidate,
    RepairResult,
)
from verifix.core.engine import VeriFixEngine
from verifix.verifier.smt_engine import SMTVerifier
from verifix.core.contract import requires, ensures, invariant, safety

__version__ = "0.1.0"
__all__ = [
    "Specification",
    "VerificationResult",
    "VerificationStatus",
    "Counterexample",
    "PatchCandidate",
    "RepairResult",
    "VeriFixEngine",
    "SMTVerifier",
    "requires",
    "ensures",
    "invariant",
    "safety",
]
