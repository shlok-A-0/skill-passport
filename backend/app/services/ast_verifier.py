import ast
import posixpath
import re
from pathlib import PurePosixPath


def _signature_in_text(signature: str, text: str) -> bool:
    """Case-insensitive; a signature starting with a letter must start a word ("SELECT " is not in "deselect ")."""
    prefix = r"(?<![A-Za-z0-9_])" if signature[:1].isalnum() or signature[:1] == "_" else ""
    return re.search(prefix + re.escape(signature), text, flags=re.IGNORECASE) is not None


def _call_argument_strings(call: ast.Call):
    """String literals passed to a call, including nested calls and concatenations:
    execute(text("SELECT ...")), read_sql("SELECT " + table), connect(f"postgresql://{host}").
    A lambda's body is not passed to the call (register(lambda: "SELECT ...") is not evidence);
    calls inside it are checked on their own."""
    pending: list[ast.AST] = [*call.args, *(keyword.value for keyword in call.keywords)]
    while pending:
        child = pending.pop()
        if isinstance(child, ast.Lambda):
            continue
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child
        pending.extend(ast.iter_child_nodes(child))


def python_ast_has_signature(
    source_content: str,
    start_line: int,
    end_line: int,
    signatures: list[str],
    string_arguments: bool = False,
) -> bool | None:
    """Check if the cited line range (with +-5 margin) in a Python file contains AST-level evidence.

    With `string_arguments`, a signature also counts inside a string literal that is an argument to a
    call (SQL in text(...)/execute(...), connection URLs in connect(...)); comments, docstrings and
    other bare strings still never count.

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
        start_l = getattr(node, "lineno", None)
        if start_l is None:
            continue
        end_l = getattr(node, "end_lineno", start_l)
        if not (start_l <= window_end and end_l >= window_start):
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
                dec_start = getattr(decorator, "lineno", start_l)
                dec_end = getattr(decorator, "end_lineno", dec_start)
                if dec_start <= window_end and dec_end >= window_start:
                    dec_str = ast.unparse(decorator) if hasattr(ast, "unparse") else ""
                    for sig in signatures:
                        clean_sig = sig.lstrip("@").rstrip("(")
                        if clean_sig and clean_sig in dec_str:
                            return True

        # 3. Function / Constructor calls (e.g. Flask(), FastAPI(), APIRouter())
        if isinstance(node, ast.Call):
            if string_arguments:
                for literal in _call_argument_strings(node):
                    literal_start = getattr(literal, "lineno", start_l)
                    literal_end = getattr(literal, "end_lineno", literal_start)
                    if literal_start <= window_end and literal_end >= window_start and any(
                        _signature_in_text(sig, literal.value) for sig in signatures
                    ):
                        return True
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


def _is_trivial_assert(node: ast.Assert) -> bool:
    """Check if an assert statement is trivial/vacuous like `assert True` or `assert 1 == 1`."""
    test = node.test
    if isinstance(test, ast.Constant):
        return True
    if isinstance(test, ast.Compare):
        if isinstance(test.left, ast.Constant) and all(isinstance(c, ast.Constant) for c in test.comparators):
            return True
    return False


def _walk_test_body(node: ast.AST):
    """Walk an AST node while skipping recursive descent into nested function or class definitions."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield child
        yield from _walk_test_body(child)


