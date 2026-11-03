# VeriFix User Guide

Welcome to the **VeriFix User Guide**. This document walks you through installing, configuring, and leveraging VeriFix to formally verify Python code and repair vulnerabilities using neurosymbolic CEGIS.

---

## 📖 Table of Contents
1. [Installation](#installation)
2. [Defining Contracts & Specifications](#defining-contracts--specifications)
3. [CLI Reference](#cli-reference)
4. [Python API Reference](#python-api-reference)
5. [End-to-End Walkthroughs](#end-to-end-walkthroughs)
   - [Example 1: Account Withdrawal & Negative Balances](#example-1-account-withdrawal--negative-balances)
   - [Example 2: Division by Zero & Bounds](#example-2-division-by-zero--bounds)
   - [Example 3: NoneType Dereference Protection](#example-3-nonetype-dereference-protection)
6. [LLM Providers & Configuration](#llm-providers--configuration)
7. [Troubleshooting & FAQ](#troubleshooting--faq)

---

## 1. Installation

### Basic Setup
```bash
git clone https://github.com/acedev0/verifix.git
cd verifix
pip install -e .
```

### With Gemini LLM Support
```bash
pip install -e ".[gemini]"
```

Requirements:
- Python 3.10 or higher
- `z3-solver >= 4.12.0`
- `typing_extensions >= 4.5.0`

---

## 2. Defining Contracts & Specifications

VeriFix supports multiple expressive styles for specifying function preconditions, postconditions, and invariants.

### Style A: Docstring Tags (Recommended)
You can annotate function docstrings using `@requires` and `@ensures`:

```python
def safe_divide(numerator: int, denominator: int) -> int:
    """
    @requires denominator != 0
    @ensures return_val * denominator <= numerator
    """
    return numerator // denominator
```

- `@requires <expr>`: Precondition required of inputs.
- `@ensures <expr>`: Postcondition that must hold for return values (`return_val`).

### Style B: Decorators
Decorators provide clean, programmatic contract declarations:

```python
from verifix import requires, ensures

@requires("balance >= 0 and amount > 0")
@ensures("return_val >= 0")
def withdraw(balance: int, amount: int) -> int:
    if amount <= balance:
        return balance - amount
    return balance
```

### Style C: Native Inline Assertions
VeriFix automatically extracts inline `assert` statements as formal safety invariant checks during symbolic execution.

---

## 3. CLI Reference

VeriFix provides three primary commands: `demo`, `verify`, and `patch`.

### 1. `verifix demo`
Runs an interactive, self-contained CEGIS repair demonstration on an underflow-vulnerable withdrawal function:

```bash
verifix demo
```

### 2. `verifix verify`
Statically executes symbolic paths through the target function and queries the Z3 SMT solver for specification or safety violations:

```bash
verifix verify <file.py> [--func <function_name>] [--timeout <ms>] [--check-overflow]
```

**Options:**
- `file`: Path to the Python file.
- `--func`, `-f`: Name of the function to verify (default: first function in file).
- `--timeout`: SMT solver timeout in milliseconds (default: `5000`).
- `--check-overflow`: Enable 32-bit signed integer overflow checks.

**Exit Codes:**
- `0`: Verified mathematically sound (UNSAT for all violation conditions).
- `1`: Error parsing or solver failure.
- `2`: Vulnerability detected; counterexample produced.

### 3. `verifix patch`
Runs the dual-engine CEGIS loop to synthesize a formally verified patch:

```bash
verifix patch <file.py> [--func <function_name>] [--max-iters <int>] [--provider gemini|scripted] [--in-place]
```

**Options:**
- `--max-iters`, `-i`: Maximum CEGIS counterexample refinement rounds (default: `5`).
- `--provider`: LLM backend (`scripted` for deterministic local repair, `gemini` for live generative repair).
- `--in-place`, `-w`: Overwrite the original file with the verified patch.

---

## 4. Python API Reference

You can embed VeriFix directly into test suites, security scanners, or automated pipelines.

### Verification API:
```python
from verifix import SMTVerifier

code = """
def clamp(val: int, low: int, high: int) -> int:
    '''
    @requires low <= high
    @ensures return_val >= low and return_val <= high
    '''
    if val < low:
        return low
    return val  # Bug: missing high check!
"""

verifier = SMTVerifier()
result = verifier.verify_source(code, function_name="clamp")

if result.is_verified:
    print("Formally sound!")
else:
    print(f"Counterexample inputs: {result.counterexample.inputs}")
    # Output: {'val': 10, 'low': 0, 'high': 5, '[computed_return]': 10}
```

### Repair API:
```python
from verifix import VeriFixEngine

engine = VeriFixEngine(max_iterations=5)
repair_result = engine.repair(code, function_name="clamp")

if repair_result.success:
    print("Verified patch:")
    print(repair_result.verified_patch)
```

---

## 5. End-to-End Walkthroughs

### Example 1: Account Withdrawal & Negative Balances

**Target Code (`wallet.py`):**
```python
def withdraw(balance: int, amount: int) -> int:
    """
    @requires balance >= 0
    @requires amount > 0
    @ensures return_val >= 0
    @ensures return_val == balance - amount if amount <= balance else return_val == balance
    """
    return balance - amount
```

**Run VeriFix:**
```bash
verifix patch wallet.py --func withdraw --in-place
```

**Console Output:**
```
[Phase 1: Initial SMT Verification]
  * SMT Solver found concrete bug input: {'balance': 0, 'amount': 1, '[computed_return]': -1}
  * Description: Specification 'return_val >= 0' violated

[Phase 2.1: CEGIS Iteration 1]
  * Status: [BUG DETECTED (SAT CEX)]
  * Candidate rejected by Z3. Counterexample: {'balance': 1, 'amount': 1}

[Phase 2.2: CEGIS Iteration 2]
  * Status: [PROVEN VERIFIED (UNSAT)]
  * Formal Proof: Mathematically verified across 2 symbolic paths.
```

---

## 6. LLM Providers & Configuration

### Google Gemini Provider
To use Gemini for generative repair:
1. Obtain an API key from Google AI Studio.
2. Set the environment variable:
   ```bash
   # Linux / macOS
   export GEMINI_API_KEY="your-api-key"

   # Windows PowerShell
   $env:GEMINI_API_KEY="your-api-key"
   ```
3. Run with `--provider gemini`:
   ```bash
   verifix patch file.py --provider gemini
   ```

### Scripted Heuristic Provider (Offline Default)
When no API key is provided or when running offline / in CI pipelines, VeriFix uses its built-in rule-guided patch synthesizer. This requires zero network calls and runs deterministically.

---

## 7. Troubleshooting & FAQ

**Q: Why does the solver report an unknown status?**  
A: Nonlinear integer arithmetic or deep unbounded loops can cause SMT undecidability. Increase `--timeout` or provide explicit bounds in `@requires`.

**Q: Can VeriFix handle external libraries (e.g., NumPy, pandas)?**  
A: VeriFix models core Python semantics, control flow, arithmetic, bitwise operations, and collections. External C-extensions should be bounded or mocked via function contracts.

**Q: How does VeriFix guarantee no hallucinations in LLM output?**  
A: The LLM acts purely as a proposer. No patch is accepted without an UNSAT proof from Z3 across all symbolic execution branches.
