"""Safety rules and vulnerability invariants for formal verification."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, List, Optional
import z3


@dataclass
class SafetyCheck:
    """Represents a safety invariant that must hold during execution."""
    condition: z3.BoolRef  # The condition that MUST be true for code to be safe
    rule_name: str  # e.g., "DIVISION_BY_ZERO", "INDEX_OUT_OF_BOUNDS", "INTEGER_OVERFLOW"
    description: str
    ast_node: Optional[ast.AST] = None
    line_number: Optional[int] = None
    path_condition: Optional[z3.BoolRef] = None


class SafetyRuleManager:
    """Manages active safety checks for vulnerability detection."""

    def __init__(self, enabled_rules: Optional[List[str]] = None) -> None:
        self.enabled_rules = set(enabled_rules if enabled_rules is not None else [
            "no_division_by_zero",
            "no_index_out_of_bounds",
            "no_none_dereference",
            "assertions",
        ])

    def check_division_by_zero(
        self,
        denominator: z3.ExprRef,
        node: Optional[ast.AST] = None,
        path_cond: Optional[z3.BoolRef] = None
    ) -> Optional[SafetyCheck]:
        """Generate safety condition that denominator != 0."""
        if "no_division_by_zero" not in self.enabled_rules:
            return None
        
        # In Z3, denominator != 0
        cond = (denominator != 0)
        lineno = getattr(node, "lineno", None) if node else None
        return SafetyCheck(
            condition=cond,
            rule_name="DIVISION_BY_ZERO",
            description=f"Potential division or modulo by zero at line {lineno or '?'}",
            ast_node=node,
            line_number=lineno,
            path_condition=path_cond,
        )

    def check_array_bounds(
        self,
        index: z3.ExprRef,
        length: z3.ExprRef,
        node: Optional[ast.AST] = None,
        path_cond: Optional[z3.BoolRef] = None,
        arr_name: str = "array",
    ) -> Optional[SafetyCheck]:
        """Generate safety condition that 0 <= index < length."""
        if "no_index_out_of_bounds" not in self.enabled_rules:
            return None

        cond = z3.And(index >= 0, index < length)
        lineno = getattr(node, "lineno", None) if node else None
        return SafetyCheck(
            condition=cond,
            rule_name="INDEX_OUT_OF_BOUNDS",
            description=f"Potential index out of bounds on '{arr_name}' at line {lineno or '?'}: index must satisfy 0 <= idx < len",
            ast_node=node,
            line_number=lineno,
            path_condition=path_cond,
        )

    def check_none_dereference(
        self,
        var_name: str,
        is_none_flag: z3.BoolRef,
        node: Optional[ast.AST] = None,
        path_cond: Optional[z3.BoolRef] = None,
    ) -> Optional[SafetyCheck]:
        """Generate safety condition that variable is NOT None (is_none_flag is False)."""
        if "no_none_dereference" not in self.enabled_rules:
            return None

        cond = z3.Not(is_none_flag)
        lineno = getattr(node, "lineno", None) if node else None
        return SafetyCheck(
            condition=cond,
            rule_name="NONE_DEREFERENCE",
            description=f"Potential NoneType dereference: '{var_name}' may be None at line {lineno or '?'}",
            ast_node=node,
            line_number=lineno,
            path_condition=path_cond,
        )

    def check_integer_overflow_32bit(
        self,
        expr: z3.ExprRef,
        node: Optional[ast.AST] = None,
        path_cond: Optional[z3.BoolRef] = None
    ) -> Optional[SafetyCheck]:
        """Generate safety condition that -2^31 <= expr <= 2^31 - 1."""
        if "no_integer_overflow" not in self.enabled_rules:
            return None

        min_val = -2147483648
        max_val = 2147483647
        cond = z3.And(expr >= min_val, expr <= max_val)
        lineno = getattr(node, "lineno", None) if node else None
        return SafetyCheck(
            condition=cond,
            rule_name="INTEGER_OVERFLOW",
            description=f"Potential 32-bit integer overflow/underflow at line {lineno or '?'}",
            ast_node=node,
            line_number=lineno,
            path_condition=path_cond,
        )

    def check_assertion(
        self,
        assert_cond: z3.BoolRef,
        node: Optional[ast.AST] = None,
        path_cond: Optional[z3.BoolRef] = None,
        msg: Optional[str] = None
    ) -> Optional[SafetyCheck]:
        """Generate safety condition for an in-code assertion."""
        lineno = getattr(node, "lineno", None) if node else None
        desc = msg or f"Assertion violated at line {lineno or '?'}"
        return SafetyCheck(
            condition=assert_cond,
            rule_name="ASSERTION_FAILED",
            description=desc,
            ast_node=node,
            line_number=lineno,
            path_condition=path_cond,
        )
