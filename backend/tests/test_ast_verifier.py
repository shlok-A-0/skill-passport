import pytest
from app.services.ast_verifier import check_test_has_assertions, python_ast_has_signature


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
    # Should find real import at line 2
    assert python_ast_has_signature(valid_flask_code, 2, 2, ["from flask import", "Flask("]) is True
    # Should find real decorator at lines 6-8
    assert python_ast_has_signature(valid_flask_code, 6, 8, ["@app.route"]) is True


def test_assertion_detector_rejects_empty_test_file() -> None:
    empty_test = """
import billing.engine
# todo: add test cases
"""
    assert check_test_has_assertions(empty_test, "test_billing.py") is False


def test_assertion_detector_accepts_assert_statements() -> None:
    real_test = """
import pytest
from billing.engine import calculate

def test_calculate():
    assert calculate(10) == 20
"""
    assert check_test_has_assertions(real_test, "test_billing.py") is True


def test_targeting_test_rejects_empty_test_when_committed_only() -> None:
    from app.services.verifier import targeting_test
    empty_test = "import billing.engine\n# no tests yet\n"
    repo = {
        "test_files": ["tests/test_engine.py"],
        "test_contents": {"tests/test_engine.py": empty_test},
        "test_authored": {"tests/test_engine.py": True},
    }
    # When committed_only=True, an empty test without assertions is rejected
    assert targeting_test("pkg/engine.py", repo, committed_only=True) is None
    # When committed_only=False, name matching still works
    assert targeting_test("pkg/engine.py", repo, committed_only=False) == {"path": "tests/test_engine.py", "match": "name"}


def test_targeting_test_accepts_test_with_assertions_when_committed_only() -> None:
    from app.services.verifier import targeting_test
    real_test = "def test_it():\n    assert 1 == 1\n"
    repo = {
        "test_files": ["tests/test_engine.py"],
        "test_contents": {"tests/test_engine.py": real_test},
        "test_authored": {"tests/test_engine.py": True},
    }
    assert targeting_test("pkg/engine.py", repo, committed_only=True) == {"path": "tests/test_engine.py", "match": "name"}


