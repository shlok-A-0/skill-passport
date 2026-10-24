"""SQL evidence depth: a point-by-point score of the SQL found in the analysed files.

Read-only: it never changes skill levels, role matching, ranking or verification. Each part of the
score lists the queries that earned it. Only lines the existing checks count as the candidate's
are scored: files they committed to, not excluded as an earlier public source, with at least one
cited line last changed in their commits. The weights and caps are provisional, not a validated
proficiency measure, and repository analysis never proves proficiency.
"""
import ast
import logging
import re
from itertools import combinations
from pathlib import PurePosixPath
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Tokenizer, TokenType

from app.services.github_ingest import is_test_path
from app.services.verifier import (
    EARLIER_SOURCE_KINDS,
    _ai_leftover_signal,
    _bulk_upload_signal,
    _line_share_signal,
    _make_url,
    targeting_test,
)

logger = logging.getLogger(__name__)

NOTE = "Depth of SQL evidence in the analysed repositories, not a full measure of skill."
WEIGHTS_NOTE = "Weights and caps are provisional, not a validated proficiency measure."
# Calls whose string argument is SQL: cursor.execute(...), text(...), pandas.read_sql(...).
SQL_CALLS = {"execute", "executemany", "executescript", "text", "read_sql", "read_sql_query"}
SQL_ARGUMENT_NAMES = {"sql", "query", "statement", "operation", "sql_script"}
# A Python string is only treated as SQL when it starts like a statement; text("Hello") is not SQL.
SQL_START = re.compile(
    r"^\s*(?:(?:--[^\n]*(?:\n|$)|/\*.*?\*/)\s*)*"
    r"(?:select|with|insert|update|delete|create|alter|drop|begin|commit|rollback|merge|replace"
    r"|savepoint|release|start\s+transaction|truncate)\b",
    re.I | re.S,
)
# DB-API placeholders sqlglot can't read; replaced with "?" padded to the same length, so
# character offsets and line numbers stay exact.
PYFORMAT_PLACEHOLDER = re.compile(r"%\(\w+\)s|%s")
DIALECTS = (None, "postgres", "mysql", "sqlite")
QUERY_TYPES = (exp.Select, exp.SetOperation, exp.Insert, exp.Update, exp.Delete, exp.Merge)
SCHEMA_TYPES = (exp.Create, exp.Alter, exp.Drop)
TRANSACTION_TYPES = (exp.Transaction, exp.Commit, exp.Rollback)
ROUTE_DECORATOR = re.compile(
    r"(?:^|\.)(?:route|get|post|put|patch|delete|api_route|websocket|command)\s*\(", re.I
)

WEIGHTS = {
    "complexity": 0.20,
    "breadth": 0.20,
    "repetition": 0.20,
    "integration": 0.15,
    "authorship": 0.15,
    "testing": 0.10,
}
PART_LABELS = {
    "complexity": "Complexity",
    "breadth": "Breadth",
    "repetition": "Repetition",
    "integration": "Integration",
    "authorship": "Authorship",
    "testing": "Testing",
}
CONCEPTS = {
    "filter_sort": "Filtering and sorting",
    "data_change": "Data changes (INSERT, UPDATE, DELETE)",
    "group_aggregate": "Grouping and aggregation",
    "join": "Joins",
    "subquery_cte": "Subqueries and CTEs",
    "window": "Window functions",
    "schema_design": "Schema design",
    "transaction": "Transactions",
}
LEVEL_LABELS = {
    25: "Simple",
    50: "Grouping or aggregation",
    75: "Joins or subqueries",
    100: "CTEs, window functions or set operations",
}
ONE_QUERY_CAP = 35
SINGLE_REPO_REPETITION_CAP = 75
MIN_DISTINCT_QUERIES = 3
MAX_EVIDENCE = 3
MAX_SKIPPED = 25
SNIPPET_LINES = 12
SHAPE_CHARS = 300


# --- Extraction -------------------------------------------------------------------------------


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _constant_string(node: ast.AST) -> str | None:
    """A string literal, or literals joined with "+"; None for anything built at runtime."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _constant_string(node.left), _constant_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _static_text(node: ast.AST) -> str:
    """The literal pieces of a string built at runtime (an f-string or a "+" with variables)."""
    return "".join(
        child.value for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    )


def _sqlalchemy_text_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to SQLAlchemy's text() and to the sqlalchemy module itself."""
    functions, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "sqlalchemy":
            functions.update(alias.asname or alias.name for alias in node.names if alias.name == "text")
        elif isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name for alias in node.names if alias.name.split(".")[0] == "sqlalchemy"
            )
    return functions, modules


