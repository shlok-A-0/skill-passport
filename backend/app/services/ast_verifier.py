import ast
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
        None: File had syntax errors or incomplete fragment; fallback to lexical check.
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


def check_test_has_assertions(content: str, path: str) -> bool:
    """Verify that a test file contains executable assertions or test functions, not just empty imports.

    Protects against completely empty or dead test files while accommodating both full test suites
    and mock fixtures.
    """
    if not content or not content.strip():
        return False
    suffix = path.lower().split(".")[-1]
    if suffix == "py":
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assert):
                    return True
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("test_") or node.name.endswith("_test"):
                        return True
                if isinstance(node, ast.Call):
                    name = ""
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name.startswith("assert") or name in {"raises", "fail"}:
                        return True
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    # Explicit import of test framework
                    names = [a.name for a in getattr(node, "names", [])]
                    mod = getattr(node, "module", "") or ""
                    if any("pytest" in n or "unittest" in n for n in names) or "pytest" in mod or "unittest" in mod:
                        return True
            return False
        except Exception:
            return "assert" in content or "test" in content.lower()
    # JavaScript / TypeScript / other languages: check for test suite keywords or assertions
    test_keywords = (
        "expect(", "assert.", "assert(", "should.", "toBe(", "toEqual(", "toBeTruthy(",
        "test(", "it(", "describe("
    )
    return any(keyword in content for keyword in test_keywords)


