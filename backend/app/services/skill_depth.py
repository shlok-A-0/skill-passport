"""Deterministic, citation-backed summaries of skill evidence in sampled repositories.

These are evidence summaries, not proficiency estimates. Signals are capped and only come
from candidate-attributed, non-fork files that have not been excluded by verification.
"""
from __future__ import annotations

import ast
import copy
import json
import logging
import re
import statistics
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from app.services.sql_depth import safe_sql_depth

logger = logging.getLogger(__name__)
DISCLAIMER = (
    "Skill Evidence Depth summarizes patterns found in analyzed repository files. "
    "It is not a measure of overall proficiency; missing patterns do not mean the skill is absent."
)
MIN_COVERAGE = 60
MIN_COMPETENCIES = 3

# (id, label, weight, foundational); weights total 100 in each profile.
PROFILES: dict[str, list[tuple[str, str, int, bool]]] = {
    "python": [
        ("structure", "Functions and modular structure", 25, True),
        ("data", "Data handling and transformation", 20, True),
        ("errors", "Error handling", 20, True),
        ("interfaces", "Reusable interfaces and abstractions", 20, False),
        ("robustness", "Input validation and defensive behavior", 15, False),
    ],
    "react": [
        ("components", "Reusable components", 25, True),
        ("state", "State and event handling", 25, True),
        ("composition", "Component composition and props", 20, True),
        ("effects", "Effects and lifecycle boundaries", 15, False),
        ("accessibility", "Accessible interaction signals", 15, False),
    ],
    "fastapi": [
        ("routes", "Route design and HTTP behavior", 25, True),
        ("validation", "Request and response validation", 25, True),
        ("dependencies", "Dependency injection", 20, True),
        ("errors", "Explicit error handling", 15, False),
        ("async", "Async endpoint behavior", 15, False),
    ],
    "docker": [
        ("build", "Explicit image build stages", 25, True),
        ("runtime", "Runtime and process configuration", 20, True),
        ("security", "Least-privilege runtime signals", 20, True),
        ("health", "Health checks and readiness", 15, False),
        ("compose", "Service orchestration", 20, False),
    ],
    "git": [
        ("authored", "Candidate-authored repository changes", 30, True),
        ("breadth", "Changes across distinct project files", 25, True),
        ("history", "Usable commit history", 20, True),
        ("config", "Repository collaboration configuration", 15, False),
        ("workflow", "Workflow-linked changes", 10, False),
    ],
    "unit-testing": [
        ("behavior", "Behavior-focused assertions", 25, True),
        ("cases", "Multiple test cases", 20, True),
        ("negative", "Failure and boundary cases", 20, True),
        ("isolation", "Test isolation and controlled dependencies", 20, False),
        ("targeting", "Tests linked to analyzed source", 15, False),
    ],
    "scikit-learn": [
        ("estimator", "Estimator use and fit/predict flow", 25, True),
        ("split", "Held-out validation", 25, True),
        ("preprocessing", "Preprocessing and feature preparation", 20, True),
        ("pipeline", "Leakage-aware pipelines or cross-validation", 20, False),
        ("metrics", "Explicit evaluation metrics", 10, False),
    ],
}

