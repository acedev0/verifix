"""Tests for the VeriFix CLI interface."""

import subprocess
import sys
import tempfile
import pytest

from verifix.cli import main, create_parser


def test_cli_parser():
    parser = create_parser()
    args = parser.parse_args(["verify", "dummy.py", "--func", "my_func"])
    assert args.command == "verify"
    assert args.file == "dummy.py"
    assert args.func == "my_func"


def test_cli_demo_execution():
    result = subprocess.run(
        [sys.executable, "-m", "verifix.cli", "demo"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "VeriFix: Neurosymbolic Code Vulnerability Patcher" in result.stdout
    assert "Verified Sound Patch:" in result.stdout


def test_cli_verify_file(tmp_path):
    safe_file = tmp_path / "safe.py"
    safe_file.write_text('''
def abs_val(x: int) -> int:
    """
    @ensures return_val >= 0
    """
    if x < 0:
        return -x
    return x
''')
    result = subprocess.run(
        [sys.executable, "-m", "verifix.cli", "verify", str(safe_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "PROVEN VERIFIED" in result.stdout


def test_cli_patch_file(tmp_path):
    buggy_file = tmp_path / "buggy.py"
    buggy_file.write_text('''
def withdraw(balance: int, amount: int) -> int:
    """
    @requires balance >= 0
    @requires amount > 0
    @ensures return_val >= 0
    """
    return balance - amount
''')
    result = subprocess.run(
        [sys.executable, "-m", "verifix.cli", "patch", str(buggy_file), "--in-place"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Successfully wrote verified patch" in result.stdout
    # Verify file content was patched
    new_content = buggy_file.read_text()
    assert "if amount > balance:" in new_content or "if amount <= balance:" in new_content
