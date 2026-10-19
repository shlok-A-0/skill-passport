import ast
import re
from typing import Any


def python_ast_has_signature(
    source_content: str,
    start_line: int,
    end_line: int,
    signatures: list[str],
) -> bool | None:
    """Check if the cited line range (with +-5 margin) in a Python file contains AST-level evidence.

    Returns:
        True: AST found valid syntax node matching the signature.
        False: File parsed successfully, but signatures only appear in comments/strings or not at all.
        None: File had syntax errors or incomplete fragment.
    """
    try:
        tree = ast.parse(source_content)
    except (SyntaxError, ValueError):
        return None

    window_start = max(1, start_line - 5)
    window_end = end_line + 5

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None or not (window_start <= lineno <= window_end):
            continue

        # 1. Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                for sig in signatures:
                    target = sig.replace("import ", "").strip()
                    if target and (alias.name == target or alias.name.startswith(target + ".")):
                        return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for sig in signatures:
                target = sig.replace("from ", "").replace(" import", "").strip()
                if target and (mod == target or mod.startswith(target + ".")):
                    return True

        # 2. Function decorators (e.g. @app.route)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                dec_lineno = getattr(decorator, "lineno", lineno)
                if window_start <= dec_lineno <= window_end:
                    dec_str = ast.unparse(decorator) if hasattr(ast, "unparse") else ""
                    for sig in signatures:
                        clean_sig = sig.lstrip("@").rstrip("(")
                        if clean_sig and clean_sig in dec_str:
                            return True

        # 3. Function / Constructor calls (e.g. Flask(), FastAPI(), APIRouter())
        if isinstance(node, ast.Call):
            call_id = ""
            if isinstance(node.func, ast.Name):
                call_id = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_id = node.func.attr
            for sig in signatures:
                clean_sig = sig.rstrip("(")
                if clean_sig and (call_id == clean_sig or clean_sig.endswith("." + call_id)):
                    return True

        # 4. Class inheritance (e.g. models.Model, nn.Module)
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_str = ast.unparse(base) if hasattr(ast, "unparse") else ""
                for sig in signatures:
                    if sig in base_str:
                        return True

        # 5. Attribute access (e.g. pd.DataFrame, torch.tensor, tf.keras)
        if isinstance(node, ast.Attribute):
            attr_full = ast.unparse(node) if hasattr(ast, "unparse") else ""
            for sig in signatures:
                if sig.endswith(".") and attr_full.startswith(sig):
                    return True

    return False


def strip_js_comments_and_strings(code: str) -> str:
    """Strip single-line comments, block comments, and string literals from JS/TS code."""
    pattern = r'/\*[\s\S]*?\*/|//.*?$|"(?:\\.|[^\\"\n])*"|\'(?:\\.|[^\\\'\n])*\'|`(?:\\.|[^\\`])*`'
    return re.sub(pattern, " ", code, flags=re.MULTILINE)


def js_has_signature(window_text: str, signatures: list[str]) -> bool:
    """Verify that a signature in JS/TS exists in actual code, not inside comments or strings."""
    stripped = strip_js_comments_and_strings(window_text)
    for sig in signatures:
        escaped = re.escape(sig)
        if sig[:1].isalnum() or sig[:1] == "_":
            escaped = r"(?<![A-Za-z0-9_])" + escaped
        if re.search(escaped, stripped, re.IGNORECASE):
            return True
    return False


def check_test_has_assertions(content: str, path: str) -> bool:
    """Verify that a test file contains executable assertions or behavioural verification logic.

    Strictly rejects:
    - Empty or whitespace-only files
    - Placeholder test functions (e.g. `def test_foo(): pass`)
    - Commented-out assertions (e.g. `# assert x == 1`)
    - Test-framework imports without tests (e.g. `import pytest`)
    - Test suite declarations without assertions (e.g. `describe('suite', () => {})`)
    """
    if not content or not content.strip():
        return False

    suffix = path.lower().split(".")[-1]
    if suffix == "py":
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return False

        for node in ast.walk(tree):
            # 1. Direct Python assert statement
            if isinstance(node, ast.Assert):
                return True

            # 2. Function calls to assertion helpers
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                # Standard unittest, pytest, numpy, torch, mock assertion helpers
                if (
                    name.startswith("assert")
                    or name in {"raises", "fail", "assert_called", "assert_called_once", "assert_called_with"}
                ):
                    return True

        return False

    # JavaScript / TypeScript / other languages: must contain actual assertion calls
    if suffix in ("js", "jsx", "ts", "tsx", "mjs", "cjs"):
        stripped = strip_js_comments_and_strings(content)
        assertion_tokens = (
            "expect(", "assert.", "assert(", ".to.equal(", ".to.be.",
            ".to.have.", ".should.", "t.is(", "t.assert(", "t.equal(", "t.truthy("
        )
        return any(token in stripped for token in assertion_tokens)

    # General fallback for compiled languages (Go, Java, Rust): check for assertion calls
    general_assertion_tokens = (
        "assert.", "require.", "t.Error", "t.Fatal", "t.Fail",
        "assertEquals", "assertTrue", "assertFalse", "assertThat", "assert_eq!"
    )
    return any(token in content for token in general_assertion_tokens)
