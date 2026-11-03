"""CLI and terminal formatting with ANSI colors and structured reports."""

from __future__ import annotations

import sys
from typing import Any, Dict, Optional

from verifix.core.types import Counterexample, RepairResult, VerificationResult, VerificationStatus


class ConsoleFormatter:
    """Terminal styling, ANSI coloring, and visual report generation."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"

    @classmethod
    def banner(cls) -> str:
        return (
            f"{cls.BOLD}{cls.CYAN}"
            f"==========================================================\n"
            f"  VeriFix: Neurosymbolic Code Vulnerability Patcher\n"
            f"  LLM Generative Repair + SMT (Z3) Formal Verification\n"
            f"=========================================================={cls.RESET}"
        )

    @classmethod
    def badge(cls, status: VerificationStatus) -> str:
        if status == VerificationStatus.VERIFIED:
            return f"{cls.BOLD}{cls.GREEN}[PROVEN VERIFIED (UNSAT)]{cls.RESET}"
        elif status == VerificationStatus.COUNTEREXAMPLE_FOUND:
            return f"{cls.BOLD}{cls.RED}[BUG DETECTED (SAT CEX)]{cls.RESET}"
        elif status == VerificationStatus.SYNTAX_ERROR:
            return f"{cls.BOLD}{cls.YELLOW}[SYNTAX ERROR]{cls.RESET}"
        return f"{cls.BOLD}{cls.GRAY}[{status.value}]{cls.RESET}"

    @classmethod
    def print_counterexample(cls, cex: Counterexample) -> None:
        print(f"\n{cls.BOLD}{cls.RED}>> Counterexample Found by SMT Solver:{cls.RESET}")
        print(f"  {cls.BOLD}Type:{cls.RESET} {cex.violation_type}")
        print(f"  {cls.BOLD}Details:{cls.RESET} {cex.description}")
        if cex.failed_condition:
            print(f"  {cls.BOLD}Violated Condition:{cls.RESET} {cex.failed_condition}")
        if cex.line_number:
            print(f"  {cls.BOLD}Line Number:{cls.RESET} {cex.line_number}")
        print(f"  {cls.BOLD}Concrete Failure Inputs:{cls.RESET}")
        for k, v in cex.inputs.items():
            print(f"    * {cls.CYAN}{k}{cls.RESET} = {cls.BOLD}{v}{cls.RESET}")
        if cex.trace:
            print(f"  {cls.BOLD}Execution Trace / Path Condition:{cls.RESET}")
            for step in cex.trace:
                print(f"    -> {step}")

    @classmethod
    def print_repair_summary(cls, result: RepairResult) -> None:
        print(f"\n{cls.BOLD}{cls.CYAN}----------------------------------------------------------{cls.RESET}")
        print(f"{cls.BOLD}Repair Summary for function '{result.function_name}':{cls.RESET}")
        print(f"  Success: {'YES - Formally Proven Sound' if result.success else 'NO - Could not prove'}")
        print(f"  Total Iterations: {result.iterations}")
        print(f"  Execution Time: {result.total_time_ms:.2f} ms")

        if result.success and result.verified_patch:
            print(f"\n{cls.BOLD}{cls.GREEN}Verified Sound Patch:{cls.RESET}")
            print(f"{cls.GREEN}```python\n{result.verified_patch.strip()}\n```{cls.RESET}")
        elif not result.success:
            print(f"\n{cls.BOLD}{cls.RED}Repair did not converge within iteration limit.{cls.RESET}")
        print(f"{cls.BOLD}{cls.CYAN}----------------------------------------------------------{cls.RESET}\n")