PATTERNS: dict[str, dict[str, tuple[str, ...]]] = {
    "python": {
        "structure": (r"^\s*(?:async\s+)?def\s+\w+\s*\(", r"^\s*class\s+\w+"),
        "data": (r"\b(?:for|yield)\b", r"\.(?:map|filter|groupby|sort_values|apply)\s*\("),
        "errors": (r"\btry\s*:", r"\bexcept\b"),
        "interfaces": (r"^\s*@(?:dataclass|property)", r"^\s*class\s+\w+"),
        "robustness": (r"\b(?:isinstance|assert)\s*\(", r"\braise\s+(?:ValueError|TypeError|KeyError)"),
    },
    "react": {
        "components": (r"\bfunction\s+[A-Z]\w*\s*\(", r"\bconst\s+[A-Z]\w*\s*=\s*(?:\([^)]*\)|\w+)\s*=>"),
        "state": (r"\buseState\s*\(", r"\bon(?:Click|Change|Submit)\s*="),
        "composition": (r"\bchildren\b", r"<[A-Z]\w+[\s/>]", r"\bprops\s*\."),
        "effects": (r"\buseEffect\s*\(", r"\buseMemo\s*\("),
        "accessibility": (r"\b(?:aria-label|aria-describedby|role)\s*=", r"<label\b", r"\b(?:htmlFor|tabIndex)\s*="),
    },
    "fastapi": {
        "routes": (r"@\w+\.(?:get|post|put|patch|delete|api_route)\s*\(",),
        "validation": (r"\b(?:BaseModel|Field|Query|Path|Body)\b", r"->\s*(?:\w+Response|\w+Model)"),
        "dependencies": (r"\bDepends\s*\(",),
        "errors": (r"\bHTTPException\s*\(", r"\b(?:try\s*:|except\s+\w+)"),
        "async": (r"async\s+def\s+\w+", r"\bawait\s+"),
    },
    "docker": {
        "build": (r"(?im)^\s*FROM\s+.+", r"(?im)^\s*(?:COPY|ADD|RUN)\s+.+"),
        "runtime": (r"(?im)^(?:WORKDIR|CMD|ENTRYPOINT|EXPOSE)\s+.+",),
        "security": (r"(?im)^USER\s+(?!root\b|0\b)\S+",),
        "health": (r"(?im)^HEALTHCHECK\s+.+",),
        "compose": (r"(?m)^\s{0,4}services\s*:", r"(?m)^\s{2,}healthcheck\s*:"),
    },
    "unit-testing": {
        "behavior": (r"\b(?:assert\s+|assertEqual\s*\(|expect\s*\(|should\s*\(|assertThat\s*\()",),
        "cases": (r"\b(?:def\s+test_\w+|it\s*\(|test\s*\(|pytest\.mark\.parametrize|parameterized)\b",),
        "negative": (r"\b(?:raises\s*\(|assertRaises\s*\(|toThrow\s*\(|rejects\.)", r"\b(?:invalid|empty|missing|boundary|limit)\b"),
        "isolation": (r"\b(?:monkeypatch|mock|Mock|patch\s*\(|fixture|setUp)\b",),
        "targeting": (r"\bfrom\s+\w+\s+import\b", r"\bimport\s+\w+"),
    },
    "scikit-learn": {
        "estimator": (r"\b(?:\.fit\s*\(|\.predict\s*\(|\w+Classifier\s*\(|\w+Regressor\s*\()",),
        "split": (r"\btrain_test_split\s*\(", r"\b(?:cross_val_score|KFold|StratifiedKFold)\s*\("),
        "preprocessing": (r"\b(?:StandardScaler|OneHotEncoder|ColumnTransformer|SimpleImputer)\s*\(", r"\b(?:get_dummies|LabelEncoder)\s*\("),
        "pipeline": (r"\b(?:make_pipeline|Pipeline)\s*\(", r"\bGridSearchCV\s*\("),
        "metrics": (r"\b(?:accuracy_score|f1_score|roc_auc_score|mean_squared_error|classification_report)\s*\(",),
    },
}

SKLEARN_ESTIMATOR_NAMES = {
    "LogisticRegression", "LogisticRegressionCV", "LinearRegression", "Ridge", "RidgeCV",
    "RidgeClassifier", "RidgeClassifierCV", "Lasso", "LassoCV", "LassoLars", "ElasticNet",
    "ElasticNetCV", "SGDClassifier", "SGDRegressor", "PassiveAggressiveClassifier",
    "PassiveAggressiveRegressor", "Perceptron", "BayesianRidge", "ARDRegression",
    "HuberRegressor", "RANSACRegressor", "TheilSenRegressor", "PoissonRegressor",
    "GammaRegressor", "TweedieRegressor", "SVC", "NuSVC", "LinearSVC", "SVR", "NuSVR",
    "LinearSVR", "OneClassSVM", "KMeans", "MiniBatchKMeans", "DBSCAN", "PCA",
    "TruncatedSVD", "GaussianNB", "MultinomialNB", "BernoulliNB", "ComplementNB",
    "DecisionTreeClassifier", "DecisionTreeRegressor", "RandomForestClassifier",
    "RandomForestRegressor", "ExtraTreesClassifier", "ExtraTreesRegressor",
    "GradientBoostingClassifier", "GradientBoostingRegressor", "HistGradientBoostingClassifier",
    "HistGradientBoostingRegressor", "AdaBoostClassifier", "AdaBoostRegressor",
    "KNeighborsClassifier", "KNeighborsRegressor", "MLPClassifier", "MLPRegressor",
}


def _line_share(source: dict[str, Any], start: int, end: int, username: str) -> tuple[bool, bool]:
    """Return whether line history is complete and whether the candidate owns at least half."""
    blame = source.get("blame")
    if not blame:
        return False, False
    total = end - start + 1
    identified = yours = 0
    for first, last, login in blame:
        overlap = max(0, min(end, last) - max(start, first) + 1)
        if overlap and login:
            identified += overlap
            yours += overlap if login.casefold() == username.casefold() else 0
    return identified == total, identified == total and yours / total >= 0.5


def _has_candidate_authored_lines(source: dict[str, Any], username: str, line_count: int) -> bool:
    """Whether current line blame attributes any content in this file to the candidate."""
    if not username:
        return False
    return any(
        login and login.casefold() == username.casefold()
        and min(line_count, last) >= max(1, first)
        for first, last, login in (source.get("blame") or [])
    )


