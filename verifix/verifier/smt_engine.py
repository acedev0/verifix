"""Formal SMT verification engine using Z3."""

from __future__ import annotations

import ast
import time
from typing import Any, Dict, List, Optional
import z3

from verifix.core.types import (
    Counterexample,
    Specification,
    VerificationResult,
    VerificationStatus,
)
from verifix.core.contract import ContractExtractor
from verifix.verifier.symbolic_exec import SymbolicExecutor, ExecutionPath
from verifix.verifier.safety_rules import SafetyRuleManager


class SMTVerifier:
    """SMT-based formal verification engine powered by Z3."""

    def __init__(
        self,
        timeout_ms: int = 5000,
        max_loop_unroll: int = 5,
        safety_manager: Optional[SafetyRuleManager] = None,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.max_loop_unroll = max_loop_unroll
        self.safety_manager = safety_manager or SafetyRuleManager()

    def verify_source(
        self,
        source_code: str,
        function_name: Optional[str] = None,
        spec: Optional[Specification] = None,
    ) -> VerificationResult:
        """Parse source code and verify the specified function against specifications."""
        try:
            func_node, extracted_spec = ContractExtractor.from_source(source_code, function_name)
        except Exception as e:
            return VerificationResult(
                status=VerificationStatus.SYNTAX_ERROR,
                details=f"Failed to parse source code: {e}",
            )

        merged_spec = spec or extracted_spec
        if spec and extracted_spec:
            # Merge extracted specs with user-provided spec
            for pre in extracted_spec.preconditions:
                if pre not in merged_spec.preconditions:
                    merged_spec.preconditions.append(pre)
            for post in extracted_spec.postconditions:
                if post not in merged_spec.postconditions:
                    merged_spec.postconditions.append(post)
            for inv in extracted_spec.invariants:
                if inv not in merged_spec.invariants:
                    merged_spec.invariants.append(inv)
            for k, v in extracted_spec.param_types.items():
                if k not in merged_spec.param_types:
                    merged_spec.param_types[k] = v

        return self.verify_function(func_node, merged_spec)

    def verify_function(
        self,
        func_node: ast.FunctionDef,
        spec: Optional[Specification] = None,
    ) -> VerificationResult:
        """Verify an AST FunctionDef node against a Specification using Z3."""
        start_time = time.perf_counter()
        spec = spec or ContractExtractor.from_function_node(func_node)

        # Sync safety rule manager with specification
        import copy
        active_safety_mgr = copy.deepcopy(self.safety_manager)
        if spec and spec.safety_checks:
            active_safety_mgr.enabled_rules = set(spec.safety_checks)
            active_safety_mgr.enabled_rules.add("assertions")

        executor = SymbolicExecutor(
            spec=spec,
            max_loop_unroll=self.max_loop_unroll,
            safety_manager=active_safety_mgr,
        )

        try:
            paths = executor.execute_function(func_node)
        except Exception as e:
            return VerificationResult(
                status=VerificationStatus.RUNTIME_ERROR,
                details=f"Symbolic execution failed: {e}",
                solver_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        # Phase 1: Verify all safety checks (division by zero, out of bounds, assertions, etc.)
        for path_idx, path in enumerate(paths):
            for check in path.safety_checks:
                solver = z3.Solver()
                solver.set("timeout", self.timeout_ms)

                # Path condition constraints up to the point of this safety check
                if check.path_condition is not None:
                    solver.add(check.path_condition)
                else:
                    for pc in path.path_condition:
                        solver.add(pc)

                # Negate the safety condition: we check if there exists an input where condition is FALSE
                solver.add(z3.Not(check.condition))

                check_status = solver.check()
                if check_status == z3.sat:
                    model = solver.model()
                    inputs = self._extract_model_inputs(
                        model,
                        executor.initial_vars,
                        executor.initial_lens,
                        executor.initial_nones,
                    )

                    v_type = check.rule_name
                    desc = check.description
                    # Detect if index out of bounds was specifically on an empty collection
                    if check.rule_name == "INDEX_OUT_OF_BOUNDS":
                        for arr_name, len_sym in executor.initial_lens.items():
                            if arr_name in desc or len(executor.initial_lens) == 1:
                                len_val = model.eval(len_sym)
                                if isinstance(len_val, z3.IntNumRef) and len_val.as_long() == 0:
                                    desc = f"Potential index out of bounds on empty collection '{arr_name}' (len == 0) at line {check.line_number or '?'}"
                                    break

                    cex = Counterexample(
                        inputs=inputs,
                        violation_type=v_type,
                        description=desc,
                        failed_condition=str(check.condition),
                        line_number=check.line_number,
                        trace=path.trace,
                    )
                    elapsed = (time.perf_counter() - start_time) * 1000
                    return VerificationResult(
                        status=VerificationStatus.COUNTEREXAMPLE_FOUND,
                        counterexample=cex,
                        details=f"Safety check violated: {desc}",
                        solver_time_ms=elapsed,
                    )
                elif check_status == z3.unknown:
                    elapsed = (time.perf_counter() - start_time) * 1000
                    return VerificationResult(
                        status=VerificationStatus.UNKNOWN,
                        details=f"Solver returned unknown on safety check: {solver.reason_unknown()}",
                        solver_time_ms=elapsed,
                    )

        # Phase 2: Verify postconditions and invariants
        conditions_to_check = [("postcondition", p) for p in spec.postconditions] + \
                              [("invariant", inv) for inv in spec.invariants]

        for cond_type, cond_str in conditions_to_check:
            for path_idx, path in enumerate(paths):
                # Construct evaluation environment for postcondition
                post_env_path = path.fork()
                if path.return_val is not None:
                    post_env_path.env["return_val"] = path.return_val
                    post_env_path.env["result"] = path.return_val
                    post_env_path.env[func_node.name] = path.return_val

                try:
                    cond_expr = executor.eval_condition_str(cond_str, post_env_path)
                except Exception as e:
                    elapsed = (time.perf_counter() - start_time) * 1000
                    return VerificationResult(
                        status=VerificationStatus.RUNTIME_ERROR,
                        details=f"Failed to parse or evaluate specification '{cond_str}': {e}",
                        solver_time_ms=elapsed,
                    )

                if cond_expr is None:
                    elapsed = (time.perf_counter() - start_time) * 1000
                    return VerificationResult(
                        status=VerificationStatus.RUNTIME_ERROR,
                        details=f"Specification '{cond_str}' did not evaluate to a boolean condition",
                        solver_time_ms=elapsed,
                    )

                solver = z3.Solver()
                solver.set("timeout", self.timeout_ms)

                for pc in path.path_condition:
                    solver.add(pc)

                # Negate postcondition: exists an input where postcondition FAILS
                solver.add(z3.Not(cond_expr))

                check_status = solver.check()
                if check_status == z3.sat:
                    model = solver.model()
                    inputs = self._extract_model_inputs(
                        model,
                        executor.initial_vars,
                        executor.initial_lens,
                        executor.initial_nones,
                    )
                    if path.return_val is not None:
                        # Also compute the buggy return value for this counterexample
                        try:
                            ret_val_concrete = self._eval_in_model(model, path.return_val)
                            inputs["[computed_return]"] = ret_val_concrete
                        except Exception:
                            pass

                    cex = Counterexample(
                        inputs=inputs,
                        violation_type=f"{cond_type.upper()}_VIOLATION",
                        description=f"Specification '{cond_str}' violated for path {path_idx + 1}",
                        failed_condition=cond_str,
                        trace=path.trace,
                    )
                    elapsed = (time.perf_counter() - start_time) * 1000
                    return VerificationResult(
                        status=VerificationStatus.COUNTEREXAMPLE_FOUND,
                        counterexample=cex,
                        details=f"Formal verification failed: counterexample input found.",
                        solver_time_ms=elapsed,
                    )
                elif check_status == z3.unknown:
                    elapsed = (time.perf_counter() - start_time) * 1000
                    return VerificationResult(
                        status=VerificationStatus.UNKNOWN,
                        details=f"Solver timed out or returned unknown: {solver.reason_unknown()}",
                        solver_time_ms=elapsed,
                    )

        # All checks UNSAT => Function is formally verified!
        elapsed = (time.perf_counter() - start_time) * 1000
        total_paths = len(paths)
        summary = (
            f"Mathematically verified across {total_paths} symbolic path(s). "
            f"All {len(conditions_to_check)} postcondition(s) and safety rules proved UNSAT for failure states."
        )
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            details="Formally verified with Z3 SMT solver.",
            proof_summary=summary,
            solver_time_ms=elapsed,
        )

    def _extract_model_inputs(
        self,
        model: z3.ModelRef,
        initial_vars: Dict[str, z3.ExprRef],
        initial_lens: Dict[str, z3.ExprRef],
        initial_nones: Optional[Dict[str, z3.BoolRef]] = None,
    ) -> Dict[str, Any]:
        """Convert a Z3 model into concrete Python variables and values."""
        inputs: Dict[str, Any] = {}
        for name, sym in initial_vars.items():
            if initial_nones and name in initial_nones:
                none_val = model.eval(initial_nones[name])
                if z3.is_true(none_val):
                    inputs[name] = None
                    continue

            if isinstance(sym, z3.ArrayRef):
                # Reconstruct array up to length
                arr_len = 0
                if name in initial_lens:
                    len_sym = initial_lens[name]
                    eval_len = model.eval(len_sym)
                    if isinstance(eval_len, z3.IntNumRef):
                        arr_len = max(0, min(eval_len.as_long(), 10))  # Bound preview
                arr_values = []
                for idx in range(arr_len):
                    elem = model.eval(z3.Select(sym, z3.IntVal(idx)))
                    if isinstance(elem, z3.IntNumRef):
                        arr_values.append(elem.as_long())
                    else:
                        arr_values.append(0)
                inputs[name] = arr_values
            else:
                eval_val = model.eval(sym, model_completion=True)
                inputs[name] = self._z3_val_to_python(eval_val)
        return inputs

    def _eval_in_model(self, model: z3.ModelRef, expr: z3.ExprRef) -> Any:
        """Evaluate a symbolic expression using concrete model."""
        evaluated = model.eval(expr, model_completion=True)
        return self._z3_val_to_python(evaluated)

    def _z3_val_to_python(self, val: z3.ExprRef) -> Any:
        """Convert a Z3 evaluated value to native Python type."""
        if isinstance(val, z3.IntNumRef):
            return val.as_long()
        elif isinstance(val, z3.BoolRef):
            return z3.is_true(val)
        elif isinstance(val, z3.RatNumRef):
            return float(val.as_decimal(4))
        return str(val)
