"""Contract decorators and AST specification extraction."""

from __future__ import annotations

import ast
import inspect
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from verifix.core.types import Specification


def requires(condition: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to attach a precondition to a function."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not hasattr(func, "__verifix_spec__"):
            func.__verifix_spec__ = Specification()
        func.__verifix_spec__.add_precondition(condition)
        return func
    return decorator


def ensures(condition: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to attach a postcondition to a function."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not hasattr(func, "__verifix_spec__"):
            func.__verifix_spec__ = Specification()
        func.__verifix_spec__.add_postcondition(condition)
        return func
    return decorator


def invariant(condition: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to attach an invariant to a function."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not hasattr(func, "__verifix_spec__"):
            func.__verifix_spec__ = Specification()
        func.__verifix_spec__.add_invariant(condition)
        return func
    return decorator


def safety(rule: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to attach a safety rule check to a function."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if not hasattr(func, "__verifix_spec__"):
            func.__verifix_spec__ = Specification()
        func.__verifix_spec__.enable_safety_check(rule)
        return func
    return decorator


class ContractExtractor:
    """Extracts formal contracts, specifications, and type signatures from AST and docstrings."""

    @classmethod
    def from_function_node(cls, func_node: ast.FunctionDef) -> Specification:
        """Extract specification from an ast.FunctionDef node."""
        spec = Specification()

        # 1. Extract parameter types
        for arg in func_node.args.args:
            arg_name = arg.arg
            if arg.annotation:
                spec.param_types[arg_name] = cls._annotation_to_type_str(arg.annotation)
            else:
                spec.param_types[arg_name] = "int"  # Default assumption for arithmetic

        if func_node.returns:
            spec.return_type = cls._annotation_to_type_str(func_node.returns)
        else:
            spec.return_type = "int"

        # 2. Extract docstring contracts
        docstring = ast.get_docstring(func_node)
        if docstring:
            cls._parse_docstring_contracts(docstring, spec)

        # 3. Extract decorator contracts from AST
        for decorator in func_node.decorator_list:
            cls._parse_decorator_node(decorator, spec)



        return spec

    @classmethod
    def from_source(cls, source_code: str, function_name: Optional[str] = None) -> Tuple[ast.FunctionDef, Specification]:
        """Parse source code, locate the target function, and extract its specification."""
        tree = ast.parse(source_code)
        all_funcs: List[ast.FunctionDef] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                all_funcs.append(node)

        target_fn: Optional[ast.FunctionDef] = None
        if function_name is not None:
            for fn in all_funcs:
                if fn.name == function_name:
                    target_fn = fn
                    break
            # Fallback: if single function exists in patch candidate
            if target_fn is None and len(all_funcs) == 1:
                target_fn = all_funcs[0]
                target_fn.name = function_name
        elif all_funcs:
            target_fn = all_funcs[0]

        if target_fn is None:
            raise ValueError(f"Function {function_name or '<first>'} not found in source code.")

        spec = cls.from_function_node(target_fn)
        return target_fn, spec

    @classmethod
    def _annotation_to_type_str(cls, node: ast.AST) -> str:
        """Convert an AST annotation into a canonical string."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Subscript):
            val = cls._annotation_to_type_str(node.value)
            sl = cls._annotation_to_type_str(node.slice)
            return f"{val}[{sl}]"
        elif isinstance(node, ast.Constant):
            return str(node.value)
        return ast.unparse(node)

    @classmethod
    def _parse_docstring_contracts(cls, docstring: str, spec: Specification) -> None:
        """Parse contracts from docstring tags like @requires, @ensures, @invariant, or @safety."""
        lines = docstring.strip().split("\n")
        for raw_line in lines:
            # Strip leading markdown bullets (-, *) or whitespace
            line = re.sub(r"^[-*]\s*", "", raw_line.strip()).strip()

            # Match @requires or requires: or @precondition
            m_req = re.match(r"^@?(?:requires|precondition):?\s+(.+)$", line, re.IGNORECASE)
            if m_req:
                spec.add_precondition(m_req.group(1).strip())
                continue

            # Match @ensures or ensures: or @postcondition
            m_ens = re.match(r"^@?(?:ensures|postcondition):?\s+(.+)$", line, re.IGNORECASE)
            if m_ens:
                spec.add_postcondition(m_ens.group(1).strip())
                continue

            # Match @invariant or invariant:
            m_inv = re.match(r"^@?invariant:?\s+(.+)$", line, re.IGNORECASE)
            if m_inv:
                spec.add_invariant(m_inv.group(1).strip())
                continue

            # Match @safety or safety:
            m_safe = re.match(r"^@?safety:?\s+(.+)$", line, re.IGNORECASE)
            if m_safe:
                rule_name = m_safe.group(1).strip().lower()
                spec.enable_safety_check(rule_name)
                continue

    @classmethod
    def _parse_decorator_node(cls, node: ast.AST, spec: Specification) -> None:
        """Parse decorator calls like @requires("x > 0"), @safety("no_integer_overflow") from AST."""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            dec_name = node.func.id
            if dec_name == "requires" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    spec.add_precondition(arg.value)
            elif dec_name == "ensures" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    spec.add_postcondition(arg.value)
            elif dec_name == "invariant" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    spec.add_invariant(arg.value)
            elif dec_name == "safety" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    spec.enable_safety_check(arg.value.lower())

    @classmethod
    def _infer_assert_contracts(cls, func_node: ast.FunctionDef, spec: Specification) -> None:
        """Inspect top-level assert statements to infer contracts if none provided."""
        # Preconditions: leading asserts before any state mutations
        body = [stmt for stmt in func_node.body if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))]
        for stmt in body:
            if isinstance(stmt, ast.Assert):
                expr_str = ast.unparse(stmt.test)
                if expr_str not in spec.preconditions and not spec.preconditions:
                    spec.add_precondition(expr_str)
            else:
                break
