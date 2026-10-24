"""Layer rules from CLAUDE.md and PLAN.md §6, checked by reading every module's imports."""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "marketdata"

PURE_PACKAGES = (
    "marketdata.extract",
    "marketdata.verify",
    "marketdata.normalize",
    "marketdata.content",
)
# Anything that touches the network, the database, files or settings.
IO_MODULES = (
    "marketdata.api",
    "marketdata.config",
    "marketdata.db",
    "marketdata.ingest",
    "marketdata.llm",
    "marketdata.mcp_server",
    "marketdata.prices",
    "marketdata.sec",
    "marketdata.service",
    "marketdata.tasks",
    "marketdata.web",
    "marketdata.webhooks",
    "alembic",
    "anthropic",
    "boto3",
    "botocore",
    "httpx",
    "psycopg",
    "psycopg_pool",
    "requests",
    "socket",
    "sqlalchemy",
    "urllib.request",
)
INTERFACE_PACKAGES = ("marketdata.api", "marketdata.mcp_server", "marketdata.web")
SQL_ALLOWED = ("marketdata.db.repositories", "marketdata.db.migrations")
# SEC hosts are named only inside sec/, so every SEC URL comes from sec/urls.py and every
# SEC request from sec/client.py.
SEC_HOST_ALLOWED = ("marketdata.sec",)
SEC_HOST_PATTERN = re.compile(r"\bsec\.gov\b", re.IGNORECASE)
SQL_PATTERN = re.compile(
    r"^\s*(select\s.+\sfrom\s|insert\s+into\s|update\s+\w+\s+set\s|delete\s+from\s"
    r"|create\s+(table|index)\s|alter\s+table\s|drop\s+(table|index)\s|truncate\s)",
    re.IGNORECASE | re.DOTALL,
)


def _module_name(file: Path) -> str:
    parts = file.relative_to(ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _modules() -> dict[str, ast.Module]:
    return {
        _module_name(file): ast.parse(file.read_text(), filename=str(file))
        for file in sorted(PACKAGE.rglob("*.py"))
    }


def _imports(module: str, tree: ast.Module, is_package: bool) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")
                if not is_package:
                    base = base[:-1]
                base = base[: len(base) - (node.level - 1)]
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            yield target
            yield from (f"{target}.{alias.name}" for alias in node.names)


def _within(name: str, packages: tuple[str, ...]) -> bool:
    return any(name == package or name.startswith(package + ".") for package in packages)


def _violations(sources: tuple[str, ...], banned: tuple[str, ...]) -> list[str]:
    found = []
    for module, tree in _modules().items():
        if not _within(module, sources):
            continue
        is_package = (PACKAGE.parent / module.replace(".", "/") / "__init__.py").exists()
        for imported in _imports(module, tree, is_package):
            if _within(imported, banned):
                found.append(f"{module} imports {imported}")
    return sorted(set(found))


def _docstring_nodes(tree: ast.Module) -> set[int]:
    nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                nodes.add(id(body[0].value))
    return nodes


def test_the_scan_sees_the_package() -> None:
    modules = _modules()
    assert "marketdata.verify.number_check" in modules
    assert "marketdata.db.repositories" in modules


def test_verify_never_imports_extract() -> None:
    assert _violations(("marketdata.verify",), ("marketdata.extract",)) == []


def test_pure_modules_do_no_io() -> None:
    assert _violations(PURE_PACKAGES, IO_MODULES) == []


def test_interfaces_go_through_the_service_layer() -> None:
    assert _violations(INTERFACE_PACKAGES, ("marketdata.db.repositories",)) == []


def test_sec_package_is_transport_and_storage_only() -> None:
    """sec/ fetches and stores files; recording them in the database is ingest/'s job."""
    banned = tuple(
        f"marketdata.{name}"
        for name in ("api", "db", "ingest", "mcp_server", "service", "tasks", "web", "webhooks")
    )
    assert _violations(("marketdata.sec",), banned) == []


def test_sql_lives_only_in_repositories() -> None:
    found = []
    for module, tree in _modules().items():
        if _within(module, SQL_ALLOWED):
            continue
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and SQL_PATTERN.match(node.value)
            ):
                found.append(f"{module}:{node.lineno}")
    assert found == []


def test_sec_hosts_are_named_only_in_the_sec_package() -> None:
    found = []
    for module, tree in _modules().items():
        if _within(module, SEC_HOST_ALLOWED):
            continue
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and SEC_HOST_PATTERN.search(node.value)
            ):
                found.append(f"{module}:{node.lineno}")
    assert found == []
