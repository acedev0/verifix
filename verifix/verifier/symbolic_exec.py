"""Symbolic execution engine for Python AST translating statements to Z3 formulas."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
import z3

from verifix.core.types import Specification
from verifix.verifier.safety_rules import SafetyCheck, SafetyRuleManager


@dataclass
class ExecutionPath:
    """A symbolic execution path with path constraints, environment, and checks."""
    path_condition: List[z3.BoolRef] = field(default_factory=list)
    env: Dict[str, z3.ExprRef] = field(default_factory=dict)
    array_lens: Dict[str, z3.ExprRef] = field(default_factory=dict)
    none_flags: Dict[str, z3.BoolRef] = field(default_factory=dict)
    return_val: Optional[z3.ExprRef] = None
    is_terminated: bool = False
    is_loop_broken: bool = False
    is_loop_continued: bool = False
    safety_checks: List[SafetyCheck] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)

    def fork(self) -> ExecutionPath:
        """Create a deep copy of the execution path for branching."""
        return ExecutionPath(
            path_condition=list(self.path_condition),
            env=dict(self.env),
            array_lens=dict(self.array_lens),
            none_flags=dict(self.none_flags),
            return_val=self.return_val,
            is_terminated=self.is_terminated,
            is_loop_broken=self.is_loop_broken,
            is_loop_continued=self.is_loop_continued,
            safety_checks=list(self.safety_checks),
            trace=list(self.trace),
        )

    def current_pc_formula(self) -> z3.BoolRef:
        """Combine all path condition constraints into a single Z3 formula."""
        if not self.path_condition:
            return z3.BoolVal(True)
        return z3.And(*self.path_condition)


class SymbolicExecutor:
    """Symbolically executes Python AST functions and produces Z3 formulas."""

    def __init__(
        self,
        spec: Optional[Specification] = None,
        max_loop_unroll: int = 5,
        safety_manager: Optional[SafetyRuleManager] = None,
    ) -> None:
        self.spec = spec or Specification()
        self.max_loop_unroll = max_loop_unroll
        self.safety_manager = safety_manager or SafetyRuleManager()
        self.initial_vars: Dict[str, z3.ExprRef] = {}
        self.initial_lens: Dict[str, z3.ExprRef] = {}
        self.initial_nones: Dict[str, z3.BoolRef] = {}

    def initialize_inputs(self, func_node: ast.FunctionDef) -> ExecutionPath:
        """Create symbolic variables for function arguments and set up initial state."""
        path = ExecutionPath()
        self.initial_vars.clear()
        self.initial_lens.clear()
        self.initial_nones.clear()

        # Check for default arguments with None
        default_map: Dict[str, bool] = {}
        defaults = func_node.args.defaults
        num_defaults = len(defaults)
        if num_defaults > 0:
            for arg, default_val in zip(func_node.args.args[-num_defaults:], defaults):
                if isinstance(default_val, ast.Constant) and default_val.value is None:
                    default_map[arg.arg] = True

        for arg in func_node.args.args:
            name = arg.arg
            type_str = self.spec.param_types.get(name, "int").lower()
            is_nullable = (
                "optional" in type_str
                or "none" in type_str
                or "|" in type_str
                or default_map.get(name, False)
            )

            if is_nullable:
                none_sym = z3.Bool(f"{name}_is_none")
                path.none_flags[name] = none_sym
                self.initial_nones[name] = none_sym
            else:
                path.none_flags[name] = z3.BoolVal(False)

            if "list" in type_str or "array" in type_str:
                arr_sym = z3.Array(name, z3.IntSort(), z3.IntSort())
                len_sym = z3.Int(f"{name}_len")
                path.env[name] = arr_sym
                path.array_lens[name] = len_sym
                # Constraint: array length >= 0
                path.path_condition.append(len_sym >= 0)
                if is_nullable:
                    path.path_condition.append(z3.Implies(path.none_flags[name], len_sym == 0))
                self.initial_vars[name] = arr_sym
                self.initial_lens[name] = len_sym
            elif "bool" in type_str:
                sym = z3.Bool(name)
                path.env[name] = sym
                self.initial_vars[name] = sym
            elif "float" in type_str:
                sym = z3.Real(name)
                path.env[name] = sym
                self.initial_vars[name] = sym
            else:
                # Default to integer
                sym = z3.Int(name)
                path.env[name] = sym
                self.initial_vars[name] = sym

        # Add explicit preconditions from specification to path condition
        for pre in self.spec.preconditions:
            try:
                pre_expr = self.eval_condition_str(pre, path)
                if pre_expr is not None:
                    path.path_condition.append(pre_expr)
                    path.trace.append(f"Precondition assumed: {pre}")
            except Exception:
                pass

        return path

    def eval_condition_str(self, cond_str: str, state: ExecutionPath) -> Optional[z3.BoolRef]:
        """Parse and evaluate a condition string (e.g., 'x > 0 and y <= 10') in state environment."""
        parsed = ast.parse(cond_str, mode="eval")
        res = self._eval_expr(parsed.body, state)
        if isinstance(res, z3.BoolRef):
            return res
        return None

    def execute_function(self, func_node: ast.FunctionDef) -> List[ExecutionPath]:
        """Symbolically execute the entire function, returning all resulting paths."""
        initial_state = self.initialize_inputs(func_node)
        active_paths = [initial_state]

        for stmt in func_node.body:
            # Skip docstrings
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue

            new_active_paths: List[ExecutionPath] = []
            for path in active_paths:
                if path.is_terminated:
                    new_active_paths.append(path)
                else:
                    stepped_paths = self._exec_stmt(stmt, path)
                    for sp in stepped_paths:
                        # Prune unreachable paths
                        if self._is_path_feasible(sp):
                            new_active_paths.append(sp)
            active_paths = new_active_paths

        # If a path reached the end without explicit return, set return_val = None or 0
        for path in active_paths:
            if not path.is_terminated:
                path.return_val = z3.IntVal(0)
                path.is_terminated = True

        return active_paths

    def _is_path_feasible(self, path: ExecutionPath) -> bool:
        """Check if path condition is satisfiable."""
        if not path.path_condition:
            return True
        solver = z3.Solver()
        solver.set("timeout", 1000)
        for pc in path.path_condition:
            solver.add(pc)
        return solver.check() != z3.unsat

    def _exec_stmt(self, stmt: ast.stmt, state: ExecutionPath) -> List[ExecutionPath]:
        """Execute a single AST statement on an execution path."""
        if state.is_terminated or state.is_loop_broken or state.is_loop_continued:
            return [state]

        if isinstance(stmt, ast.Return):
            if stmt.value is not None:
                state.return_val = self._eval_expr(stmt.value, state)
            else:
                state.return_val = z3.IntVal(0)
            state.is_terminated = True
            state.trace.append(f"Return: {ast.unparse(stmt.value) if stmt.value else 'None'}")
            return [state]

        elif isinstance(stmt, ast.Break):
            state.is_loop_broken = True
            state.trace.append("Break loop")
            return [state]

        elif isinstance(stmt, ast.Continue):
            state.is_loop_continued = True
            state.trace.append("Continue loop")
            return [state]

        elif isinstance(stmt, ast.Assign):
            val = self._eval_expr(stmt.value, state)
            is_none_val = (val is None) or (isinstance(stmt.value, ast.Constant) and stmt.value.value is None)

            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    if is_none_val:
                        state.none_flags[target.id] = z3.BoolVal(True)
                        state.env[target.id] = z3.Int(f"{target.id}_none")
                    else:
                        if isinstance(stmt.value, ast.Name) and stmt.value.id in state.none_flags:
                            state.none_flags[target.id] = state.none_flags[stmt.value.id]
                        else:
                            state.none_flags[target.id] = z3.BoolVal(False)
                        state.env[target.id] = val if val is not None else z3.IntVal(0)
                    state.trace.append(f"Assign {target.id} = {ast.unparse(stmt.value)}")

                elif isinstance(target, ast.Subscript):
                    # Array element assignment: arr[idx] = val
                    if isinstance(target.value, ast.Name):
                        arr_name = target.value.id
                        # Check NoneType dereference
                        if arr_name in state.none_flags:
                            chk = self.safety_manager.check_none_dereference(
                                arr_name, state.none_flags[arr_name], target, state.current_pc_formula()
                            )
                            if chk:
                                state.safety_checks.append(chk)

                        idx_expr = self._eval_expr(target.slice, state)
                        # Bounds check
                        if arr_name in state.array_lens:
                            arr_len = state.array_lens[arr_name]
                            bounds_check = self.safety_manager.check_array_bounds(
                                idx_expr, arr_len, target, state.current_pc_formula(), arr_name=arr_name
                            )
                            if bounds_check:
                                state.safety_checks.append(bounds_check)
                        if arr_name in state.env:
                            store_val = val if val is not None else z3.IntVal(0)
                            state.env[arr_name] = z3.Store(state.env[arr_name], idx_expr, store_val)
                            state.trace.append(f"Array Store: {arr_name}[{ast.unparse(target.slice)}] = {ast.unparse(stmt.value)}")

                elif isinstance(target, ast.Attribute):
                    if isinstance(target.value, ast.Name):
                        obj_name = target.value.id
                        if obj_name in state.none_flags:
                            chk = self.safety_manager.check_none_dereference(
                                obj_name, state.none_flags[obj_name], target, state.current_pc_formula()
                            )
                            if chk:
                                state.safety_checks.append(chk)
                        store_val = val if val is not None else z3.IntVal(0)
                        state.env[f"{obj_name}_{target.attr}"] = store_val
            return [state]

        elif isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name):
                target_id = stmt.target.id
                if target_id in state.none_flags:
                    chk = self.safety_manager.check_none_dereference(
                        target_id, state.none_flags[target_id], stmt, state.current_pc_formula()
                    )
                    if chk:
                        state.safety_checks.append(chk)
                curr_val = state.env.get(target_id, z3.Int(target_id))
                rhs_val = self._eval_expr(stmt.value, state)
                new_val = self._apply_binary_op(stmt.op, curr_val, rhs_val, stmt, state)
                state.env[target_id] = new_val
                state.trace.append(f"AugAssign {target_id} {type(stmt.op).__name__}= {ast.unparse(stmt.value)}")

            elif isinstance(stmt.target, ast.Subscript) and isinstance(stmt.target.value, ast.Name):
                arr_name = stmt.target.value.id
                if arr_name in state.none_flags:
                    chk = self.safety_manager.check_none_dereference(
                        arr_name, state.none_flags[arr_name], stmt, state.current_pc_formula()
                    )
                    if chk:
                        state.safety_checks.append(chk)
                idx_expr = self._eval_expr(stmt.target.slice, state)
                if arr_name in state.array_lens:
                    arr_len = state.array_lens[arr_name]
                    bounds_chk = self.safety_manager.check_array_bounds(
                        idx_expr, arr_len, stmt, state.current_pc_formula(), arr_name=arr_name
                    )
                    if bounds_chk:
                        state.safety_checks.append(bounds_chk)
                curr_val = z3.Select(state.env[arr_name], idx_expr)
                rhs_val = self._eval_expr(stmt.value, state)
                new_val = self._apply_binary_op(stmt.op, curr_val, rhs_val, stmt, state)
                state.env[arr_name] = z3.Store(state.env[arr_name], idx_expr, new_val)
            return [state]

        elif isinstance(stmt, ast.If):
            test_cond = self._eval_expr(stmt.test, state)
            if not isinstance(test_cond, z3.BoolRef):
                test_cond = (test_cond != 0)

            # Check if this is a None check like `x is None` or `x is not None`
            none_tested_var: Optional[str] = None
            is_none_check = False
            is_not_none_check = False
            if isinstance(stmt.test, ast.Compare) and len(stmt.test.ops) == 1 and len(stmt.test.comparators) == 1:
                cmp_op = stmt.test.ops[0]
                left_node = stmt.test.left
                right_node = stmt.test.comparators[0]
                if isinstance(left_node, ast.Name) and isinstance(right_node, ast.Constant) and right_node.value is None:
                    none_tested_var = left_node.id
                    is_none_check = isinstance(cmp_op, (ast.Is, ast.Eq))
                    is_not_none_check = isinstance(cmp_op, (ast.IsNot, ast.NotEq))
                elif isinstance(right_node, ast.Name) and isinstance(left_node, ast.Constant) and left_node.value is None:
                    none_tested_var = right_node.id
                    is_none_check = isinstance(cmp_op, (ast.Is, ast.Eq))
                    is_not_none_check = isinstance(cmp_op, (ast.IsNot, ast.NotEq))

            # Fork Then branch
            then_path = state.fork()
            then_path.path_condition.append(test_cond)
            if none_tested_var:
                if is_none_check:
                    then_path.none_flags[none_tested_var] = z3.BoolVal(True)
                elif is_not_none_check:
                    then_path.none_flags[none_tested_var] = z3.BoolVal(False)

            then_path.trace.append(f"Branch [True]: {ast.unparse(stmt.test)}")

            then_results: List[ExecutionPath] = [then_path]
            for s in stmt.body:
                temp: List[ExecutionPath] = []
                for p in then_results:
                    if p.is_terminated or p.is_loop_broken or p.is_loop_continued:
                        temp.append(p)
                    else:
                        temp.extend(self._exec_stmt(s, p))
                then_results = temp

            # Fork Else branch
            else_path = state.fork()
            else_path.path_condition.append(z3.Not(test_cond))
            if none_tested_var:
                if is_none_check:
                    else_path.none_flags[none_tested_var] = z3.BoolVal(False)
                elif is_not_none_check:
                    else_path.none_flags[none_tested_var] = z3.BoolVal(True)

            else_path.trace.append(f"Branch [False]: not ({ast.unparse(stmt.test)})")

            else_results: List[ExecutionPath] = [else_path]
            for s in stmt.orelse:
                temp = []
                for p in else_results:
                    if p.is_terminated or p.is_loop_broken or p.is_loop_continued:
                        temp.append(p)
                    else:
                        temp.extend(self._exec_stmt(s, p))
                else_results = temp

            return then_results + else_results

        elif isinstance(stmt, ast.Assert):
            test_cond = self._eval_expr(stmt.test, state)
            if not isinstance(test_cond, z3.BoolRef):
                test_cond = (test_cond != 0)
            msg = ast.unparse(stmt.msg) if stmt.msg else f"Assert failed: {ast.unparse(stmt.test)}"
            chk = self.safety_manager.check_assertion(test_cond, stmt, state.current_pc_formula(), msg)
            if chk:
                state.safety_checks.append(chk)
            # After assert, condition is assumed to hold on remaining path
            state.path_condition.append(test_cond)
            return [state]

        elif isinstance(stmt, ast.While):
            return self._unroll_while_loop(stmt, state)

        elif isinstance(stmt, ast.For):
            return self._unroll_for_loop(stmt, state)

        elif isinstance(stmt, (ast.Pass, ast.Expr)):
            if isinstance(stmt, ast.Expr):
                self._eval_expr(stmt.value, state)
            return [state]

        # Unsupported or pass-through statements
        return [state]

    def _unroll_while_loop(self, loop: ast.While, state: ExecutionPath) -> List[ExecutionPath]:
        """Bounded loop unrolling for while loops."""
        completed_paths: List[ExecutionPath] = []
        current_paths = [state]

        for _ in range(self.max_loop_unroll):
            next_paths: List[ExecutionPath] = []
            for path in current_paths:
                if path.is_terminated or path.is_loop_broken:
                    path.is_loop_broken = False
                    completed_paths.append(path)
                    continue

                cond = self._eval_expr(loop.test, path)
                if not isinstance(cond, z3.BoolRef):
                    cond = (cond != 0)

                # Exit loop branch
                exit_path = path.fork()
                exit_path.path_condition.append(z3.Not(cond))
                completed_paths.append(exit_path)

                # Loop body branch
                iter_path = path.fork()
                iter_path.path_condition.append(cond)
                iter_results = [iter_path]
                for stmt in loop.body:
                    t = []
                    for p in iter_results:
                        if p.is_terminated or p.is_loop_broken:
                            t.append(p)
                        elif p.is_loop_continued:
                            p.is_loop_continued = False
                            t.append(p)
                        else:
                            t.extend(self._exec_stmt(stmt, p))
                    iter_results = t

                for p in iter_results:
                    if p.is_terminated or p.is_loop_broken:
                        p.is_loop_broken = False
                        completed_paths.append(p)
                    else:
                        p.is_loop_continued = False
                        next_paths.append(p)

            current_paths = next_paths
            if not current_paths:
                break

        completed_paths.extend(current_paths)
        return completed_paths

    def _unroll_for_loop(self, loop: ast.For, state: ExecutionPath) -> List[ExecutionPath]:
        """Bounded unrolling for loops over range(...) or lists."""
        if isinstance(loop.iter, ast.Call) and isinstance(loop.iter.func, ast.Name) and loop.iter.func.id == "range":
            args = loop.iter.args
            start_node: ast.expr = ast.Constant(value=0)
            step_node: ast.expr = ast.Constant(value=1)

            if len(args) == 1:
                stop_node = args[0]
            elif len(args) == 2:
                start_node = args[0]
                stop_node = args[1]
            elif len(args) == 3:
                start_node = args[0]
                stop_node = args[1]
                step_node = args[2]
            else:
                return [state]

            # Desugar into while loop:
            # target = start
            # while target < stop:
            #     body
            #     target += step
            target_name = loop.target.id if isinstance(loop.target, ast.Name) else "_i"
            init_stmt = ast.Assign(targets=[ast.Name(id=target_name, ctx=ast.Store())], value=start_node)
            test_node = ast.Compare(
                left=ast.Name(id=target_name, ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[stop_node],
            )
            step_stmt = ast.AugAssign(
                target=ast.Name(id=target_name, ctx=ast.Store()),
                op=ast.Add(),
                value=step_node,
            )
            while_node = ast.While(test=test_node, body=loop.body + [step_stmt], orelse=[])

            init_paths = self._exec_stmt(init_stmt, state)
            all_res = []
            for ip in init_paths:
                all_res.extend(self._unroll_while_loop(while_node, ip))
            return all_res

        elif isinstance(loop.iter, ast.Name) and loop.iter.id in state.array_lens:
            # Desugar for item in arr:
            # _idx = 0
            # while _idx < len(arr):
            #     item = arr[_idx]
            #     body
            #     _idx += 1
            arr_name = loop.iter.id
            target_name = loop.target.id if isinstance(loop.target, ast.Name) else "_elem"
            idx_name = f"_{target_name}_idx"

            init_stmt = ast.Assign(
                targets=[ast.Name(id=idx_name, ctx=ast.Store())],
                value=ast.Constant(value=0),
            )
            test_node = ast.Compare(
                left=ast.Name(id=idx_name, ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[ast.Call(func=ast.Name(id="len", ctx=ast.Load()), args=[ast.Name(id=arr_name, ctx=ast.Load())], keywords=[])],
            )
            fetch_stmt = ast.Assign(
                targets=[ast.Name(id=target_name, ctx=ast.Store())],
                value=ast.Subscript(
                    value=ast.Name(id=arr_name, ctx=ast.Load()),
                    slice=ast.Name(id=idx_name, ctx=ast.Load()),
                    ctx=ast.Load(),
                ),
            )
            step_stmt = ast.AugAssign(
                target=ast.Name(id=idx_name, ctx=ast.Store()),
                op=ast.Add(),
                value=ast.Constant(value=1),
            )
            while_node = ast.While(test=test_node, body=[fetch_stmt] + loop.body + [step_stmt], orelse=[])

            init_paths = self._exec_stmt(init_stmt, state)
            all_res = []
            for ip in init_paths:
                all_res.extend(self._unroll_while_loop(while_node, ip))
            return all_res

        return [state]

    def _eval_expr(self, node: ast.AST, state: ExecutionPath) -> Any:
        """Evaluate an AST expression node into a Z3 expression."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return z3.BoolVal(node.value)
            elif isinstance(node.value, int):
                return z3.IntVal(node.value)
            elif isinstance(node.value, float):
                return z3.RealVal(node.value)
            elif node.value is None:
                return None
            return z3.IntVal(0)

        elif isinstance(node, ast.Name):
            if node.id in state.env:
                return state.env[node.id]
            new_sym = z3.Int(node.id)
            state.env[node.id] = new_sym
            return new_sym

        elif isinstance(node, ast.Attribute):
            obj_name = node.value.id if isinstance(node.value, ast.Name) else None
            if obj_name and obj_name in state.none_flags:
                chk = self.safety_manager.check_none_dereference(
                    obj_name, state.none_flags[obj_name], node, state.current_pc_formula()
                )
                if chk:
                    state.safety_checks.append(chk)
            attr_key = f"{obj_name}_{node.attr}" if obj_name else "attr_val"
            if attr_key not in state.env:
                state.env[attr_key] = z3.Int(attr_key)
            return state.env[attr_key]

        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.operand, ast.Name) and node.operand.id in state.none_flags:
                chk = self.safety_manager.check_none_dereference(
                    node.operand.id, state.none_flags[node.operand.id], node, state.current_pc_formula()
                )
                if chk:
                    state.safety_checks.append(chk)

            operand = self._eval_expr(node.operand, state)
            if operand is None:
                return z3.IntVal(0)

            if isinstance(node.op, ast.USub):
                return -operand
            elif isinstance(node.op, ast.Not):
                if isinstance(operand, z3.BoolRef):
                    return z3.Not(operand)
                return operand == 0
            elif isinstance(node.op, ast.UAdd):
                return operand
            elif isinstance(node.op, ast.Invert):
                bv = self._to_bitvec(operand)
                return self._from_bitvec(~bv)

        elif isinstance(node, ast.BinOp):
            if isinstance(node.left, ast.Name) and node.left.id in state.none_flags:
                chk = self.safety_manager.check_none_dereference(
                    node.left.id, state.none_flags[node.left.id], node, state.current_pc_formula()
                )
                if chk:
                    state.safety_checks.append(chk)
            if isinstance(node.right, ast.Name) and node.right.id in state.none_flags:
                chk = self.safety_manager.check_none_dereference(
                    node.right.id, state.none_flags[node.right.id], node, state.current_pc_formula()
                )
                if chk:
                    state.safety_checks.append(chk)

            left = self._eval_expr(node.left, state)
            right = self._eval_expr(node.right, state)
            left_val = left if left is not None else z3.IntVal(0)
            right_val = right if right is not None else z3.IntVal(0)
            return self._apply_binary_op(node.op, left_val, right_val, node, state)

        elif isinstance(node, ast.BoolOp):
            vals = [self._eval_expr(val, state) for val in node.values]
            bool_vals = [v if isinstance(v, z3.BoolRef) else (v != 0) for v in vals if v is not None]
            if not bool_vals:
                return z3.BoolVal(True)
            if isinstance(node.op, ast.And):
                return z3.And(*bool_vals)
            elif isinstance(node.op, ast.Or):
                return z3.Or(*bool_vals)

        elif isinstance(node, ast.Compare):
            # Check for None comparisons: e.g. x is None, x is not None, x == None, x != None
            if len(node.ops) == 1 and len(node.comparators) == 1:
                cmp_op = node.ops[0]
                left_n = node.left
                right_n = node.comparators[0]
                if isinstance(left_n, ast.Name) and isinstance(right_n, ast.Constant) and right_n.value is None:
                    none_flag = state.none_flags.get(left_n.id, z3.BoolVal(False))
                    if isinstance(cmp_op, (ast.Is, ast.Eq)):
                        return none_flag
                    elif isinstance(cmp_op, (ast.IsNot, ast.NotEq)):
                        return z3.Not(none_flag)
                elif isinstance(right_n, ast.Name) and isinstance(left_n, ast.Constant) and left_n.value is None:
                    none_flag = state.none_flags.get(right_n.id, z3.BoolVal(False))
                    if isinstance(cmp_op, (ast.Is, ast.Eq)):
                        return none_flag
                    elif isinstance(cmp_op, (ast.IsNot, ast.NotEq)):
                        return z3.Not(none_flag)

            left = self._eval_expr(node.left, state)
            comparisons: List[z3.BoolRef] = []
            curr_left = left if left is not None else z3.IntVal(0)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval_expr(comparator, state)
                curr_right = right if right is not None else z3.IntVal(0)
                cmp_res = self._apply_compare_op(op, curr_left, curr_right)
                comparisons.append(cmp_res)
                curr_left = curr_right

            if len(comparisons) == 1:
                return comparisons[0]
            return z3.And(*comparisons)

        elif isinstance(node, ast.IfExp):
            # Ternary expression: body if test else orelse
            test_cond = self._eval_expr(node.test, state)
            if not isinstance(test_cond, z3.BoolRef):
                test_cond = (test_cond != 0)
            body_val = self._eval_expr(node.body, state)
            else_val = self._eval_expr(node.orelse, state)
            b_val = body_val if body_val is not None else z3.IntVal(0)
            e_val = else_val if else_val is not None else z3.IntVal(0)
            return z3.If(test_cond, b_val, e_val)

        elif isinstance(node, ast.Subscript):
            # Array lookup: arr[idx]
            if isinstance(node.value, ast.Name):
                arr_name = node.value.id
                if arr_name in state.none_flags:
                    chk = self.safety_manager.check_none_dereference(
                        arr_name, state.none_flags[arr_name], node, state.current_pc_formula()
                    )
                    if chk:
                        state.safety_checks.append(chk)

                idx_expr = self._eval_expr(node.slice, state)
                if arr_name in state.array_lens:
                    arr_len = state.array_lens[arr_name]
                    bounds_chk = self.safety_manager.check_array_bounds(
                        idx_expr, arr_len, node, state.current_pc_formula(), arr_name=arr_name
                    )
                    if bounds_chk:
                        state.safety_checks.append(bounds_chk)
                if arr_name in state.env:
                    return z3.Select(state.env[arr_name], idx_expr)
            return z3.IntVal(0)

        elif isinstance(node, ast.Call):
            return self._eval_call(node, state)

        return z3.IntVal(0)

    def _eval_call(self, call_node: ast.Call, state: ExecutionPath) -> Any:
        """Evaluate function calls and method calls symbolically."""
        if isinstance(call_node.func, ast.Name):
            func_name = call_node.func.id
            args = [self._eval_expr(a, state) for a in call_node.args]

            if func_name == "abs" and len(args) == 1:
                x = args[0]
                return z3.If(x >= 0, x, -x)
            elif func_name == "min" and len(args) >= 2:
                res = args[0]
                for other in args[1:]:
                    res = z3.If(res <= other, res, other)
                return res
            elif func_name == "max" and len(args) >= 2:
                res = args[0]
                for other in args[1:]:
                    res = z3.If(res >= other, res, other)
                return res
            elif func_name == "len" and len(args) == 1:
                if isinstance(call_node.args[0], ast.Name):
                    name = call_node.args[0].id
                    if name in state.none_flags:
                        chk = self.safety_manager.check_none_dereference(
                            name, state.none_flags[name], call_node, state.current_pc_formula()
                        )
                        if chk:
                            state.safety_checks.append(chk)
                    if name in state.array_lens:
                        return state.array_lens[name]
                return z3.IntVal(0)
            elif func_name == "implies" and len(args) == 2:
                p, q = args[0], args[1]
                p_b = p if isinstance(p, z3.BoolRef) else (p != 0)
                q_b = q if isinstance(q, z3.BoolRef) else (q != 0)
                return z3.Implies(p_b, q_b)

        elif isinstance(call_node.func, ast.Attribute):
            # Method call e.g. obj.pop(), obj.append(...)
            if isinstance(call_node.func.value, ast.Name):
                obj_name = call_node.func.value.id
                if obj_name in state.none_flags:
                    chk = self.safety_manager.check_none_dereference(
                        obj_name, state.none_flags[obj_name], call_node, state.current_pc_formula()
                    )
                    if chk:
                        state.safety_checks.append(chk)

        return z3.IntVal(0)

    def _apply_binary_op(
        self,
        op: ast.operator,
        left: z3.ExprRef,
        right: z3.ExprRef,
        node: ast.AST,
        state: ExecutionPath,
    ) -> z3.ExprRef:
        """Apply binary operator and register safety obligations."""
        res_expr: z3.ExprRef

        if isinstance(op, ast.Add):
            res_expr = left + right
        elif isinstance(op, ast.Sub):
            res_expr = left - right
        elif isinstance(op, ast.Mult):
            res_expr = left * right
        elif isinstance(op, ast.Div):
            chk = self.safety_manager.check_division_by_zero(right, node, state.current_pc_formula())
            if chk:
                state.safety_checks.append(chk)
            res_expr = left / right
        elif isinstance(op, ast.FloorDiv):
            chk = self.safety_manager.check_division_by_zero(right, node, state.current_pc_formula())
            if chk:
                state.safety_checks.append(chk)
            res_expr = left / right
        elif isinstance(op, ast.Mod):
            chk = self.safety_manager.check_division_by_zero(right, node, state.current_pc_formula())
            if chk:
                state.safety_checks.append(chk)
            res_expr = left % right
        elif isinstance(op, ast.BitAnd):
            bv_l = self._to_bitvec(left)
            bv_r = self._to_bitvec(right)
            return self._from_bitvec(bv_l & bv_r)
        elif isinstance(op, ast.BitOr):
            bv_l = self._to_bitvec(left)
            bv_r = self._to_bitvec(right)
            return self._from_bitvec(bv_l | bv_r)
        elif isinstance(op, ast.BitXor):
            bv_l = self._to_bitvec(left)
            bv_r = self._to_bitvec(right)
            return self._from_bitvec(bv_l ^ bv_r)
        elif isinstance(op, ast.LShift):
            bv_l = self._to_bitvec(left)
            bv_r = self._to_bitvec(right)
            res_expr = self._from_bitvec(bv_l << bv_r)
        elif isinstance(op, ast.RShift):
            bv_l = self._to_bitvec(left)
            bv_r = self._to_bitvec(right)
            return self._from_bitvec(bv_l >> bv_r)
        elif isinstance(op, ast.Pow):
            res_expr = left ** right
        else:
            res_expr = left + right

        # Check integer overflow if enabled
        if "no_integer_overflow" in self.safety_manager.enabled_rules and isinstance(op, (ast.Add, ast.Sub, ast.Mult, ast.LShift, ast.Pow)):
            chk = self.safety_manager.check_integer_overflow_32bit(res_expr, node, state.current_pc_formula())
            if chk:
                state.safety_checks.append(chk)

        return res_expr

    def _to_bitvec(self, expr: Any, bits: int = 32) -> z3.BitVecRef:
        """Convert an int or Z3 expression to BitVec."""
        if isinstance(expr, int):
            return z3.BitVecVal(expr, bits)
        elif isinstance(expr, z3.ArithRef):
            return z3.Int2BV(expr, bits)
        elif isinstance(expr, z3.BitVecRef):
            return expr
        return z3.BitVecVal(0, bits)

    def _from_bitvec(self, bv: z3.BitVecRef) -> z3.ArithRef:
        """Convert a BitVec back to signed integer ArithRef."""
        return z3.BV2Int(bv, is_signed=True)

    def _apply_compare_op(
        self,
        op: ast.cmpop,
        left: z3.ExprRef,
        right: z3.ExprRef,
    ) -> z3.BoolRef:
        """Apply comparison operator between two expressions."""
        if isinstance(op, ast.Eq):
            return left == right
        elif isinstance(op, ast.NotEq):
            return left != right
        elif isinstance(op, ast.Lt):
            return left < right
        elif isinstance(op, ast.LtE):
            return left <= right
        elif isinstance(op, ast.Gt):
            return left > right
        elif isinstance(op, ast.GtE):
            return left >= right
        elif isinstance(op, ast.Is):
            return left == right
        elif isinstance(op, ast.IsNot):
            return left != right
        return left == right
