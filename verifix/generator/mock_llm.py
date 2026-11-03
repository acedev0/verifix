"""Mock and scripted LLM generators for deterministic testing and offline repair."""

from __future__ import annotations

import re
from typing import Callable, List, Optional, Union

from verifix.generator.llm_client import LLMClient


class MockLLMClient(LLMClient):
    """Deterministic mock LLM that returns a pre-configured sequence of patch proposals."""

    def __init__(
        self,
        responses: Optional[List[str]] = None,
        generator_fn: Optional[Callable[[str, int], str]] = None,
    ) -> None:
        self.responses = list(responses or [])
        self.generator_fn = generator_fn
        self.call_count = 0
        self.prompts_received: List[str] = []

    def generate_patch(self, prompt: str) -> str:
        self.prompts_received.append(prompt)
        current_idx = self.call_count
        self.call_count += 1

        if self.generator_fn:
            return self.generator_fn(prompt, current_idx)

        if current_idx < len(self.responses):
            return self.responses[current_idx]

        # If exhausted, return the last response
        if self.responses:
            return self.responses[-1]

        return "# No mock response available"


class ScriptedRepairClient(LLMClient):
    """Heuristic repair engine that synthesizes patches for common safety and invariant bugs.

    Useful when running offline or without an active LLM API key.
    """

    def __init__(self) -> None:
        self.call_count = 0

    def generate_patch(self, prompt: str) -> str:
        self.call_count += 1

        # Extract target function from prompt
        m_code = re.search(r"```python\n(def\s+[\s\S]+?)```", prompt)
        orig_fn_code = m_code.group(1).strip() if m_code else ""

        # Check for NoneType dereference
        if "NONE_DEREFERENCE" in prompt:
            m_none_var = re.search(r"- Counterexample inputs:.*?(\w+)\s*=\s*None", prompt)
            none_var = m_none_var.group(1) if m_none_var else "x"
            if orig_fn_code:
                lines = orig_fn_code.splitlines()
                insert_idx = 1
                in_doc = False
                for idx, line in enumerate(lines):
                    if '"""' in line or "'''" in line:
                        if in_doc:
                            insert_idx = idx + 1
                            break
                        else:
                            in_doc = True
                    elif idx > 0 and not in_doc and line.strip() and not line.strip().startswith("@"):
                        insert_idx = idx
                        break

                indent = "    "
                null_check = f"{indent}if {none_var} is None:\n{indent}    return 0"
                lines.insert(insert_idx, null_check)
                return f"```python\n" + "\n".join(lines) + "\n```"

        # Check for empty collection / index out of bounds
        if "EMPTY_COLLECTION" in prompt or "INDEX_OUT_OF_BOUNDS" in prompt:
            m_arr = re.search(r"(\w+)\s*=\s*\[\]", prompt)
            arr_name = m_arr.group(1) if m_arr else "arr"
            if orig_fn_code:
                lines = orig_fn_code.splitlines()
                insert_idx = 1
                in_doc = False
                for idx, line in enumerate(lines):
                    if '"""' in line or "'''" in line:
                        if in_doc:
                            insert_idx = idx + 1
                            break
                        else:
                            in_doc = True
                    elif idx > 0 and not in_doc and line.strip() and not line.strip().startswith("@"):
                        insert_idx = idx
                        break

                indent = "    "
                check_stmt = f"{indent}if len({arr_name}) == 0:\n{indent}    return 0"
                lines.insert(insert_idx, check_stmt)
                return f"```python\n" + "\n".join(lines) + "\n```"

        # Check for integer overflow
        if "INTEGER_OVERFLOW" in prompt:
            if orig_fn_code:
                lines = orig_fn_code.splitlines()
                for idx, line in enumerate(lines):
                    if line.strip().startswith("return "):
                        ret_expr = line.strip()[7:]
                        lines[idx] = f"    return min(2147483647, max(-2147483648, {ret_expr}))"
                return f"```python\n" + "\n".join(lines) + "\n```"

        # Check if counterexample feedback mentions division by zero
        if "DIVISION_BY_ZERO" in prompt:
            m_fn = re.search(r"def\s+(\w+)\s*\(([^)]*)\).*?:", prompt)
            if m_fn:
                fn_name = m_fn.group(1)
                args = [a.strip() for a in m_fn.group(2).split(",") if a.strip()]
                m_div = re.search(r"Violated Condition:\s*(\w+)\s*!=\s*0", prompt)
                zero_var = m_div.group(1) if m_div else (args[-1].split(":")[0].strip() if args else "y")
                first_arg = args[0].split(":")[0].strip() if args else "x"

                # Check if postcondition requires non-negative return
                has_non_neg_post = "return_val >= 0" in prompt
                ret_stmt = f"    res = {first_arg} // {zero_var}\n    return res if res >= 0 else 0" if has_non_neg_post else f"    return {first_arg} // {zero_var}"

                return (
                    f"```python\n"
                    f"def {fn_name}({m_fn.group(2)}):\n"
                    f"    if {zero_var} == 0:\n"
                    f"        return 0\n"
                    f"{ret_stmt}\n"
                    f"```"
                )

        # Check for balance/resource underflow
        if "POSTCONDITION_VIOLATION" in prompt or "balance" in prompt.lower():
            if "balance" in prompt and "amount" in prompt:
                return (
                    "```python\n"
                    "def withdraw(balance: int, amount: int) -> int:\n"
                    '    """\n'
                    "    @requires balance >= 0\n"
                    "    @requires amount > 0\n"
                    "    @ensures return_val >= 0\n"
                    '    """\n'
                    "    if amount > balance:\n"
                    "        return balance\n"
                    "    return balance - amount\n"
                    "```"
                )

        # Fallback: extract code from prompt
        m_code = re.search(r"```python\n(def\s+[\s\S]+?)```", prompt)
        if m_code:
            return f"```python\n{m_code.group(1)}\n```"

        return "```python\ndef patched_func(): pass\n```"