def _excluded_files(verified: dict[str, Any]) -> set[tuple[str, str]]:
    excluded = set()
    for item in verified.get("rejected_claims", []):
        if item.get("kind") not in {"earlier_source", "copied", "not_written_by_candidate"}:
            continue
        claim = item.get("claim") or {}
        repo = claim.get("repo") or item.get("repo")
        path = claim.get("file_path") or claim.get("path") or item.get("file_path")
        if repo and path:
            excluded.add((repo, path))
    return excluded


def _citation(repo: dict[str, Any], path: str, source: dict[str, Any], start: int,
              end: int, explanation: str) -> dict[str, Any]:
    lines = source.get("lines") or []
    sha = repo.get("head_sha") or "HEAD"
    url = "https://github.com/" + repo.get("full_name", repo["name"]) + "/blob/" + sha
    url += "/" + quote(path, safe="/") + f"#L{start}-L{end}"
    return {"repo": repo["name"], "file_path": path, "start_line": start, "end_line": end,
            "commit_sha": sha, "url": url, "snippet": "\n".join(lines[start - 1:min(end, start + 4)]),
            "explanation": explanation}


def _python_import_aliases(root: ast.AST) -> dict[str, str]:
    """Map local import names to their fully qualified modules/symbols."""
    aliases: dict[str, str] = {}
    for node in ast.walk(root):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".")[0]
                aliases[local] = item.name if item.asname else item.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                if item.name == "*":
                    continue
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def _resolved_name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolved_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Subscript):
        return _resolved_name(node.value, aliases)
    return ""


def _has_decorator(node: ast.AST, suffixes: tuple[str, ...], aliases: dict[str, str]) -> bool:
    return any(_resolved_name(decorator.func if isinstance(decorator, ast.Call) else decorator, aliases)
               .endswith(suffixes) for decorator in getattr(node, "decorator_list", []))


def _fixture_decorator(node: ast.AST, aliases: dict[str, str]) -> ast.AST | None:
    """The @pytest.fixture / @fixture decorator (with or without arguments) on a function."""
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _resolved_name(target, aliases) in {"pytest.fixture", "fixture"}:
            return decorator
    return None


