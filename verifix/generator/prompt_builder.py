"""CEGIS (Counterexample-Guided Inductive Synthesis) prompt constructor for LLMs."""

from __future__ import annotations

from typing import List, Optional

from verifix.core.types import Counterexample, PatchCandidate, Specification


class CEGISPromptBuilder:
    """Builds structured CEGIS prompts for LLMs incorporating SMT counterexample feedback."""

    @classmethod
    def build_prompt(
        cls,
        function_name: str,
        current_code: str,
        spec: Specification,
        latest_counterexample: Optional[Counterexample],
        history: Optional[List[PatchCandidate]] = None,
        iteration: int = 1,
    ) -> str:
        """Construct the prompt guiding the LLM to patch the code based on formal counterexamples."""
        parts: List[str] = []

        parts.append("# TASK: Formal Code Vulnerability Repair (Neurosymbolic CEGIS)")
        parts.append(
            f"You are an expert formal methods and security repair engineer. "
            f"A formal verifier (Z3 SMT Solver) has analyzed function `{function_name}` "
            f"and detected a specification violation or vulnerability.\n"
        )

        parts.append("## TARGET FUNCTION TO REPAIR:")
        parts.append("```python")
        parts.append(current_code.strip())
        parts.append("```\n")

        parts.append("## FORMAL SPECIFICATIONS & CONTRACTS:")
        if spec.preconditions:
            parts.append("- Preconditions (Assume true for all inputs):")
            for pre in spec.preconditions:
                parts.append(f"    * {pre}")
        if spec.postconditions:
            parts.append("- Postconditions (Must hold for all returns):")
            for post in spec.postconditions:
                parts.append(f"    * {post}")
        if spec.invariants:
            parts.append("- Invariants:")
            for inv in spec.invariants:
                parts.append(f"    * {inv}")
        if spec.safety_checks:
            parts.append("- Automatic Safety Rules:")
            for chk in spec.safety_checks:
                parts.append(f"    * {chk}")
        parts.append("")

        if latest_counterexample:
            parts.append("## CRITICAL SMT COUNTEREXAMPLE (MATHEMATICAL PROOF OF FAILURE):")
            parts.append(
                "The formal solver proved that the current implementation fails under the following concrete inputs:"
            )
            parts.append("```text")
            parts.append(latest_counterexample.format_feedback())
            parts.append("```")
            parts.append(
                "Your patch MUST handle this concrete edge case, but DO NOT hardcode a special case "
                "just for this single input. The fix must be general and sound for ALL inputs.\n"
            )

        if history:
            flawed_attempts = [p for p in history if p.verification_result and not p.verification_result.is_verified]
            if flawed_attempts:
                parts.append("## PREVIOUS REJECTED PATCH PROPOSALS & COUNTEREXAMPLES:")
                for candidate in flawed_attempts[-3:]:  # Keep recent history
                    parts.append(f"### Attempt #{candidate.iteration}:")
                    parts.append("```python")
                    parts.append(candidate.code.strip())
                    parts.append("```")
                    if candidate.verification_result and candidate.verification_result.counterexample:
                        parts.append(f"Rejected by SMT Verifier with Counterexample:")
                        parts.append(f"  Inputs: {candidate.verification_result.counterexample.inputs}")
                        parts.append(f"  Violation: {candidate.verification_result.counterexample.violation_type}")
                        parts.append(f"  Details: {candidate.verification_result.counterexample.description}")
                    parts.append("")

        parts.append("## INSTRUCTIONS:")
        parts.append("1. Fix the bug, vulnerability, or invariant violation.")
        parts.append("2. Ensure the patch satisfies ALL preconditions, postconditions, and safety rules for EVERY possible input.")
        parts.append("3. Retain the same function signature and contracts/docstrings.")
        parts.append("4. Output ONLY the complete, corrected Python function enclosed in a single ```python ... ``` code block.")

        return "\n".join(parts)
