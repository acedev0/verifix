"""Command-Line Interface (CLI) for VeriFix."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from verifix.core.engine import VeriFixEngine
from verifix.core.types import Specification, VerificationStatus
from verifix.core.contract import ContractExtractor
from verifix.verifier.smt_engine import SMTVerifier
from verifix.generator.gemini import GeminiClient
from verifix.generator.mock_llm import ScriptedRepairClient, MockLLMClient
from verifix.patches.ast_patcher import ASTPatcher
from verifix.utils.formatter import ConsoleFormatter


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the VeriFix CLI."""
    parser = argparse.ArgumentParser(
        prog="verifix",
        description="VeriFix: Neurosymbolic Code Vulnerability & Bug Patcher combining LLMs with Z3 SMT Formal Verification.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: verify
    verify_p = subparsers.add_parser("verify", help="Formally verify a Python function with Z3 SMT solver")
    verify_p.add_argument("file", help="Path to Python source file")
    verify_p.add_argument("--func", "-f", help="Target function name (default: first function in file)")
    verify_p.add_argument("--timeout", type=int, default=5000, help="SMT solver timeout in ms")
    verify_p.add_argument("--check-overflow", action="store_true", help="Enable 32-bit integer overflow checking")

    # Command: patch
    patch_p = subparsers.add_parser("patch", help="Run Neurosymbolic CEGIS repair loop to fix a buggy function")
    patch_p.add_argument("file", help="Path to Python source file")
    patch_p.add_argument("--func", "-f", help="Target function name (default: first function in file)")
    patch_p.add_argument("--max-iters", "-i", type=int, default=5, help="Maximum CEGIS loop iterations")
    patch_p.add_argument(
        "--provider",
        choices=["gemini", "scripted", "mock"],
        default="scripted",
        help="LLM generation provider (default: scripted/heuristic, use 'gemini' for live LLM)",
    )
    patch_p.add_argument("--check-overflow", action="store_true", help="Enable 32-bit integer overflow checking")
    patch_p.add_argument("--in-place", "-w", action="store_true", help="Write verified patch directly into file")

    # Command: demo
    demo_p = subparsers.add_parser("demo", help="Run a self-contained neurosymbolic repair demonstration")

    return parser


def run_verify(args: argparse.Namespace) -> int:
    """Run formal verification on a file."""
    print(ConsoleFormatter.banner())
    if not os.path.exists(args.file):
        print(f"Error: File '{args.file}' not found.")
        return 1

    with open(args.file, "r", encoding="utf-8") as f:
        source_code = f.read()

    verifier = SMTVerifier(timeout_ms=args.timeout)
    if getattr(args, "check_overflow", False):
        verifier.safety_manager.enabled_rules.add("no_integer_overflow")

    print(f"\n[Verifying {args.file} (function: {args.func or '<first>'}) with Z3...]")
    res = verifier.verify_source(source_code, function_name=args.func)

    badge = ConsoleFormatter.badge(res.status)
    print(f"Verification Result: {badge} ({res.solver_time_ms:.2f} ms)")
    if res.details:
        print(f"Details: {res.details}")
    if res.proof_summary:
        print(f"Proof: {res.proof_summary}")

    if res.counterexample:
        ConsoleFormatter.print_counterexample(res.counterexample)
        return 2  # Counterexample found (bug)

    return 0 if res.is_verified else 1


def run_patch(args: argparse.Namespace) -> int:
    """Run the neurosymbolic CEGIS repair loop."""
    print(ConsoleFormatter.banner())
    if not os.path.exists(args.file):
        print(f"Error: File '{args.file}' not found.")
        return 1

    with open(args.file, "r", encoding="utf-8") as f:
        source_code = f.read()

    # Select LLM client
    if args.provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Warning: GEMINI_API_KEY environment variable not set. Falling back to scripted provider.")
            llm_client = ScriptedRepairClient()
        else:
            llm_client = GeminiClient(api_key=api_key)
    elif args.provider == "mock":
        llm_client = MockLLMClient()
    else:
        llm_client = ScriptedRepairClient()

    verifier = SMTVerifier()
    if getattr(args, "check_overflow", False):
        verifier.safety_manager.enabled_rules.add("no_integer_overflow")

    def on_step_callback(step: int, msg: str, v_res: Optional[Any]) -> None:
        if step == 0:
            print(f"[*] Initial formal check: {msg}")
            if v_res and v_res.counterexample:
                print(f"    -> Counterexample found: {v_res.counterexample.inputs}")
        else:
            status_str = v_res.status.value if v_res else "N/A"
            print(f"[*] CEGIS Iteration {step}: {msg} [{status_str}]")
            if v_res and v_res.counterexample:
                print(f"    -> SMT Counterexample: {v_res.counterexample.inputs} ({v_res.counterexample.violation_type})")

    engine = VeriFixEngine(
        llm_client=llm_client,
        verifier=verifier,
        max_iterations=args.max_iters,
        on_step=on_step_callback,
    )

    print(f"\n[Starting Neurosymbolic CEGIS repair on '{args.file}'...]")
    repair_result = engine.repair(source_code, function_name=args.func)
    ConsoleFormatter.print_repair_summary(repair_result)

    if repair_result.success and repair_result.verified_patch:
        # Show diff
        diff_str = ASTPatcher.generate_diff(repair_result.original_code, repair_result.verified_patch)
        if diff_str:
            print("Unified Diff of Verified Repair:")
            print(diff_str)

        if args.in_place:
            new_source = ASTPatcher.replace_function_in_source(
                source_code,
                repair_result.function_name,
                repair_result.verified_patch,
            )
            with open(args.file, "w", encoding="utf-8") as f:
                f.write(new_source)
            print(f"[+] Successfully wrote verified patch to '{args.file}'")

        return 0

    return 1


def run_demo(args: argparse.Namespace) -> int:
    """Run an end-to-end interactive demo."""
    print(ConsoleFormatter.banner())
    print("\n--- VeriFix End-to-End Neurosymbolic CEGIS Demo ---")
    print("Scenario: Vulnerable Bank Account Withdrawal function with negative balance vulnerability.\n")

    vulnerable_code = (
        'def withdraw(balance: int, amount: int) -> int:\n'
        '    """\n'
        '    @requires balance >= 0\n'
        '    @requires amount > 0\n'
        '    @ensures return_val >= 0\n'
        '    @ensures return_val == balance - amount if amount <= balance else return_val == balance\n'
        '    """\n'
        '    # Vulnerability: does not check if amount <= balance before subtracting!\n'
        '    return balance - amount\n'
    )

    print(f"Original Code:\n{vulnerable_code}")

    # Simulated LLM that first produces a flawed patch, then self-corrects via counterexample
    flawed_patch_1 = (
        '```python\n'
        'def withdraw(balance: int, amount: int) -> int:\n'
        '    """\n'
        '    @requires balance >= 0\n'
        '    @requires amount > 0\n'
        '    @ensures return_val >= 0\n'
        '    @ensures return_val == balance - amount if amount <= balance else return_val == balance\n'
        '    """\n'
        '    # Flawed patch: off-by-one or inverted condition: amount < balance instead of amount <= balance\n'
        '    if amount < balance:\n'
        '        return balance - amount\n'
        '    return balance\n'
        '```'
    )

    verified_patch_2 = (
        '```python\n'
        'def withdraw(balance: int, amount: int) -> int:\n'
        '    """\n'
        '    @requires balance >= 0\n'
        '    @requires amount > 0\n'
        '    @ensures return_val >= 0\n'
        '    @ensures return_val == balance - amount if amount <= balance else return_val == balance\n'
        '    """\n'
        '    if amount <= balance:\n'
        '        return balance - amount\n'
        '    return balance\n'
        '```'
    )

    mock_llm = MockLLMClient(responses=[flawed_patch_1, verified_patch_2])

    def on_step(step: int, msg: str, v_res: Optional[Any]) -> None:
        if step == 0:
            print(f"[Phase 1: Initial SMT Verification]")
            if v_res and v_res.counterexample:
                print(f"  * SMT Solver found concrete bug input: {v_res.counterexample.inputs}")
                print(f"  * Description: {v_res.counterexample.description}")
        else:
            print(f"\n[Phase 2.{step}: CEGIS Iteration {step}]")
            if v_res:
                print(f"  * Status: {ConsoleFormatter.badge(v_res.status)}")
                if v_res.counterexample:
                    print(f"  * LLM Patch Rejected! Counterexample: {v_res.counterexample.inputs}")
                    print(f"  * Violated condition: {v_res.counterexample.failed_condition}")
                elif v_res.is_verified:
                    print(f"  * Formal Proof: {v_res.proof_summary}")

    engine = VeriFixEngine(
        llm_client=mock_llm,
        verifier=SMTVerifier(),
        max_iterations=3,
        on_step=on_step,
    )

    result = engine.repair(vulnerable_code, function_name="withdraw")
    ConsoleFormatter.print_repair_summary(result)

    diff = ASTPatcher.generate_diff(result.original_code, result.verified_patch or "")
    print(f"Mathematical Proof of Correctness:\n{result.history[-1].verification_result.proof_summary}")
    print("\nVerified Patch Diff:\n" + diff)
    return 0


def main() -> None:
    """CLI entrypoint."""
    parser = create_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()
    if args.command == "verify":
        code = run_verify(args)
    elif args.command == "patch":
        code = run_patch(args)
    elif args.command == "demo":
        code = run_demo(args)
    else:
        parser.print_help()
        code = 0

    sys.exit(code)


if __name__ == "__main__":
    main()