def _is_sql_call(node: ast.Call, text_functions: set[str], sqlalchemy_modules: set[str]) -> bool:
    name = _call_name(node)
    if name != "text":
        return name in SQL_CALLS
    # text() is a common name (UI widgets, templating); only SQLAlchemy's counts.
    if isinstance(node.func, ast.Name):
        return name in text_functions
    root = node.func.value
    while isinstance(root, ast.Attribute):
        root = root.value
    return isinstance(root, ast.Name) and root.id in sqlalchemy_modules


def _python_sql_strings(content: str) -> dict[str, Any]:
    """SQL strings passed to supported calls, with the file lines of each string literal.

    A variable is followed only when it is assigned one string literal in the file
    (`QUERY = "SELECT ..."; cursor.execute(QUERY)`). `execute(text("..."))` is read once, from
    the inner call.
    """
    tree = ast.parse(content)
    text_functions, sqlalchemy_modules = _sqlalchemy_text_names(tree)
    assigned: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
            assigned.setdefault(node.target.id, []).append(node.value)

    # The final flag records whether decoded newlines map one-to-one to source lines.
    # Python escape sequences and line continuations can make that mapping ambiguous.
    strings: dict[tuple[int, int], tuple[str, int, int, list[int], bool]] = {}
    dynamic: list[tuple[int, int]] = []
    unresolved = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_sql_call(node, text_functions, sqlalchemy_modules):
            continue
        argument = node.args[0] if node.args else next(
            (keyword.value for keyword in node.keywords if keyword.arg in SQL_ARGUMENT_NAMES), None
        )
        if argument is None or isinstance(argument, ast.Call):
            continue
        if isinstance(argument, ast.Name):
            values = assigned.get(argument.id, [])
            if len(values) != 1 or _constant_string(values[0]) is None:
                unresolved += 1
                continue
            argument = values[0]
        value = _constant_string(argument)
        start, end = argument.lineno, getattr(argument, "end_lineno", argument.lineno) or argument.lineno
        if value is None:
            if SQL_START.match(_static_text(argument)):
                dynamic.append((start, end))
            continue
        if SQL_START.match(value):
            source_literal = ast.get_source_segment(content, argument) or ""
            line_mapping_exact = (end - start) == value.count("\n")
            if "\\\n" in source_literal or "\\\r\n" in source_literal:
                line_mapping_exact = False
            strings.setdefault((argument.lineno, argument.col_offset),
                               (value, start, end, [], line_mapping_exact))[3].append(node.lineno)
    return {"strings": list(strings.values()), "dynamic": dynamic, "unresolved": unresolved}


def _statements(sql: str) -> list[tuple[str, int, int]]:
    """Each statement's text and its first/last line within `sql` (1-based). Raises on bad tokens."""
    padded = PYFORMAT_PLACEHOLDER.sub(lambda match: "?" + " " * (len(match.group()) - 1), sql)
    tokens = Tokenizer().tokenize(padded)
    groups: list[list[Any]] = [[]]
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            groups.append([])
        else:
            groups[-1].append(token)
    return [
        (padded[group[0].start:group[-1].end + 1], group[0].line, group[-1].line)
        for group in groups if group
    ]


def _parse(text: str) -> exp.Expression | None:
    """The statement's syntax tree, or None when no dialect reads it as a recognised statement."""
    for dialect in DIALECTS:
        try:
            parsed = sqlglot.parse_one(text, read=dialect)
        except SqlglotError:
            continue
        if isinstance(parsed, QUERY_TYPES + SCHEMA_TYPES + TRANSACTION_TYPES):
            return parsed
    return None


def _extract(path: str, source: dict[str, Any]) -> dict[str, Any]:
    """Raw SQL statements in one file: (text, first file line, last file line, lines of the calls running it)."""
    content = source.get("content") or "\n".join(source.get("lines", []))
    found: list[tuple[str, int, int, list[int]]] = []
    skipped: list[tuple[int, int, str, str]] = []
    unresolved = 0
    if path.lower().endswith(".sql"):
        pieces = [(content, 1, max(1, len(source.get("lines", []))), True, [], True)]
    else:
        try:
            result = _python_sql_strings(content)
        except (SyntaxError, ValueError):
            return {"statements": [], "skipped": [], "unresolved": 0}
        pieces = [(value, start, end, "\n" in value, calls, mapping_exact)
                  for value, start, end, calls, mapping_exact in result["strings"]]
        skipped.extend(
            (start, end, "dynamic", "SQL built at runtime (f-string or concatenation with variables); not analysed.")
            for start, end in result["dynamic"]
        )
        unresolved = result["unresolved"]
    for value, start, end, multiline, calls, mapping_exact in pieces:
        try:
            statements = _statements(value)
        except SqlglotError:
            skipped.append((start, end, "unparseable", "SQL could not be read (unbalanced quotes or comments)."))
            continue
        for text, first, last in statements:
            if multiline and mapping_exact:
                # Value line k is file line start + k - 1 (triple-quoted strings and .sql files).
                lines = max(start, min(end, start + first - 1)), max(start, min(end, start + last - 1))
            else:
                # Escapes can suppress or introduce decoded newlines. Use the entire source
                # literal as a safe citation and blame range when its exact line map is unknown.
                lines = start, end
            found.append((text, *lines, calls))
    return {"statements": found, "skipped": skipped, "unresolved": unresolved}


