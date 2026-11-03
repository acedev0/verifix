"""VeriFix Neurosymbolic Dual-Engine Feedback Loop (CEGIS)."""

from __future__ import annotations

import ast
import time
from typing import Callable, List, Optional

from verifix.core.types import (
    Counterexample,
    PatchCandidate,
    RepairResult,
    Specification,
    VerificationResult,
    VerificationStatus,
)
from verifix.core.contract import ContractExtractor
from verifix.verifier.smt_engine import SMTVerifier
from verifix.generator.llm_client import LLMClient
from verifix.generator.prompt_builder import CEGISPromptBuilder
from verifix.generator.mock_llm import ScriptedRepairClient
from verifix.patches.ast_patcher import ASTPatcher


class VeriFixEngine:
    """The Neurosymbolic Dual-Engine Patcher combining LLMs with Z3 Formal Verification."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        verifier: Optional[SMTVerifier] = None,
        max_iterations: int = 5,
        on_step: Optional[Callable[[int, str, Optional[VerificationResult]], None]] = None,
    ) -> None:
        self.llm_client = llm_client or ScriptedRepairClient()
        self.verifier = verifier or SMTVerifier()
        self.max_iterations = max_iterations
        self.on_step = on_step

    def repair(
        self,
        source_code: str,
        function_name: Optional[str] = None,
        spec: Optional[Specification] = None,
    ) -> RepairResult:
        """Execute the CEGIS neurosymbolic repair loop on a target function."""
        start_time = time.perf_counter()

        # Step 1: Parse and extract target function and specification
        try:
            func_node, extracted_spec = ContractExtractor.from_source(source_code, function_name)
            target_fn_name = func_node.name
        except Exception as e:
            return RepairResult(
                original_code=source_code,
                function_name=function_name or "unknown",
                success=False,
                verified_patch=None,
                iterations=0,
                total_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        effective_spec = spec or extracted_spec
        original_fn_code = ast.unparse(func_node)

        # Step 2: Formally verify original function
        initial_check = self.verifier.verify_function(func_node, effective_spec)

        if initial_check.is_verified:
            # Code is already mathematically sound!
            return RepairResult(
                original_code=original_fn_code,
                function_name=target_fn_name,
                success=True,
                verified_patch=original_fn_code,
                iterations=0,
                initial_counterexample=None,
                total_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        initial_cex = initial_check.counterexample
        current_cex = initial_cex
        history: List[PatchCandidate] = []
        current_code = original_fn_code

        if self.on_step:
            self.on_step(0, "Initial formal verification detected counterexample", initial_check)

        # Step 3: Neurosymbolic Dual-Engine CEGIS Feedback Loop
        for iteration in range(1, self.max_iterations + 1):
            # 3a. Construct CEGIS prompt incorporating concrete SMT counterexamples
            prompt = CEGISPromptBuilder.build_prompt(
                function_name=target_fn_name,
                current_code=current_code,
                spec=effective_spec,
                latest_counterexample=current_cex,
                history=history,
                iteration=iteration,
            )

            # 3b. Query LLM Generator for candidate patch proposal
            try:
                raw_response = self.llm_client.generate_patch(prompt)
            except Exception as e:
                # LLM invocation failed
                if self.on_step:
                    self.on_step(iteration, f"LLM generation failed: {e}", None)
                continue

            # 3c. Extract and validate candidate code
            candidate_code = ASTPatcher.extract_code_block(raw_response)
            is_valid_syntax, syntax_err = ASTPatcher.validate_syntax(candidate_code)

            if not is_valid_syntax:
                v_res = VerificationResult(
                    status=VerificationStatus.SYNTAX_ERROR,
                    details=f"Invalid Python syntax: {syntax_err}",
                )
                candidate = PatchCandidate(
                    iteration=iteration,
                    code=candidate_code,
                    explanation=f"Syntax Error: {syntax_err}",
                    verification_result=v_res,
                )
                history.append(candidate)
                if self.on_step:
                    self.on_step(iteration, "Candidate syntax error", v_res)
                continue

            # 3d. Formal Verification of Candidate Patch with SMT / Z3
            v_res = self.verifier.verify_source(
                source_code=candidate_code,
                function_name=target_fn_name,
                spec=effective_spec,
            )

            candidate = PatchCandidate(
                iteration=iteration,
                code=candidate_code,
                verification_result=v_res,
            )
            history.append(candidate)

            if self.on_step:
                self.on_step(iteration, f"Candidate tested: {v_res.status.value}", v_res)

            # 3e. Check if verified
            if v_res.is_verified:
                # Mathematical proof completed! UNSAT counterexample
                total_time = (time.perf_counter() - start_time) * 1000
                return RepairResult(
                    original_code=original_fn_code,
                    function_name=target_fn_name,
                    success=True,
                    verified_patch=candidate_code,
                    iterations=iteration,
                    history=history,
                    initial_counterexample=initial_cex,
                    total_time_ms=total_time,
                )

            # 3f. If flawed, extract new counterexample and update for next CEGIS iteration
            if v_res.counterexample:
                current_cex = v_res.counterexample
                current_code = candidate_code

        # Max iterations reached without formal proof
        total_time = (time.perf_counter() - start_time) * 1000
        return RepairResult(
            original_code=original_fn_code,
            function_name=target_fn_name,
            success=False,
            verified_patch=None,
            iterations=self.max_iterations,
            history=history,
            initial_counterexample=initial_cex,
            total_time_ms=total_time,
        )