def _node_has_assertion(node: ast.AST) -> bool:
    """Check if an AST node contains an executable assertion or test failure call."""
    walker = _walk_test_body(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else ast.walk(node)
    for child in walker:
        if isinstance(child, ast.Assert):
            if not _is_trivial_assert(child):
                return True
        elif isinstance(child, ast.Call):
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
        elif isinstance(child, ast.With):
            for item in child.items:
                ctx = item.context_expr
                c_name = ""
                if isinstance(ctx, ast.Call):
                    if isinstance(ctx.func, ast.Name):
                        c_name = ctx.func.id
                    elif isinstance(ctx.func, ast.Attribute):
                        c_name = ctx.func.attr
                if c_name in {"raises", "assertRaises"}:
                    return True
    return False


def _test_function_exercises_target(fn: ast.AST, target_symbols: set[str]) -> bool:
    """Check if a test function has non-trivial assertions that evaluate or exercise target symbols."""
    derived_symbols = set(target_symbols)

    def expr_references(expr: ast.AST) -> bool:
        for child in ast.walk(expr):
            if isinstance(child, ast.Name) and child.id.lower() in derived_symbols:
                return True
            if isinstance(child, ast.Attribute) and child.attr.lower() in derived_symbols:
                return True
        return False

    body = getattr(fn, "body", [fn])
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.Assign):
            if expr_references(stmt.value):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        derived_symbols.add(target.id.lower())
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        for elt in target.elts:
                            if isinstance(elt, ast.Name):
                                derived_symbols.add(elt.id.lower())
        elif isinstance(stmt, ast.AnnAssign) and stmt.value:
            if expr_references(stmt.value):
                if isinstance(stmt.target, ast.Name):
                    derived_symbols.add(stmt.target.id.lower())

    for child in _walk_test_body(fn):
        if isinstance(child, ast.Assert):
            if not _is_trivial_assert(child) and expr_references(child.test):
                return True
        elif isinstance(child, ast.Call):
            name = ""
            if isinstance(child.func, ast.Name):
                name = child.func.id
            elif isinstance(child.func, ast.Attribute):
                name = child.func.attr

            if name.startswith("assert") or name in {"assert_called", "assert_called_once", "assert_called_with"}:
                if isinstance(child.func, ast.Attribute) and expr_references(child.func.value):
                    return True
                if any(expr_references(arg) for arg in child.args):
                    return True
                if any(expr_references(kw.value) for kw in child.keywords):
                    return True

        elif isinstance(child, ast.With):
            is_raises_mgr = False
            for item in child.items:
                ctx_expr = item.context_expr
                ctx_name = ""
                if isinstance(ctx_expr, ast.Call):
                    if isinstance(ctx_expr.func, ast.Name):
                        ctx_name = ctx_expr.func.id
                    elif isinstance(ctx_expr.func, ast.Attribute):
                        ctx_name = ctx_expr.func.attr
                if ctx_name in {"raises", "assertRaises"}:
                    is_raises_mgr = True
                    break
            if is_raises_mgr:
                for stmt in child.body:
                    if expr_references(stmt):
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
    - Dummy assertions (e.g. `assert True`, `assert 1 == 1`)
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


def test_targets_implementation_with_assertions(
    content: str, path: str, module_stem: str, cited_path: str | None = None
) -> bool:
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
            # Check if any test function has an assertion that directly evaluates or exercises target
            for fn in test_functions:
                if _test_function_exercises_target(fn, target_symbols):
                    return True
            return False

        # Fallback for flat script-style tests without functions
        return _test_function_exercises_target(tree, target_symbols)

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
        # A relative import can exercise an exported symbol without mentioning its module name.
        target_symbols = {stem_lower}
        import_pattern = re.compile(
            r"(?m)^\s*import\s+([\s\S]*?)\s+from\s*(['\"])([^'\"]+)\2\s*;?"
        )
        imports = list(import_pattern.finditer(content))
        for match in imports:
            spec = match.group(3)
            if not spec.startswith("."):
                continue
            imported_path = posixpath.normpath(posixpath.join(
                str(PurePosixPath(path).parent), spec
            ))
            imported_module = PurePosixPath(imported_path).with_suffix("").as_posix()
            if cited_path is not None:
                cited_module = PurePosixPath(cited_path).with_suffix("").as_posix()
                if imported_module != cited_module:
                    continue
            elif PurePosixPath(spec).stem.lower() != stem_lower:
                continue
            clause = match.group(1).strip()
            named = re.search(r"\{([^}]*)\}", clause)
            if named:
                for item in named.group(1).split(","):
                    imported, _, local = item.strip().partition(" as ")
                    if imported:
                        target_symbols.add((local or imported).lower())
            default = re.match(r"([\w$]+)", clause)
            if default and default.group(1) != "type":
                target_symbols.add(default.group(1).lower())
            namespace = re.search(r"\*\s+as\s+([\w$]+)", clause)
            if namespace:
                target_symbols.add(namespace.group(1).lower())
        without_imports = strip_js_comments_and_strings(import_pattern.sub(" ", content))
        return any(re.search(rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])",
                             without_imports, re.IGNORECASE) for symbol in target_symbols)

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