# --- Classification ---------------------------------------------------------------------------


def _cte_names(expression: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE) if cte.alias_or_name}


def _tables(expression: exp.Expression) -> set[str]:
    ctes = _cte_names(expression)
    return {
        table.name.lower() for table in expression.find_all(exp.Table)
        if table.name and table.name.lower() not in ctes
    }


def _concepts(expression: exp.Expression) -> set[str]:
    found = set()
    if expression.find(exp.Where) or expression.find(exp.Order):
        found.add("filter_sort")
    if isinstance(expression, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        found.add("data_change")
    if expression.find(exp.Group) or expression.find(exp.AggFunc):
        found.add("group_aggregate")
    if expression.find(exp.Join):
        found.add("join")
    if expression.find(exp.CTE) or expression.find(exp.Subquery):
        found.add("subquery_cte")
    if expression.find(exp.Window):
        found.add("window")
    if isinstance(expression, (exp.Create, exp.Alter)):
        found.add("schema_design")
    if isinstance(expression, TRANSACTION_TYPES):
        found.add("transaction")
    return found


def _level(expression: exp.Expression) -> int:
    if expression.find(exp.CTE) or expression.find(exp.Window) or expression.find(exp.SetOperation):
        return 100
    if expression.find(exp.Join) or expression.find(exp.Subquery):
        return 75
    if expression.find(exp.Group) or expression.find(exp.AggFunc):
        return 50
    return 25


def _operation(expression: exp.Expression) -> str:
    if isinstance(expression, exp.Insert):
        return "insert"
    if isinstance(expression, exp.Update):
        return "update"
    if isinstance(expression, exp.Delete):
        return "delete"
    if isinstance(expression, exp.Merge):
        return "merge"
    return "select"


def _target_table(expression: exp.Expression) -> str | None:
    table = expression.find(exp.Table)
    return table.name.lower() if table is not None and table.name else None


def _structure_key(expression: exp.Expression) -> str:
    """Literals, placeholders, column names and aliases masked; identifiers lower-cased.

    Formatting, literal values, IN-list lengths, multi-row VALUES and which columns are
    selected or filtered don't make a query distinct; its tables and structure do.
    """
    def mask(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.Literal, exp.Placeholder, exp.Parameter)):
            return exp.Placeholder()
        if isinstance(node, exp.Column):
            return exp.column("c")
        if isinstance(node, exp.Alias):
            return exp.alias_(node.this, "a")
        if isinstance(node, exp.TableAlias):
            return exp.TableAlias(this=exp.to_identifier("a"))
        if isinstance(node, exp.Identifier):
            return exp.to_identifier(node.name.lower())
        return node

    masked = expression.copy().transform(mask)
    for node in list(masked.find_all(exp.In)):
        if node.expressions:
            node.set("expressions", [exp.Placeholder()])
    for node in list(masked.find_all(exp.Values)):
        if len(node.expressions) > 1:
            node.set("expressions", node.expressions[:1])
    return masked.sql()


def _shape(expression: exp.Expression) -> str:
    """Readable form: literal values replaced with ?, keywords and spacing normalised."""
    def mask(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.Literal, exp.Placeholder, exp.Parameter)):
            return exp.Placeholder()
        return node

    text = expression.copy().transform(mask).sql()
    return text if len(text) <= SHAPE_CHARS else text[:SHAPE_CHARS - 1] + "…"


def _schema_pairs(expression: exp.Expression) -> set[tuple[str, str]]:
    """(table, referenced table) for each REFERENCES in a CREATE/ALTER TABLE."""
    if not isinstance(expression, (exp.Create, exp.Alter)):
        return set()
    table = _target_table(expression)
    pairs = set()
    for reference in expression.find_all(exp.Reference):
        target = reference.find(exp.Table)
        if table and target is not None and target.name and target.name.lower() != table:
            pairs.add(tuple(sorted((table, target.name.lower()))))
    return pairs


def _join_pairs(expression: exp.Expression) -> set[tuple[str, str]]:
    if not expression.find(exp.Join):
        return set()
    return {tuple(sorted(pair)) for pair in combinations(sorted(_tables(expression)), 2)}