def _imports_own_module(node: ast.AST, own_modules: set[str]) -> bool:
    """An import of one of the candidate's analyzed source modules (not pytest, stdlib or packages)."""
    if isinstance(node, ast.Import):
        return any(alias.name in own_modules for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        if node.module and node.module in own_modules:
            return True
        prefix = node.module + "." if node.module else ""
        return any(prefix + alias.name in own_modules for alias in node.names)
    return False


def _own_python_modules(repo: dict[str, Any]) -> set[str]:
    """Module paths of the candidate's analyzed, non-test Python files: `app/db.py` -> app.db, db."""
    from app.services.github_ingest import is_test_path
    modules: set[str] = set()
    for path, source in (repo.get("source_files") or {}).items():
        pure = PurePosixPath(path)
        if pure.suffix != ".py" or is_test_path(path) or not source.get("authored_shas"):
            continue
        parts = list(pure.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules.update(".".join(parts[start:]) for start in range(len(parts)))
    return modules


# Vite's generated entry point: createRoot(...).render(<StrictMode><App /></StrictMode>).
VITE_SCAFFOLD_SIGNAL = re.compile(r"^(?:<(?:React\.)?StrictMode>\s*<App\s*/>\s*</(?:React\.)?StrictMode>|<App\s*/>)$")


def _is_vite_scaffold(path: str, text: str, signal: str) -> bool:
    return (PurePosixPath(path).name.lower() in {"main.tsx", "main.jsx"} and "createRoot(" in text
            and bool(VITE_SCAFFOLD_SIGNAL.match(signal.strip())))


def _python_framework_context(skill_id: str, text: str) -> tuple[ast.AST | None, dict[str, str]]:
    try:
        root = ast.parse(text)
    except (SyntaxError, ValueError):
        return None, {}
    aliases = _python_import_aliases(root)
    module = {"fastapi": "fastapi", "scikit-learn": "sklearn"}.get(skill_id)
    if module and not any(value == module or value.startswith(module + ".") for value in aliases.values()):
        return None, aliases
    return root, aliases


def _eligible_sources(ingestion: dict[str, Any], verified: dict[str, Any]):
    excluded = _excluded_files(verified)
    username = ingestion.get("username", "")
    for repo in ingestion.get("repos", []):
        if repo.get("is_fork"):
            continue
        for path, source in (repo.get("source_files") or {}).items():
            if (repo.get("name"), path) in excluded or not source.get("authored_shas"):
                continue
            lines = source.get("lines") or []
            if any(line.strip() for line in lines):  # empty files never yield citations
                yield repo, path, source, "\n".join(lines)


def _content_span(lines: list[str]) -> tuple[int, int] | None:
    """First and last non-blank line, or None for an empty file."""
    filled = [index for index, line in enumerate(lines, 1) if line.strip()]
    return (filled[0], filled[-1]) if filled else None


def _ast_matches(skill_id: str, competency: str, text: str,
                 own_modules: set[str] | None = None) -> list[tuple[int, int, str]]:
    try:
        root = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    aliases = _python_import_aliases(root)
    pydantic_models = {
        node.name for node in ast.walk(root)
        if isinstance(node, ast.ClassDef)
        and any(_resolved_name(base, aliases) == "pydantic.BaseModel" for base in node.bases)
    }
    fastapi_routes: set[str] = set()
    sklearn_estimators: set[str] = set()
    for node in ast.walk(root):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Call):
                called = _resolved_name(value.func, aliases)
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if called in {"fastapi.FastAPI", "fastapi.APIRouter"}:
                        fastapi_routes.add(target.id)
                    if called.startswith("sklearn.") and (
                        called.rsplit(".", 1)[-1] in SKLEARN_ESTIMATOR_NAMES
                        or called.rsplit(".", 1)[-1].endswith(("Classifier", "Regressor"))
                        or called in {"sklearn.pipeline.Pipeline", "sklearn.pipeline.make_pipeline"}
                    ):
                        sklearn_estimators.add(target.id)

    def is_fastapi_route(function: ast.AST) -> bool:
        for decorator in getattr(function, "decorator_list", []):
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr not in {"get", "post", "put", "patch", "delete", "api_route"}:
                continue
            if isinstance(decorator.func.value, ast.Name) and decorator.func.value.id in fastapi_routes:
                return True
        return False

    result: list[tuple[int, int, str]] = []
    for node in ast.walk(root):
        name = ""
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
        matched = False
        span: ast.AST = node
        if skill_id == "python":
            matched = {
                "structure": isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)),
                "data": isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.Yield, ast.YieldFrom,
                                          ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp))
                        or name in {"map", "filter", "groupby", "sort_values", "apply", "sorted"},
                "errors": isinstance(node, (ast.Try, ast.Raise)),
                # A class counts only with behavior (a method) or as an explicit interface;
                # a field-only model or an empty exception subclass is not an abstraction.
                "interfaces": (isinstance(node, ast.ClassDef) and (
                                   any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in node.body)
                                   or any(_resolved_name(base, aliases) in {
                                       "typing.Protocol", "typing_extensions.Protocol", "abc.ABC"}
                                          for base in node.bases)))
                              or (isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                                  and _has_decorator(node, ("dataclass", "property"), aliases)),
                "robustness": name == "isinstance" or
                              (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                               and ast.unparse(node.exc.func) in {"ValueError", "TypeError", "KeyError"}),
            }[competency]
        elif skill_id == "fastapi":
            resolved = _resolved_name(node.func, aliases) if isinstance(node, ast.Call) else ""
            validation_names = {
                "fastapi.Field", "fastapi.Query", "fastapi.Path", "fastapi.Body",
                "pydantic.Field", *pydantic_models,
            }
            matched = {
                "routes": isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_fastapi_route(node),
                "validation": any(
                    isinstance(part, (ast.Name, ast.Attribute))
                    and (_resolved_name(part, aliases) in validation_names
                         or (isinstance(part, ast.Name) and part.id in pydantic_models))
                    for part in ast.walk(node)
                ) or any(_resolved_name(base, aliases) == "pydantic.BaseModel"
                         for base in getattr(node, "bases", [])),
                "dependencies": isinstance(node, ast.Call) and resolved == "fastapi.Depends",
                "errors": isinstance(node, ast.Call) and resolved == "fastapi.HTTPException",
                "async": isinstance(node, ast.AsyncFunctionDef) and is_fastapi_route(node),
            }[competency]
        elif skill_id == "scikit-learn":
            resolved = _resolved_name(node.func, aliases) if isinstance(node, ast.Call) else ""
            sklearn_calls = {
                "sklearn.model_selection.train_test_split", "sklearn.model_selection.cross_val_score",
                "sklearn.model_selection.KFold", "sklearn.model_selection.StratifiedKFold",
                "sklearn.model_selection.GridSearchCV", "sklearn.pipeline.Pipeline",
                "sklearn.pipeline.make_pipeline", "sklearn.preprocessing.StandardScaler",
                "sklearn.preprocessing.OneHotEncoder", "sklearn.preprocessing.LabelEncoder",
                "sklearn.compose.ColumnTransformer", "sklearn.impute.SimpleImputer",
                "sklearn.metrics.accuracy_score", "sklearn.metrics.f1_score",
                "sklearn.metrics.roc_auc_score", "sklearn.metrics.mean_squared_error",
                "sklearn.metrics.classification_report",
            }
            receiver = (node.func.value.id if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                        else None)
            matched = {
                "estimator": (isinstance(node, ast.Call) and resolved.startswith("sklearn.")
                              and resolved.rsplit(".", 1)[-1].endswith(("Classifier", "Regressor")))
                             or (isinstance(node, ast.Call) and name in {"fit", "predict"}
                                 and receiver in sklearn_estimators),
                "split": isinstance(node, ast.Call) and resolved in {
                    "sklearn.model_selection.train_test_split", "sklearn.model_selection.cross_val_score",
                    "sklearn.model_selection.KFold", "sklearn.model_selection.StratifiedKFold"},
                "preprocessing": isinstance(node, ast.Call) and resolved in {
                    "sklearn.preprocessing.StandardScaler", "sklearn.preprocessing.OneHotEncoder",
                    "sklearn.preprocessing.LabelEncoder", "sklearn.compose.ColumnTransformer",
                    "sklearn.impute.SimpleImputer"},
                "pipeline": isinstance(node, ast.Call) and resolved in {
                    "sklearn.pipeline.make_pipeline", "sklearn.pipeline.Pipeline",
                    "sklearn.model_selection.GridSearchCV"},
                "metrics": isinstance(node, ast.Call) and resolved in sklearn_calls and resolved.startswith("sklearn.metrics."),
            }[competency]
        elif skill_id == "unit-testing":
            fixture = (_fixture_decorator(node, aliases)
                       if competency == "isolation" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                       else None)
            matched = {
                "behavior": isinstance(node, ast.Assert) or name in {"assertEqual", "assertThat", "expect"},
                "cases": (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"))
                         or name in {"it", "test"},
                "negative": name in {"raises", "assertRaises", "toThrow"} or
                            (isinstance(node, ast.Constant) and isinstance(node.value, str)
                             and any(word in node.value.lower() for word in ("invalid", "empty", "missing", "boundary", "limit"))),
                "isolation": name in {"monkeypatch", "Mock", "patch", "fixture", "setUp"} or fixture is not None,
                "targeting": _imports_own_module(node, own_modules or set()),
            }[competency]
            if fixture is not None:
                span = fixture  # cite the decorator, not the whole fixture function
        else:
            matched = False
        if matched and getattr(span, "lineno", None):
            line = span.lineno
            label = ast.unparse(span)[:120]
            result.append((line, getattr(span, "end_lineno", line) or line, label))
            if len(result) >= 3:
                break
    return result


def _react_matches(competency: str, text: str, path: str) -> list[tuple[int, int, str]]:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_typescript as ts
        # JavaScript projects commonly keep JSX in `.js` files (not only `.jsx`).
        # The TSX grammar accepts ordinary JavaScript too and is required to parse JSX.
        grammar = ts.language_tsx() if path.lower().endswith((".tsx", ".jsx", ".js")) else ts.language_typescript()
        parser = Parser(Language(grammar))
        tree = parser.parse(text.encode("utf-8"))
    except (ImportError, TypeError, AttributeError):
        return []
    if tree.root_node.has_error:
        return []
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        raw = node.text.decode("utf-8", errors="replace")
        lower = raw.lower()
        kind = node.type
        if competency == "components":
            matched = kind in {"function_declaration", "arrow_function", "function_expression"} and "<" in raw
        elif competency == "state":
            matched = (kind == "call_expression" and re.search(r"\buseState\s*\(", raw)) or (
                kind == "jsx_attribute" and re.match(r"on(?:Click|Change|Submit)\s*=", raw))
        elif competency == "composition":
            matched = kind in {"jsx_element", "jsx_self_closing_element"} and (
                bool(re.search(r"<[A-Z]\w+", raw)) or "children" in lower)
        elif competency == "effects":
            matched = kind == "call_expression" and re.search(r"\b(?:useEffect|useMemo)\s*\(", raw)
        else:
            matched = kind == "jsx_attribute" and re.match(r"(?:aria-label|aria-describedby|role|htmlFor|tabIndex)\s*=", raw)
        if matched:
            start = node.start_point.row + 1
            end = max(start, node.end_point.row + (1 if node.end_point.column else 0))
            found.append((start, end, raw.strip()[:120]))
            if len(found) >= 3:
                break
        stack.extend(reversed(node.children))
    return found


def _matches(skill_id: str, competency: str, text: str, path: str = "",
             own_modules: set[str] | None = None) -> list[tuple[int, int, str]]:
    if skill_id in {"python", "fastapi", "scikit-learn", "unit-testing"}:
        return _ast_matches(skill_id, competency, text, own_modules)
    if skill_id == "react":
        return _react_matches(competency, text, path)
    if skill_id == "docker":
        if PurePosixPath(path).name.lower() != "dockerfile":
            try:
                import yaml
                document = yaml.safe_load(text)
            except Exception:
                return []
            if not isinstance(document, dict):
                return []
        lines = text.splitlines()
        patterns = PATTERNS["docker"][competency]
        return [(index, index, line.strip()) for index, line in enumerate(lines, 1)
                if any(re.search(pattern, line) for pattern in patterns)][:1]
    for pattern in PATTERNS.get(skill_id, {}).get(competency, ()):
        match = re.search(pattern, text)
        if match:
            start = text.count("\n", 0, match.start()) + 1
            end = text.count("\n", 0, match.end()) + 1
            return [(start, end, match.group(0).strip())]
    return []


def _docker_config_valid(path: str, text: str) -> bool:
    if PurePosixPath(path).name.lower() == "dockerfile":
        return any(re.match(r"(?i)^\s*(FROM|RUN|CMD|ENTRYPOINT|WORKDIR|COPY|ADD|USER|EXPOSE|ENV|ARG|HEALTHCHECK)\b", line)
                   for line in text.splitlines())
    try:
        import yaml
        value = yaml.safe_load(text)
        return isinstance(value, dict) and isinstance(value.get("services"), dict)
    except Exception:
        return False


def _package_has_react(text: str) -> bool:
    try:
        package = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return any("react" in (package.get(group) or {})
               for group in ("dependencies", "devDependencies", "peerDependencies"))


def _parse_ok(skill_id: str, path: str, text: str) -> bool:
    if skill_id == "unit-testing" and PurePosixPath(path).suffix.lower() != ".py":
        return False
    if path.endswith(".py") and skill_id in {"python", "fastapi", "scikit-learn", "unit-testing"}:
        try:
            ast.parse(text)
        except (SyntaxError, ValueError):
            return False
    if skill_id == "react" and path.lower().endswith((".tsx", ".ts", ".jsx", ".js")):
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_typescript as ts

            grammar = ts.language_tsx() if path.lower().endswith((".tsx", ".jsx", ".js")) else ts.language_typescript()
            parser = Parser(Language(grammar))
            tree = parser.parse(text.encode("utf-8"))
            return not tree.root_node.has_error
        except ImportError:
            # Missing parser is not silently treated as valid source: caller marks it unassessable.
            return False
    return True


def _generic_profile(skill_id: str, ingestion: dict[str, Any], verified: dict[str, Any]) -> dict[str, Any]:
    profile = PROFILES[skill_id]
    evidence: dict[str, list[dict[str, Any]]] = {row[0]: [] for row in profile}
    assessable: set[str] = set()
    uncertain: set[str] = set()
    username = ingestion.get("username", "")
    for repo, path, source, text in _eligible_sources(ingestion, verified):
        if skill_id == "docker" and PurePosixPath(path).name.lower() != "dockerfile":
            continue
        suffix = PurePosixPath(path).suffix.lower()
        if skill_id in {"python", "fastapi", "scikit-learn"} and suffix != ".py":
            continue
        if skill_id == "react" and suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        if skill_id == "react":
            package_context = any(
                PurePosixPath(dep_path).name == "package.json" and _package_has_react(content)
                for dep_path, content in (repo.get("dependency_contents") or {}).items()
            )
            source_context = bool(re.search(
                r"(?:from\s+['\"]react['\"]|require\s*\(\s*['\"]react['\"]|"
                r"\bReact\.|\buse(?:State|Effect|Memo|Callback)\s*\()",
                text,
            ))
            if not (package_context or source_context):
                continue
        if skill_id == "unit-testing":
            from app.services.github_ingest import is_test_path
            if not is_test_path(path):
                continue
        if skill_id in {"fastapi", "scikit-learn"}:
            framework_root, _ = _python_framework_context(skill_id, text)
            if framework_root is None:
                continue
        if skill_id == "git":
            continue
        file_lines = source.get("lines") or text.splitlines()
        attribution_complete, _candidate_majority = _line_share(source, 1, max(1, len(file_lines)), username)
        if not attribution_complete or not _has_candidate_authored_lines(source, username, len(file_lines)):
            uncertain.update(row[0] for row in profile)
            continue
        own_modules = _own_python_modules(repo) if skill_id == "unit-testing" else None
        cited_spans: dict[str, tuple[int, int]] = {}
        for competency, _label, _weight, _foundation in profile:
            if skill_id == "docker" and competency == "compose":
                continue
            if not _parse_ok(skill_id, path, text):
                continue
            assessable.add(competency)
            for start, end, signal in _matches(skill_id, competency, text, path, own_modules):
                if skill_id == "react" and _is_vite_scaffold(path, text, signal):
                    continue
                # One raise statement is evidence for error handling or validation, not both.
                if skill_id == "python" and competency == "robustness" and cited_spans.get("errors") == (start, end):
                    continue
                known, authored = _line_share(source, start, end, username)
                if not known or not authored:
                    uncertain.add(competency)
                    continue
                evidence[competency].append(_citation(
                    repo, path, source, start, end, f"Observed {signal} in an attributable source file."
                ))
                cited_spans[competency] = (start, end)
                break

    # Dockerfiles and Compose files come from the bounded structured config reader. Existing
    # authored_paths confirms a user commit to that path; unsupported config leaves stay unknown.
    if skill_id == "docker":
        for repo in ingestion.get("repos", []):
            if repo.get("is_fork"):
                continue
            for path, text in (repo.get("config_contents") or {}).items():
                basename = PurePosixPath(path).name.lower()
                is_dockerfile = basename == "dockerfile"
                is_compose = basename in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
                if not (repo.get("authored_paths") or {}).get(path) or not (is_dockerfile or is_compose):
                    continue
                if not _docker_config_valid(path, text):
                    continue
                for competency, _label, _weight, _foundation in profile:
                    if (competency == "compose") != is_compose:
                        continue
                    assessable.add(competency)
                    lines = text.splitlines()
                    for start, end, signal in _matches(skill_id, competency, text, path):
                        evidence[competency].append(_citation(
                            repo, path, {"lines": lines}, start, end,
                            f"Observed {signal} in a candidate-authored container configuration."
                        ))
                        break

    # Git uses the existing author-filtered history and authored source-path metadata. Commit
    # volume is never scored; citations point at exact files changed in user-authored commits.
    if skill_id == "git":
        for repo in ingestion.get("repos", []):
            if repo.get("is_fork"):
                continue
            # Cite each file's content, never a placeholder line 1; empty files and files whose only
            # content is line 1 are left out.
            authored = [(path, src, span) for path, src in (repo.get("source_files") or {}).items()
                        if src.get("authored_shas") and (repo.get("name"), path) not in _excluded_files(verified)
                        and (span := _content_span(src.get("lines") or [])) and span != (1, 1)]
            commits = repo.get("user_commits") or []
            if authored and commits:
                assessable.update({"authored", "breadth", "history"})
                for path, src, (start, end) in authored[:2]:
                    ref = _citation(repo, path, src, start, end,
                                    "Candidate-authored file linked to GitHub commit history; commit count is not scored.")
                    evidence["authored"].append(ref)
                if len(authored) >= 2:
                    evidence["breadth"].extend(
                        _citation(repo, path, src, start, end,
                                  "A second candidate-authored project file shows repository change breadth.")
                        for path, src, (start, end) in authored[:2]
                    )
                evidence["history"].append(evidence["authored"][0])
            gitignore = (repo.get("config_contents") or {}).get(".gitignore")
            ignore_lines = (gitignore or "").splitlines()
            meaningful_ignore = next(
                (index for index, line in enumerate(ignore_lines, 1)
                 if line.strip() and not line.lstrip().startswith("#")),
                None,
            )
            if meaningful_ignore and (repo.get("authored_paths") or {}).get(".gitignore"):
                assessable.add("config")
                evidence["config"].append(_citation(
                    repo, ".gitignore", {"lines": ignore_lines}, meaningful_ignore, meaningful_ignore,
                    "Candidate-authored repository ignore configuration is present."
                ))

    competencies = []
    applicable = assessable_weight = earned = count = 0
    foundational_observed = True
    for cid, label, weight, foundational in profile:
        applicable += weight
        citations = evidence[cid][:3]
        if cid not in assessable or (cid in uncertain and not citations):
            status, points = "insufficient_evidence", 0
            explanation = "This competency could not be assessed from attributable sampled files."
            if foundational:
                foundational_observed = False
        elif citations:
            status, points = "observed", weight
            explanation = "A qualifying signal was found in candidate-attributed evidence."
            assessable_weight += weight
            earned += points
            count += 1
        else:
            status, points = "not_observed", 0
            explanation = "No qualifying signal was found in the applicable analyzed files; this does not mean the skill is absent."
            assessable_weight += weight
            count += 1
            if foundational:
                foundational_observed = False
        competencies.append({"id": cid, "label": label, "weight": weight, "points": points,
                             "status": status, "explanation": explanation, "evidence": citations})
    coverage = round(100 * assessable_weight / applicable) if applicable else 0
    enough = coverage >= MIN_COVERAGE and count >= MIN_COMPETENCIES
    score = round(100 * earned / assessable_weight) if enough and assessable_weight else None
    if score is None:
        label = "Insufficient evidence"
    elif score >= 70 and foundational_observed:
        label = "Broad evidence"
    elif score >= 40 or score >= 70:
        label = "Developing evidence"
    else:
        label = "Foundation evidence"
    return {"rubric_version": "pilot-1", "score": score, "label": label,
            "coverage": {"percent": coverage, "assessable_weight": assessable_weight,
                         "applicable_weight": applicable, "assessable_competencies": count,
                         "total_competencies": len(profile)},
            "competencies": competencies, "disclaimer": DISCLAIMER}


def _sql_adapter(
    result: dict[str, Any] | None,
    ingestion: dict[str, Any],
) -> dict[str, Any] | None:
    if not result:
        return None
    competencies = []
    commit_shas = {repo.get("name"): repo.get("head_sha") or "HEAD"
                   for repo in ingestion.get("repos", [])}
    for key, part in result["parts"].items():
        citations = [{
            **{field: item[field] for field in
               ("repo", "file_path", "start_line", "end_line", "url", "snippet")
               if field in item},
            "commit_sha": commit_shas.get(item.get("repo"), "HEAD"),
        } for item in part.get("evidence", [])]
        competencies.append({"id": key, "label": part["label"],
                             "weight": round(part["weight"] * 100), "points": round(part["points"]),
                             "status": "observed" if citations else "not_observed",
                             "explanation": part["reason"], "evidence": citations})
    return {"rubric_version": "sql-legacy", "score": result["score"], "label": result["label"],
            "coverage": {"percent": 100, "assessable_weight": 100, "applicable_weight": 100,
                         "assessable_competencies": len(competencies), "total_competencies": len(competencies)},
            "competencies": competencies, "disclaimer": result.get("note", DISCLAIMER)}


_CODE_METRICS_NOTE = (
    "Function-level structural metrics over candidate-authored Python files in the analyzed "
    "sample. Decision points count branching constructs; they flag code worth reviewing and "
    "are an evidence summary, not a proficiency score."
)


def _decision_points(function: ast.AST) -> int:
    """McCabe-style count for one function: 1 + branching constructs. A nested function's
    constructs count toward its enclosing function and toward itself."""
    points = 1
    for node in ast.walk(function):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
                             ast.Assert, ast.IfExp)):
            points += 1
        elif isinstance(node, ast.BoolOp):
            points += len(node.values) - 1
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            points += sum(1 + len(gen.ifs) for gen in node.generators)
        elif hasattr(ast, "match_case") and isinstance(node, ast.match_case):
            points += 1 + (1 if node.guard is not None else 0)
    return points


