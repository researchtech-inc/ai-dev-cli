"""Automatic source-to-test dependency detection via AST import scanning."""

import ast
from collections import defaultdict
from pathlib import Path


def scan_test_imports(repo_root: Path, test_roots: list[str], source_packages: list[str]) -> dict[str, list[str]]:
    """Build source module to test directory mappings from imports."""
    pkg_prefixes = {
        package.replace("/", ".").split(".")[-1] if "/" in package else package for package in source_packages
    }
    source_to_tests: dict[str, set[str]] = defaultdict(set)

    for test_root in test_roots:
        test_path = repo_root / test_root
        if not test_path.is_dir():
            continue

        for py_file in sorted(test_path.rglob("*.py")):
            try:
                tree = ast.parse(py_file.read_bytes())
            except (
                OSError,
                SyntaxError,
            ):
                continue

            rel = py_file.relative_to(repo_root)
            test_dir = "/".join(rel.parts[:2]) if len(rel.parts) > 2 else rel.parent.as_posix()

            for node in ast.walk(tree):
                for module in _extract_import_modules(node):
                    source_key = _match_source_module(module, source_packages, pkg_prefixes)
                    if source_key is not None:
                        source_to_tests[source_key].add(test_dir)

    return {key: sorted(value) for key, value in sorted(source_to_tests.items())}


def _extract_import_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return []


def _match_source_module(module_name: str, source_packages: list[str], pkg_prefixes: set[str]) -> str | None:
    parts = module_name.split(".")
    if not parts or parts[0] not in pkg_prefixes:
        return None

    for package in source_packages:
        package_dotted = package.replace("/", ".")
        import_root = package_dotted.split(".")[-1] if "/" in package else package_dotted
        if module_name != import_root and not module_name.startswith(f"{import_root}."):
            continue

        remaining = module_name[len(import_root) :]
        if not remaining:
            return package

        if remaining.startswith("."):
            sub_parts = remaining.split(".")
            if len(sub_parts) >= 2:
                return f"{package}/{sub_parts[1]}"

        return package

    return None