# --- Application context (static only) --------------------------------------------------------


def _python_index(repo: dict[str, Any], excluded_paths: set[str] | None = None) -> dict[str, Any]:
    """Functions, call sites and string constants in the repo's analysed, non-test Python files."""
    excluded_paths = excluded_paths or set()
    functions: dict[str, list[dict[str, Any]]] = {}
    calls: list[dict[str, Any]] = []
    strings: list[dict[str, Any]] = []
    for path, source in (repo.get("source_files") or {}).items():
        if (not path.endswith(".py") or is_test_path(path) or path in excluded_paths
                or not source.get("authored_shas")):
            continue
        try:
            tree = ast.parse(source.get("content") or "\n".join(source.get("lines", [])))
        except (SyntaxError, ValueError):
            continue
        defs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                route = next(
                    (decorator for decorator in node.decorator_list
                     if ROUTE_DECORATOR.search(ast.unparse(decorator))),
                    None,
                )
                defs.append({
                    "name": node.name,
                    "start": min([node.lineno, *(d.lineno for d in node.decorator_list)]),
                    "end": getattr(node, "end_lineno", node.lineno) or node.lineno,
                    "route_line": route.lineno if route is not None else None,
                })
        functions[path] = defs
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name and name not in SQL_CALLS:
                    calls.append({"name": name, "path": path, "line": node.lineno})
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append({"value": node.value.lower(), "path": path, "line": node.lineno})
    return {"functions": functions, "calls": calls, "strings": strings}


def _enclosing(functions: list[dict[str, Any]], line: int) -> dict[str, Any] | None:
    containing = [item for item in functions if item["start"] <= line <= item["end"]]
    return max(containing, key=lambda item: item["start"]) if containing else None


def _wiring(
    occurrence: dict[str, Any], call_lines: list[int], repo: dict[str, Any], index: dict[str, Any]
) -> dict[str, Any]:
    """How the query is reached from application code, from static references in the analysed files.

    A Python query is judged where it runs (the execute/read_sql call), not where its string is
    assigned; with several calls, the best-connected one is used.
    """
    path = occurrence["file_path"]
    if path.lower().endswith(".sql"):
        name = PurePosixPath(path).name.lower()
        reference = next((item for item in index["strings"] if name in item["value"]), None)
        if reference is None:
            return {"status": "none", "detail": f"No analysed Python file refers to {name}.", "evidence": None}
        return {
            "status": "loaded",
            "detail": f"{PurePosixPath(path).name} is referred to in {reference['path']} line {reference['line']}.",
            "evidence": _ref(repo, reference["path"], reference["line"], reference["line"]),
        }
    results = [_call_wiring(path, line, repo, index) for line in call_lines or [occurrence["start_line"]]]
    rank = {"route": 0, "caller": 1, "none": 2}
    return min(results, key=lambda item: rank[item["status"]])