def _python_complexity_metrics(ingestion: dict[str, Any], verified: dict[str, Any]) -> dict[str, Any]:
    """Advisory function-level metrics over candidate-authored, non-excluded Python files."""
    records = []
    files = 0
    for repo, path, source, text in _eligible_sources(ingestion, verified):
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        files += 1
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                records.append({"function": node.name, "repo": repo["name"], "file_path": path,
                                "start_line": node.lineno, "end_line": node.end_lineno or node.lineno,
                                "span_lines": (node.end_lineno or node.lineno) - node.lineno + 1,
                                "decision_points": _decision_points(node)})
    points = sorted(record["decision_points"] for record in records)
    ranked = sorted(records, key=lambda r: (-r["decision_points"], -r["span_lines"]))
    return {"scope": "python", "files_analyzed": files, "functions_analyzed": len(records),
            "max_decision_points": points[-1] if points else None,
            "median_decision_points": statistics.median(points) if points else None,
            "top_functions": ranked[:3], "note": _CODE_METRICS_NOTE}


def compute_skill_depth(
    ingestion: dict[str, Any],
    verified: dict[str, Any],
    sql_result: dict[str, Any] | None = None,
    include_sql: bool = True,
) -> dict[str, dict[str, Any] | None]:
    """Compute the pilot profiles. A single analyzer failure never fails passport generation."""
    results: dict[str, dict[str, Any] | None] = {}
    for skill_id in (("sql",) if include_sql else ()) + tuple(PROFILES):
        try:
            results[skill_id] = (_sql_adapter(sql_result if sql_result is not None else safe_sql_depth(ingestion, verified), ingestion) if skill_id == "sql"
                                 else _generic_profile(skill_id, ingestion, verified))
        except Exception:
            logger.exception("Skill depth analyzer failed for %s", skill_id)
            results[skill_id] = None
    source_total = sum(
        (repo.get("coverage") or {}).get("source_files_total", len(repo.get("tree") or []))
        for repo in ingestion.get("repos", [])
    )
    source_read = sum(
        (repo.get("coverage") or {}).get("source_files_read", len(repo.get("source_files") or {}))
        for repo in ingestion.get("repos", [])
    )
    notes = []
    if source_read < source_total:
        notes.append(f"Analyzed {source_read} of {source_total} listed source files; repository sampling may omit relevant evidence.")
    if ingestion.get("repo_count", len(ingestion.get("repos", []))) >= 5:
        notes.append("The analysis is bounded to the five most recently pushed eligible public repositories.")
    if results.get("python") is not None:
        try:
            results["python"]["code_metrics"] = _python_complexity_metrics(ingestion, verified)
        except Exception:
            logger.exception("Python complexity metrics failed")
    for result in results.values():
        if result:
            result["coverage"]["source_files_read"] = source_read
            result["coverage"]["source_files_total"] = source_total
            result["coverage"]["notes"] = notes[:]
    return results


def student_skill_depth(results: dict[str, Any] | None) -> dict[str, Any] | None:
    """Remove recruiter-only advisory fields from citation records."""
    if results is None:
        return None
    clean = copy.deepcopy(results)
    for result in clean.values():
        if result:
            for competency in result.get("competencies", []):
                for citation in competency.get("evidence", []):
                    citation.pop("integrity", None)
                    citation.pop("copy_check", None)
    return clean
