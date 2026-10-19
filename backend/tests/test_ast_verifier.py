import pytest
from app.services.ast_verifier import (
    check_test_has_assertions,
    js_has_signature,
    python_ast_has_signature,
    strip_js_comments_and_strings,
)


def test_ast_rejects_signature_in_comment_or_string() -> None:
    code_with_comment = """
# Tutorial: You can run from flask import Flask, jsonify
def my_func():
    msg = "Call @app.route('/login') here"
    return msg
"""
    # The signature only appears in comment or string literal, so AST must return False
    res = python_ast_has_signature(code_with_comment, 2, 4, ["from flask import", "Flask(", "@app.route"])
    assert res is False


def test_ast_accepts_real_function_and_decorator_syntax() -> None:
    valid_flask_code = """
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/api/v1/health")
def health():
    return jsonify({"status": "ok"})
"""
    assert python_ast_has_signature(valid_flask_code, 2, 2, ["from flask import", "Flask("]) is True
    assert python_ast_has_signature(valid_flask_code, 6, 8, ["@app.route"]) is True


def test_ast_rejects_malformed_python_without_permissive_fallback() -> None:
    from app.services.verifier import _signature_found
    malformed_python = [
        "def broken_function(",
        "    # flask Flask(app)",
        "    msg = 'Flask()'",
        "    return",
    ]
    skill = {
        "id": "flask",
        "category": "Framework",
        "file_extensions": [".py"],
        "code_signatures": ["Flask("],
    }
    # When Python AST fails to parse, verifier MUST NOT fall back to permissive regex window matching
    assert _signature_found(skill, "broken.py", malformed_python, 1, 4) is False


def test_js_rejects_signature_in_comments_and_strings() -> None:
    js_comment = """
    // const app = express();
    /*
     * const router = express.Router();
     */
    const message = "Call express() to start";
    """
    assert js_has_signature(js_comment, ["express()", "express.Router()"]) is False

    js_code = """
    const express = require('express');
    const app = express();
    """
    assert js_has_signature(js_code, ["express()"]) is True


def test_assertion_detector_rejects_empty_file() -> None:
    assert check_test_has_assertions("", "test_calc.py") is False
    assert check_test_has_assertions("   \n   \t  ", "test_calc.py") is False


def test_assertion_detector_rejects_placeholder_function() -> None:
    placeholder_code = """
def test_calculation():
    pass

def test_another():
    ...
"""
    assert check_test_has_assertions(placeholder_code, "test_calc.py") is False


def test_assertion_detector_rejects_commented_out_assertion() -> None:
    commented_test = """
def test_calculation():
    # assert calc(10) == 20
    # self.assertEqual(10, 20)
    return True
"""
    assert check_test_has_assertions(commented_test, "test_calc.py") is False


def test_assertion_detector_rejects_framework_imports_without_assertions() -> None:
    import_only_test = """
import pytest
import unittest
from unittest.mock import Mock, patch
# No test functions or assertion calls
"""
    assert check_test_has_assertions(import_only_test, "test_calc.py") is False


def test_assertion_detector_rejects_js_suite_declarations_without_assertions() -> None:
    js_no_assertions = """
describe('calculation suite', () => {
    it('runs without crashing', () => {
        const x = 10;
        console.log(x);
    });
});
"""
    assert check_test_has_assertions(js_no_assertions, "test_calc.js") is False


def test_assertion_detector_accepts_js_expect() -> None:
    js_valid_test = """
describe('calculation suite', () => {
    it('calculates correctly', () => {
        expect(calc(10)).toBe(20);
    });
});
"""
    assert check_test_has_assertions(js_valid_test, "test_calc.js") is True


def test_assertion_detector_accepts_assert_statements() -> None:
    real_test = """
import pytest
from billing.engine import calculate

def test_calculate():
    assert calculate(10) == 20
"""
    assert check_test_has_assertions(real_test, "test_billing.py") is True


def test_assertion_detector_accepts_unittest_assert_helpers() -> None:
    unittest_code = """
import unittest

class TestEngine(unittest.TestCase):
    def test_run(self):
        self.assertEqual(10, 10)
        self.assertTrue(True)
"""
    assert check_test_has_assertions(unittest_code, "test_engine.py") is True


def test_assertion_detector_accepts_pytest_raises() -> None:
    pytest_raises = """
import pytest

def test_invalid():
    with pytest.raises(ValueError):
        int("invalid")
"""
    assert check_test_has_assertions(pytest_raises, "test_raises.py") is True


def test_targeting_test_rejects_empty_test_when_committed_only() -> None:
    from app.services.verifier import targeting_test
    empty_test = "import billing.engine\n# no tests yet\n"
    repo = {
        "test_files": ["tests/test_engine.py"],
        "test_contents": {"tests/test_engine.py": empty_test},
        "test_authored": {"tests/test_engine.py": True},
    }
    assert targeting_test("pkg/engine.py", repo, committed_only=True) is None
    assert targeting_test("pkg/engine.py", repo, committed_only=False) == {"path": "tests/test_engine.py", "match": "name"}


def test_targeting_test_accepts_test_with_assertions_when_committed_only() -> None:
    from app.services.verifier import targeting_test
    real_test = "import engine\n\ndef test_it():\n    assert engine.run() is True\n"
    repo = {
        "test_files": ["tests/test_engine.py"],
        "test_contents": {"tests/test_engine.py": real_test},
        "test_authored": {"tests/test_engine.py": True},
    }
    assert targeting_test("pkg/engine.py", repo, committed_only=True) == {"path": "tests/test_engine.py", "match": "name"}


def test_targeting_test_rejects_unrelated_assertions_in_matching_filename() -> None:
    from app.services.verifier import targeting_test
    # A test named test_engine.py that has assertions for an unrelated function calc()
    unrelated_test = "def test_calculation():\n    assert calc(10) == 20\n"
    repo = {
        "test_files": ["tests/test_engine.py"],
        "test_contents": {"tests/test_engine.py": unrelated_test},
        "test_authored": {"tests/test_engine.py": True},
    }
    assert targeting_test("pkg/engine.py", repo, committed_only=True) is None
