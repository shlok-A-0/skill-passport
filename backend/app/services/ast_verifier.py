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


def strip_c_style_comments_and_strings(code: str) -> str:
    """Strip C-style comments (// and /* */) and strings for JS, TS, Java, Go, Rust, C/C++."""
    pattern = r'/\*[\s\S]*?\*/|//.*?$|"(?:\\.|[^\\"\n])*"|\'(?:\\.|[^\\\'\n])*\''
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


def _node_has_assertion(node: ast.AST) -> bool:
    """Check if an AST node contains an assertion or test failure call."""
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.Call):
            name = ""
            if isinstance(child.func, ast.Name):
                name = child.func.id
            elif isinstance(child.func, ast.Attribute):
                name = child.func.attr
            if (
                name.startswith("assert")
                or name in {"raises", "fail", "assert_called", "assert_called_once", "assert_called_with"}
            ):
                return True
    return False


def _node_references_symbols(node: ast.AST, symbols: set[str]) -> bool:
    """Check if an AST node references any of the target symbol names."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id.lower() in symbols:
            return True
        if isinstance(child, ast.Attribute) and child.attr.lower() in symbols:
            return True
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            val_lower = child.value.lower()
            if any(sym in val_lower for sym in symbols):
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
        return _node_has_assertion(tree)

    # JavaScript / TypeScript
    if suffix in ("js", "jsx", "ts", "tsx", "mjs", "cjs"):
        stripped = strip_js_comments_and_strings(content)
        assertion_tokens = (
            "expect(", "assert.", "assert(", ".to.equal(", ".to.be.",
            ".to.have.", ".should.", "t.is(", "t.assert(", "t.equal(", "t.truthy("
        )
        return any(token in stripped for token in assertion_tokens)

    # Compiled languages (Go, Java, Rust, C++): strip comments before checking assertion tokens
    stripped = strip_c_style_comments_and_strings(content)
    general_assertion_tokens = (
        "assert.", "require.", "t.Error", "t.Fatal", "t.Fail",
        "assertEquals", "assertTrue", "assertFalse", "assertThat", "assert_eq!"
    )
    return any(token in stripped for token in general_assertion_tokens)


def test_targets_implementation_with_assertions(content: str, path: str, module_stem: str) -> bool:
    """Establish that the test file has a test function containing assertions that actually
    exercises or references the cited module/symbol, not merely an unrelated test in a file
    with a matching name.
    """
    if not content or not content.strip() or not module_stem:
        return False

    suffix = path.lower().split(".")[-1]
    stem_lower = module_stem.lower()

    if suffix == "py":
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return False

        # 1. Resolve target symbols (stem itself + any imported aliases originating from stem)
        target_symbols = {stem_lower}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if stem_lower in [p.lower() for p in parts]:
                        target_symbols.add((alias.asname or alias.name).split(".")[-1].lower())
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").lower().split(".")
                if stem_lower in mod:
                    for alias in node.names:
                        target_symbols.add((alias.asname or alias.name).lower())
                else:
                    for alias in node.names:
                        if alias.name.lower() == stem_lower:
                            target_symbols.add((alias.asname or alias.name).lower())

        # 2. Inspect candidate test functions
        test_functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.lower().startswith("test") or "test" in node.name.lower():
                    test_functions.append(node)

        if test_functions:
            # Check if any test function has BOTH an assertion and references the target implementation
            for fn in test_functions:
                if _node_has_assertion(fn) and _node_references_symbols(fn, target_symbols):
                    return True
            # If test functions exist but none exercise the cited implementation, reject
            return False

        # Fallback for flat script-style tests without functions
        return _node_has_assertion(tree) and _node_references_symbols(tree, target_symbols)

    # JavaScript / TypeScript
    if suffix in ("js", "jsx", "ts", "tsx", "mjs", "cjs"):
        stripped = strip_js_comments_and_strings(content)
        assertion_tokens = (
            "expect(", "assert.", "assert(", ".to.equal(", ".to.be.",
            ".to.have.", ".should.", "t.is(", "t.assert(", "t.equal(", "t.truthy("
        )
        has_assertion = any(token in stripped for token in assertion_tokens)
        if not has_assertion:
            return False
        # Must reference the module stem in the code
        stem_pattern = rf"(?<![A-Za-z0-9_]){re.escape(stem_lower)}(?![A-Za-z0-9_])"
        return bool(re.search(stem_pattern, stripped, re.IGNORECASE))

    # Compiled languages
    stripped = strip_c_style_comments_and_strings(content)
    general_assertion_tokens = (
        "assert.", "require.", "t.Error", "t.Fatal", "t.Fail",
        "assertEquals", "assertTrue", "assertFalse", "assertThat", "assert_eq!"
    )
    has_assertion = any(token in stripped for token in general_assertion_tokens)
    if not has_assertion:
        return False
    stem_pattern = rf"(?<![A-Za-z0-9_]){re.escape(stem_lower)}(?![A-Za-z0-9_])"
    return bool(re.search(stem_pattern, stripped, re.IGNORECASE))
