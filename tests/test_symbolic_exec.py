"""Tests for symbolic execution engine."""

import ast
import z3
import pytest

from verifix.verifier.symbolic_exec import SymbolicExecutor
from verifix.core.types import Specification


def test_symbolic_exec_simple_arithmetic():
    code = """
def add_one(x: int) -> int:
    y = x + 1
    return y
"""
    func_node = ast.parse(code).body[0]
    executor = SymbolicExecutor()
    paths = executor.execute_function(func_node)

    assert len(paths) == 1
    path = paths[0]
    assert path.is_terminated
    assert path.return_val is not None

    # Verify return_val == x + 1
    x_sym = executor.initial_vars["x"]
    solver = z3.Solver()
    solver.add(path.return_val != x_sym + 1)
    assert solver.check() == z3.unsat


def test_symbolic_exec_branching():
    code = """
def abs_val(x: int) -> int:
    if x < 0:
        return -x
    else:
        return x
"""
    func_node = ast.parse(code).body[0]
    executor = SymbolicExecutor()
    paths = executor.execute_function(func_node)

    assert len(paths) == 2
    for path in paths:
        assert path.is_terminated
        assert path.return_val is not None


def test_symbolic_exec_builtins():
    code = """
def clamp(val: int, low: int, high: int) -> int:
    return min(max(val, low), high)
"""
    func_node = ast.parse(code).body[0]
    executor = SymbolicExecutor()
    paths = executor.execute_function(func_node)

    assert len(paths) == 1
    ret = paths[0].return_val
    assert ret is not None


def test_symbolic_exec_unreachable_branch_pruning():
    code = """
def unreachable(x: int) -> int:
    if x > 10:
        if x < 5:
            return 999  # Mathematically impossible branch
    return 0
"""
    func_node = ast.parse(code).body[0]
    executor = SymbolicExecutor()
    paths = executor.execute_function(func_node)

    # The branch x > 10 and x < 5 is unsat and must be pruned
    for path in paths:
        s = z3.Solver()
        for pc in path.path_condition:
            s.add(pc)
        s.add(path.return_val == 999)
        assert s.check() == z3.unsat


def test_symbolic_exec_while_loop():
    code = """
def sum_to_n(n: int) -> int:
    total = 0
    i = 0
    while i < 3:
        total = total + i
        i = i + 1
    return total
"""
    func_node = ast.parse(code).body[0]
    executor = SymbolicExecutor(max_loop_unroll=4)
    paths = executor.execute_function(func_node)

    assert len(paths) >= 1
    # Check that in the terminated path where loop completed, total == 3 (0 + 1 + 2)
    s = z3.Solver()
    s.add(paths[-1].return_val == 3)
    assert s.check() == z3.sat
