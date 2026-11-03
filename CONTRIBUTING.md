# Contributing to VeriFix

Thank you for your interest in contributing to **VeriFix**! We welcome contributions from formal methods researchers, software security practitioners, and compiler/AST enthusiasts.

---

## 🛠️ Development Setup

### 1. Clone the Repository
```bash
git clone https://github.com/acedev0/verifix.git
cd verifix
```

### 2. Set Up a Virtual Environment
```bash
python -m venv .venv
# On Linux / macOS:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate
```

### 3. Install in Editable Mode
```bash
pip install -e ".[dev,gemini]"
```

---

## 🧪 Running Tests

VeriFix uses `pytest` for rigorous formal testing across AST transformers, symbolic execution paths, and Z3 SMT queries:

```bash
# Run all tests
pytest tests -v

# Run with test coverage
pytest tests --cov=verifix --cov-report=term-missing

# Run specific verification tests
pytest tests/test_neurosymbolic_fixes.py -v
pytest tests/test_cegis_loop.py -v
```

Before submitting any pull request, verify that all tests pass:
```bash
pytest
```

---

## 📐 Architecture Overview

VeriFix is organized into focused submodules:

- **`verifix/core/`**:
  - `engine.py`: The `VeriFixEngine` coordinating the CEGIS loop.
  - `contract.py`: AST parser for `@requires`, `@ensures`, assertions, and docstrings.
  - `types.py`: Dataclasses for `VerificationResult`, `Counterexample`, `Specification`, etc.
- **`verifix/verifier/`**:
  - `smt_engine.py`: Z3 SMT solver wrapper that generates verification conditions (VCGen) and checks SAT/UNSAT.
  - `symbolic_exec.py`: AST symbolic execution engine tracking path conditions and symbolic memory state.
  - `safety_rules.py`: Pluggable safety invariants (division by zero, integer overflow, NoneType dereference, index out of bounds).
- **`verifix/generator/`**:
  - `prompt_builder.py`: CEGIS prompt synthesis embedding concrete counterexamples into LLM context.
  - `gemini.py`: Live Google Gemini generative LLM integration.
  - `mock_llm.py`: Offline deterministic/heuristic patch generator for tests and offline usage.
- **`verifix/patches/`**:
  - `ast_patcher.py`: Clean AST transplantation and syntax validation.
- **`verifix/cli.py`**:
  - Command-line interface (`verify`, `patch`, `demo`).

---

## 💡 Adding New Safety Rules

1. Open `verifix/verifier/safety_rules.py`.
2. Define a new `SafetyRule` checker subclass or rule method.
3. Express the safety condition as a Z3 Boolean formula (e.g., ensuring an index is strictly within array bounds `0 <= idx < len`).
4. Register the rule in `SafetyManager.register_default_rules()`.
5. Add corresponding unit tests in `tests/test_safety_rules.py`.

---

## 📜 Pull Request Process

1. Fork the repository and create your branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Write clean, readable code with type hints.
3. Add test coverage for any new behavior or bug fix.
4. Ensure the entire test suite passes without regressions:
   ```bash
   pytest
   ```
5. Submit a pull request detailing the problem, solution, and formal verification guarantees.

---

## ⚖️ Code of Conduct & Licensing

By contributing to VeriFix, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
