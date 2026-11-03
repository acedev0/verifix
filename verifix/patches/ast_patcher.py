"""AST manipulation, code block extraction, and source splicing."""

from __future__ import annotations

import ast
import difflib
import re
from typing import Optional, Tuple


class ASTPatcher:
    """Utilities for extracting, validating, and splicing Python code patches."""

    @classmethod
    def extract_code_block(cls, response: str) -> str:
        """Extract Python code enclosed in markdown code fences or return cleaned text."""
        # Match ```python ... ``` using re.DOTALL and \Z for unclosed blocks
        pattern = r"```(?:python)?\s*\n?(.*?)(?:```|\Z)"
        matches = [m.strip() for m in re.findall(pattern, response, re.DOTALL) if m.strip()]
        if matches:
            # Pick the longest block containing 'def '
            for block in sorted(matches, key=len, reverse=True):
                if "def " in block:
                    return block.strip()
            return matches[0].strip()

        # If no fences, but starts with decorator or def
        clean = response.strip()
        m_fn = re.search(r"((?:@\w+(?:\([^)]*\))?\s*\n\s*)*def\s+[\s\S]+)", clean)
        if m_fn:
            return m_fn.group(1).strip()

        if "def " in clean:
            idx = clean.find("def ")
            return clean[idx:].strip()

        return clean

    @classmethod
    def validate_syntax(cls, code: str) -> Tuple[bool, Optional[str]]:
        """Validate if the string is syntactically valid Python code."""
        try:
            ast.parse(code)
            return True, None
        except SyntaxError as e:
            return False, f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}"
        except Exception as e:
            return False, str(e)

    @classmethod
    def replace_function_in_source(
        cls,
        source_code: str,
        function_name: str,
        new_function_code: str,
    ) -> str:
        """Replace the target function definition in a module source file with the new code."""
        tree = ast.parse(source_code)
        lines = source_code.splitlines(keepends=True)

        target_node: Optional[ast.FunctionDef] = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                target_node = node
                break

        if target_node is None:
            # If not found, append to end
            return source_code.rstrip() + "\n\n" + new_function_code.strip() + "\n"

        # Determine line boundaries (1-indexed)
        start_line = target_node.lineno - 1
        # Include decorators if present
        if target_node.decorator_list:
            start_line = min(d.lineno for d in target_node.decorator_list) - 1

        end_line = getattr(target_node, "end_lineno", len(lines))

        # Check indentation of original function
        target_line_text = lines[start_line]
        indent = len(target_line_text) - len(target_line_text.lstrip())
        indent_str = target_line_text[:indent]

        formatted_lines = []
        for line in new_function_code.strip().splitlines():
            formatted_lines.append(indent_str + line if indent_str and not line.startswith(indent_str) else line)
        replacement_text = "\n".join(formatted_lines) + "\n"

        prefix = "".join(lines[:start_line])
        suffix = "".join(lines[end_line:])

        return prefix + replacement_text + suffix

    @classmethod
    def generate_diff(
        cls,
        original_code: str,
        patched_code: str,
        from_name: str = "original.py",
        to_name: str = "patched.py",
    ) -> str:
        """Generate a unified diff between original and patched code."""
        orig_lines = [l if l.endswith("\n") else l + "\n" for l in original_code.splitlines()]
        patch_lines = [l if l.endswith("\n") else l + "\n" for l in patched_code.splitlines()]

        diff = difflib.unified_diff(
            orig_lines,
            patch_lines,
            fromfile=from_name,
            tofile=to_name,
        )
        return "".join(diff)