def _call_wiring(path: str, line: int, repo: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    functions = index["functions"].get(path, [])
    function = _enclosing(functions, line)
    if function is None:
        return {"status": "none", "detail": "Runs at module level, not inside an application function.", "evidence": None}
    if function["route_line"] is not None:
        return {
            "status": "route",
            "detail": f"Inside {function['name']}(), a route or command handler.",
            "evidence": _ref(repo, path, function["route_line"], function["route_line"]),
            "function": function["name"],
        }
    callers = [
        call for call in index["calls"]
        if call["name"] == function["name"]
        and not (call["path"] == path and function["start"] <= call["line"] <= function["end"])
    ]

    def from_route(call: dict[str, Any]) -> bool:
        caller = _enclosing(index["functions"].get(call["path"], []), call["line"])
        return bool(caller and caller["route_line"] is not None)

    if not callers:
        return {
            "status": "none",
            "detail": f"Inside {function['name']}(), but no call to it was found in the analysed files.",
            "evidence": None,
            "function": function["name"],
        }
    caller = next((call for call in callers if from_route(call)), callers[0])
    via = " from a route or command handler" if from_route(caller) else ""
    return {
        "status": "caller",
        "detail": f"{function['name']}() is called{via} in {caller['path']} line {caller['line']}.",
        "evidence": _ref(repo, caller["path"], caller["line"], caller["line"]),
        "function": function["name"],
    }


# --- Evidence ---------------------------------------------------------------------------------


def _ref(repo: dict[str, Any], path: str, start: int, end: int) -> dict[str, Any]:
    source = (repo.get("source_files") or {}).get(path) or {}
    lines = source.get("lines", [])
    shown = lines[start - 1:min(end, start - 1 + SNIPPET_LINES)]
    snippet = "\n".join(shown) + ("\n…" if end - start + 1 > SNIPPET_LINES else "")
    sha = repo.get("head_sha") or "HEAD"
    return {
        "repo": repo["name"],
        "file_path": path,
        "start_line": start,
        "end_line": end,
        "url": _make_url(repo, sha, path, start, end),
        "snippet": snippet,
    }


def _cited_code(skills: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    cited: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for skill in skills:
        for item in skill.get("evidence", []):
            if item.get("type") == "code":
                cited.setdefault((item["repo"], item["file_path"]), []).append(item)
    return cited


def _copy_check(occurrence: dict[str, Any], cited: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    """The existing earlier-source check on overlapping cited lines (it only runs on cited lines)."""
    status, advisory = "not_checked", []
    for item in cited:
        if item["end_line"] < occurrence["start_line"] or item["start_line"] > occurrence["end_line"]:
            continue
        signal = next((s for s in item.get("integrity", []) if s.get("id") == "copy_match"), None)
        if signal is None:
            continue
        if signal.get("status") == "warn":
            status = "advisory_match"
            advisory.append({"id": "copy_match", "label": signal.get("label", ""), "detail": signal.get("detail", "")})
        elif signal.get("status") == "ok" and status == "not_checked":
            status = "no_match"
    return status, advisory


def _skip(repo: dict[str, Any], path: str, start: int, end: int, kind: str, reason: str) -> dict[str, Any]:
    return {**_ref(repo, path, start, end), "kind": kind, "reason": reason}


def _part(key: str, value: float, reason: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    value = max(0.0, min(100.0, value))
    weight = WEIGHTS[key]
    return {
        "label": PART_LABELS[key],
        "value": round(value, 1),
        "weight": weight,
        "points": round(value * weight, 1),
        "max_points": round(100 * weight),
        "reason": reason,
        "evidence": evidence[:MAX_EVIDENCE],
    }


def _repetition_value(count: int) -> int:
    if count <= 0:
        return 0
    if count == 1:
        return 20
    if count <= 3:
        return 50
    if count <= 6:
        return 75
    return 100


def _label(score: int, distinct: int) -> str:
    if distinct < MIN_DISTINCT_QUERIES:
        return "Limited evidence"
    if score >= 80:
        return "Strong"
    if score >= 60:
        return "Solid"
    if score >= 35:
        return "Developing"
    return "Basic"


def _percent(numerator: int, denominator: int) -> str:
    return f"{round(100 * numerator / denominator)}%" if denominator else "0%"


# --- Scoring ----------------------------------------------------------------------------------


def compute_sql_depth(ingestion: dict[str, Any], verified: dict[str, Any]) -> dict[str, Any]:
    """Score the candidate's SQL evidence after verification and exclusion (levels are final)."""
    username = ingestion.get("username", "")
    repos = [repo for repo in ingestion.get("repos", []) if not repo.get("is_fork")]
    skills = verified.get("skills", [])
    earlier_source_files = {
        ((item.get("claim") or {}).get("repo"), (item.get("claim") or {}).get("file_path"))
        for item in verified.get("rejected_claims", [])
        if item.get("kind") in EARLIER_SOURCE_KINDS
    }
    cited = _cited_code(skills)

    groups: dict[str, dict[str, Any]] = {}
    other_statements: list[dict[str, Any]] = []  # schema and transaction statements
    skipped: list[dict[str, Any]] = []
    unresolved = 0
    for repo in repos:
        excluded_paths = {path for repo_name, path in earlier_source_files if repo_name == repo["name"]}
        index = _python_index(repo, excluded_paths)
        for path, source in (repo.get("source_files") or {}).items():
            if is_test_path(path) or not (path.endswith(".py") or path.lower().endswith(".sql")):
                continue
            extracted = _extract(path, source)
            unresolved += extracted["unresolved"]
            skipped.extend(_skip(repo, path, *item) for item in extracted["skipped"])
            if not extracted["statements"]:
                continue
            excluded = None
            if (repo["name"], path) in earlier_source_files:
                excluded = ("earlier_source", "This file matches code published earlier elsewhere, so it isn't counted.")
            elif not source.get("authored_shas"):
                excluded = ("not_committed", "No commit by the candidate touches this file.")
            for text, start, end, call_lines in extracted["statements"]:
                if excluded:
                    skipped.append(_skip(repo, path, start, end, *excluded))
                    continue
                expression = _parse(text)
                if expression is None:
                    skipped.append(_skip(repo, path, start, end, "unparseable", "Not a recognised SQL statement."))
                    continue
                if isinstance(expression, QUERY_TYPES) and not _tables(expression):
                    skipped.append(_skip(
                        repo, path, start, end, "no_table", "Reads no table (for example SELECT 1); not counted as a query.",
                    ))
                    continue
                share = _line_share_signal(source, start, end, username)
                if share.get("written") == 0:
                    skipped.append(_skip(
                        repo, path, start, end, "not_written",
                        "None of these lines were last changed in the candidate's commits.",
                    ))
                    continue
                copy_status, advisory = _copy_check({"start_line": start, "end_line": end}, cited.get((repo["name"], path), []))
                advisory += [
                    {"id": signal["id"], "label": signal["label"], "detail": signal["detail"]}
                    for signal in (_bulk_upload_signal(source), _ai_leftover_signal(source))
                    if signal["status"] == "warn"
                ]
                occurrence = {
                    **_ref(repo, path, start, end),
                    "lines_written": (
                        {"written": share["written"], "cited": share["cited"]} if "written" in share else None
                    ),
                    "copy_check": copy_status,
                    "advisory": advisory,
                }
                if not isinstance(expression, QUERY_TYPES):
                    other_statements.append({
                        "occurrence": occurrence,
                        "concepts": _concepts(expression),
                        "pairs": _schema_pairs(expression),
                        "table": _target_table(expression),
                    })
                    continue
                key = _structure_key(expression)
                group = groups.get(key)
                if group is None:
                    group = groups[key] = {
                        "shape": _shape(expression),
                        "statement": _operation(expression),
                        "level": _level(expression),
                        "concepts": _concepts(expression),
                        "tables": _tables(expression),
                        "target": _target_table(expression),
                        "join_pairs": _join_pairs(expression),
                        "occurrences": [],
                    }
                occurrence["wired"] = _wiring(occurrence, call_lines, repo, index)
                occurrence["test"] = targeting_test(path, repo, committed_only=True)
                if occurrence["test"]:
                    occurrence["test"] = {
                        **occurrence["test"],
                        "ci": "passed" if (repo.get("ci_status") or {}).get("has_successful_run") else "not_recorded",
                    }
                group["occurrences"].append(occurrence)

    distinct = list(groups.values())
    count = len(distinct)
    first = [group["occurrences"][0] for group in distinct]

    # C: mean of the top three distinct query levels.
    ranked = sorted(range(count), key=lambda i: -distinct[i]["level"])[:3]
    complexity = sum(distinct[i]["level"] for i in ranked) / len(ranked) if ranked else 0
    complexity_reason = (
        "Mean of the top {} distinct query level(s): {}.".format(
            len(ranked), ", ".join(f"{LEVEL_LABELS[distinct[i]['level']]} = {distinct[i]['level']}" for i in ranked)
        ) if ranked else "No countable queries were found."
    )

    # B: distinct concepts across counted queries, schema and transaction statements.
    concept_evidence: dict[str, list[dict[str, Any]]] = {}
    for group in distinct:
        for concept in group["concepts"]:
            concept_evidence.setdefault(concept, []).append(_public_ref(group["occurrences"][0]))
    for statement in other_statements:
        for concept in statement["concepts"]:
            concept_evidence.setdefault(concept, []).append(_public_ref(statement["occurrence"]))
    concepts_found = [
        {"id": concept, "label": label, "evidence": concept_evidence[concept][:MAX_EVIDENCE]}
        for concept, label in CONCEPTS.items() if concept in concept_evidence
    ]
    concepts_missing = [
        {"id": concept, "label": label} for concept, label in CONCEPTS.items() if concept not in concept_evidence
    ]
    breadth = len(concepts_found) / len(CONCEPTS) * 100
    breadth_reason = f"{len(concepts_found)} of {len(CONCEPTS)} SQL concepts found" + (
        ": " + ", ".join(item["label"] for item in concepts_found) + "." if concepts_found else "."
    )

    # R: how many distinct queries, capped when they all sit in one repository.
    repetition = _repetition_value(count)
    query_repos = {occurrence["repo"] for group in distinct for occurrence in group["occurrences"]}
    merged = sum(len(group["occurrences"]) - 1 for group in distinct)
    repetition_reason = f"{count} distinct quer{'y' if count == 1 else 'ies'} in {len(query_repos)} repositor{'y' if len(query_repos) == 1 else 'ies'}."
    if merged:
        repetition_reason += f" {merged} repeated or trivially different use(s) were merged and not counted again."
    if len(query_repos) == 1 and repetition > SINGLE_REPO_REPETITION_CAP:
        repetition = SINGLE_REPO_REPETITION_CAP
        repetition_reason += f" Capped at {SINGLE_REPO_REPETITION_CAP}: all queries are in one repository."

    integration, integration_reason, integration_evidence = _integration(distinct, other_statements)

    # A: mean candidate-written line share per distinct query (its best occurrence).
    shares, unknown = [], 0
    for group in distinct:
        known = [o["lines_written"] for o in group["occurrences"] if o["lines_written"]]
        if known:
            shares.append(max(item["written"] / item["cited"] for item in known))
        else:
            shares.append(0.0)
            unknown += 1
    authorship = sum(shares) / len(shares) * 100 if shares else 0
    excluded_count = sum(1 for item in skipped if item["kind"] in {"earlier_source", "not_committed", "not_written"})
    authorship_reason = (
        f"Mean share of each distinct query's lines last changed in the candidate's commits: {round(authorship)}%."
        if shares else "No countable queries were found."
    )
    if unknown:
        authorship_reason += f" {unknown} quer{'y has' if unknown == 1 else 'ies have'} unknown line authorship (no line history), so no authorship points for {'it' if unknown == 1 else 'them'}."
    if excluded_count:
        authorship_reason += f" {excluded_count} statement(s) were not counted (see Skipped)."

    # V: distinct queries in a file that a test the candidate committed to targets.
    linked = [group for group in distinct if any(o["test"] for o in group["occurrences"])]
    testing = len(linked) / count * 100 if count else 0
    ci_passed = sum(
        1 for group in linked
        if any(o["test"] and o["test"]["ci"] == "passed" for o in group["occurrences"])
    )
    testing_reason = (
        f"{len(linked)} of {count} distinct queries are in files targeted by a test the candidate committed to"
        f" ({_percent(len(linked), count)}). This shows tests exist, not that they pass"
        + (f"; CI passed on the latest commit for {ci_passed} of them (which tests ran isn't known)" if ci_passed else "")
        + ". Query results were not checked against test data, so correctness is unverified."
        if count else "No countable queries were found."
    )
    testing_evidence = []
    for group in linked:
        occurrence = next(o for o in group["occurrences"] if o["test"])
        test = occurrence["test"]
        testing_evidence.append({
            **_public_ref(occurrence),
            "test": test,
            "detail": f"Targeted by {test['path']} (by {test['match']}); "
            + ("CI passed on the latest commit" if test["ci"] == "passed" else "no passing run recorded")
            + "; correctness unverified",
        })

    def written(occurrence: dict[str, Any]) -> str:
        share = occurrence["lines_written"]
        return f"{share['written']} of {share['cited']} lines written by the candidate" if share else "Line authorship unknown"

    parts = {
        "complexity": _part("complexity", complexity, complexity_reason, [
            {**_public_ref(first[i]), "detail": f"{LEVEL_LABELS[distinct[i]['level']]} ({distinct[i]['level']})"}
            for i in ranked
        ]),
        "breadth": _part("breadth", breadth, breadth_reason, [
            {**item["evidence"][0], "detail": item["label"]} for item in concepts_found
        ]),
        "repetition": _part("repetition", repetition, repetition_reason, [
            {**_public_ref(o), "detail": group["shape"]} for o, group in zip(first, distinct)
        ]),
        "integration": _part("integration", integration, integration_reason, integration_evidence),
        "authorship": _part("authorship", authorship, authorship_reason, [
            {**_public_ref(o), "lines_written": o["lines_written"], "detail": written(o)} for o in first
        ]),
        "testing": _part("testing", testing, testing_reason, testing_evidence),
    }
    raw = sum(parts[key]["value"] * WEIGHTS[key] for key in WEIGHTS)
    score = int(raw + 0.5)
    caps = []
    if count == 0:
        caps.append("No countable SQL queries were found in the analysed files.")
    if count == 1 and score > ONE_QUERY_CAP:
        score = ONE_QUERY_CAP
        caps.append(f"Only one distinct query was found, so the score is capped at {ONE_QUERY_CAP}.")
    if 0 < count < MIN_DISTINCT_QUERIES:
        caps.append(f"Fewer than {MIN_DISTINCT_QUERIES} distinct queries: shown as Limited evidence.")

    notes = ["Only SQL in Python execute/text/read_sql calls and .sql files is analysed; test files are not scored."]
    if unresolved:
        notes.append(
            f"{unresolved} SQL call(s) pass their query in a variable that couldn't be resolved statically; not analysed."
        )
    if count and not any(o["lines_written"] for group in distinct for o in group["occurrences"]):
        notes.append("Line history (git blame) was unavailable, so line authorship couldn't be measured.")
    return {
        "score": score,
        "label": _label(score, count),
        "cap_reason": " ".join(caps) or None,
        "parts": parts,
        "concepts_found": concepts_found,
        "concepts_missing": concepts_missing,
        "distinct_query_count": count,
        "distinct_queries": [_query_summary(group) for group in distinct],
        "skipped": skipped[:MAX_SKIPPED],
        "skipped_total": len(skipped),
        "notes": notes,
        "note": NOTE,
        "weights_note": WEIGHTS_NOTE,
    }


def _integration(
    distinct: list[dict[str, Any]], other_statements: list[dict[str, Any]]
) -> tuple[float, str, list[dict[str, Any]]]:
    """I: queries reached from application code (40), tables with a connected workflow (30), and
    relationships between tables from joins or foreign keys (30). Static references only."""
    count = len(distinct)
    if not count:
        return 0, "No countable queries were found.", []
    wired = [
        group for group in distinct
        if any(o["wired"]["status"] != "none" for o in group["occurrences"])
    ]
    wiring_points = 40 * len(wired) / count
    evidence = [
        {**next(o["wired"]["evidence"] for o in group["occurrences"] if o["wired"]["evidence"]),
         "detail": next(o["wired"]["detail"] for o in group["occurrences"] if o["wired"]["status"] != "none")}
        for group in wired
    ]
    # A workflow: one table written and read (or changed in more than one way) by distinct queries.
    operations: dict[tuple[str, str], set[str]] = {}
    for group in distinct:
        for repo_name in {item["repo"] for item in group["occurrences"]}:
            if group["statement"] == "select":
                for table in group["tables"]:
                    operations.setdefault((repo_name, table), set()).add("select")
            elif group["target"]:
                operations.setdefault((repo_name, group["target"]), set()).add(group["statement"])
    workflows = sorted((repo, table) for (repo, table), ops in operations.items() if len(ops) >= 2)
    workflow_points = 0 if not workflows else 15 if len(workflows) == 1 else 30
    pairs = set().union(*(group["join_pairs"] for group in distinct), *(s["pairs"] for s in other_statements))
    relationship_points = 0 if not pairs else 15 if len(pairs) == 1 else 30
    parts = [f"{len(wired)} of {count} distinct queries are reached from application code ({round(wiring_points)}/40)"]
    parts.append(
        "connected workflows on " + ", ".join(
            f"{repo}/{table} ({'/'.join(sorted(operations[(repo, table)]))})"
            for repo, table in workflows
        ) + f" ({workflow_points}/30)" if workflows else "no table is both written and read by distinct queries (0/30)"
    )
    parts.append(
        "related tables " + ", ".join(f"{a}–{b}" for a, b in sorted(pairs)) + f" ({relationship_points}/30)"
        if pairs else "no joins or foreign keys between tables (0/30)"
    )
    reason = "; ".join(parts) + ". From static references in the analysed files; runtime behaviour is not inferred."
    return wiring_points + workflow_points + relationship_points, reason, evidence


def _public_ref(occurrence: dict[str, Any]) -> dict[str, Any]:
    return {key: occurrence[key] for key in ("repo", "file_path", "start_line", "end_line", "url", "snippet")}


def _query_summary(group: dict[str, Any]) -> dict[str, Any]:
    occurrences = group["occurrences"]
    known = [o["lines_written"] for o in occurrences if o["lines_written"]]
    test = next((o["test"] for o in occurrences if o["test"]), None)
    wired = next((o["wired"] for o in occurrences if o["wired"]["status"] != "none"), occurrences[0]["wired"])
    return {
        "shape": group["shape"],
        "statement": group["statement"],
        "level": group["level"],
        "level_label": LEVEL_LABELS[group["level"]],
        "concepts": [CONCEPTS[concept] for concept in CONCEPTS if concept in group["concepts"]],
        "tables": sorted(group["tables"]),
        "occurrence_count": len(occurrences),
        "evidence": [
            {**_public_ref(o), "lines_written": o["lines_written"], "copy_check": o["copy_check"], "advisory": o["advisory"]}
            for o in occurrences[:MAX_EVIDENCE]
        ],
        "lines_written": max(known, key=lambda item: item["written"] / item["cited"]) if known else None,
        "wired": {"status": wired["status"], "detail": wired["detail"], "evidence": wired["evidence"]},
        "test": test,
        "correctness": "unverified",
    }


def safe_sql_depth(ingestion: dict[str, Any], verified: dict[str, Any]) -> dict[str, Any] | None:
    """compute_sql_depth that never fails a passport: an unexpected error stores None."""
    try:
        return compute_sql_depth(ingestion, verified)
    except Exception:  # read-only extra; verified evidence is valid without it
        logger.exception("SQL evidence depth could not be computed")
        return None


def student_sql_depth(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """The result without detector internals (copy-check status and advisory signals)."""
    if not result:
        return result
    view = {**result, "distinct_queries": []}
    for query in result.get("distinct_queries", []):
        view["distinct_queries"].append({
            **query,
            "evidence": [
                {key: value for key, value in item.items() if key not in {"copy_check", "advisory"}}
                for item in query.get("evidence", [])
            ],
        })
    return view
