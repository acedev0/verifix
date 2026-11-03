"""Tests for Neurosymbolic CEGIS repair loop combining LLM and SMT."""

from verifix.core.engine import VeriFixEngine
from verifix.generator.mock_llm import MockLLMClient, ScriptedRepairClient
from verifix.verifier.smt_engine import SMTVerifier


def test_cegis_self_correction_multi_step():
    """Test that a flawed LLM proposal is rejected by SMT, and the concrete counterexample

    guides the LLM to synthesize a mathematically sound patch on the next step.
    """
    buggy_code = '''
def withdraw(balance: int, amount: int) -> int:
    """
    @requires balance >= 0
    @requires amount > 0
    @ensures return_val >= 0
    @ensures return_val == balance - amount if amount <= balance else return_val == balance
    """
    return balance - amount
'''

    # Flawed patch with off-by-one boundary bug (< instead of <=)
    flawed_patch = '''
```python
def withdraw(balance: int, amount: int) -> int:
    """
    @requires balance >= 0
    @requires amount > 0
    @ensures return_val >= 0
    @ensures return_val == balance - amount if amount <= balance else return_val == balance
    """
    if amount < balance:
        return balance - amount
    return balance
```
'''

    # Correct verified patch
    sound_patch = '''
```python
def withdraw(balance: int, amount: int) -> int:
    """
    @requires balance >= 0
    @requires amount > 0
    @ensures return_val >= 0
    @ensures return_val == balance - amount if amount <= balance else return_val == balance
    """
    if amount <= balance:
        return balance - amount
    return balance
```
'''

    mock_llm = MockLLMClient(responses=[flawed_patch, sound_patch])
    engine = VeriFixEngine(llm_client=mock_llm, verifier=SMTVerifier(), max_iterations=4)

    res = engine.repair(buggy_code, function_name="withdraw")

    assert res.success is True
    assert res.iterations == 2
    assert len(res.history) == 2

    # Verify iteration 1 had counterexample
    iter1_res = res.history[0].verification_result
    assert iter1_res is not None
    assert iter1_res.counterexample is not None
    assert iter1_res.counterexample.inputs["balance"] == iter1_res.counterexample.inputs["amount"]

    # Verify iteration 2 was formally verified
    iter2_res = res.history[1].verification_result
    assert iter2_res is not None
    assert iter2_res.is_verified is True

    # Check that mock LLM received the counterexample in the prompt for iteration 2
    prompt_iter2 = mock_llm.prompts_received[1]
    assert "CRITICAL SMT COUNTEREXAMPLE" in prompt_iter2


def test_cegis_syntax_error_recovery():
    """Test that engine handles syntax errors in LLM candidate and recovers."""
    buggy_code = '''
def compute(x: int) -> int:
    """
    @ensures return_val >= 0
    """
    return x
'''
    syntax_err_patch = "```python\ndef compute(x: int) -> int:\n    this is not valid python syntax !!!\n```"
    valid_patch = "```python\ndef compute(x: int) -> int:\n    return abs(x)\n```"

    mock_llm = MockLLMClient(responses=[syntax_err_patch, valid_patch])
    engine = VeriFixEngine(llm_client=mock_llm, verifier=SMTVerifier(), max_iterations=3)

    res = engine.repair(buggy_code, function_name="compute")
    assert res.success is True
    assert res.iterations == 2


def test_cegis_scripted_repair():
    """Test automatic heuristic repair when running without active LLM."""
    buggy_code = '''
def safe_mod(x: int, y: int) -> int:
    """
    @ensures return_val >= 0
    """
    return x // y
'''
    engine = VeriFixEngine(llm_client=ScriptedRepairClient(), verifier=SMTVerifier(), max_iterations=3)
    res = engine.repair(buggy_code, function_name="safe_mod")
    assert res.success is True
    assert res.verified_patch is not None
