"""Import scanner and root discovery tests."""

from pathlib import Path

from ai_dev_cli import _imports, _project
from tests.unit._helpers import write_file, write_pyproject


def test_find_repo_root_walks_up_to_pyproject(tmp_path: Path) -> None:
    write_pyproject(tmp_path, '[project]\nname = "sample"\n')
    nested = tmp_path / "src" / "pkg" / "deep"
    nested.mkdir(parents=True)

    assert _project.find_repo_root(nested) == tmp_path


def test_scan_test_imports_walks_ast_for_src_package_imports(tmp_path: Path) -> None:
    write_file(tmp_path, "src/pkg/__init__.py", "")
    write_file(tmp_path, "src/pkg/feature.py", "VALUE = 1\n")
    write_file(
        tmp_path,
        "tests/unit/test_feature.py",
        "from pkg.feature import VALUE\n\n\ndef test_value() -> None:\n    assert VALUE == 1\n",
    )
    write_file(
        tmp_path,
        "tests/integration/test_pkg.py",
        "import pkg\n\n\ndef test_pkg() -> None:\n    assert pkg is not None\n",
    )

    mapping = _imports.scan_test_imports(tmp_path, ["tests"], ["src/pkg"])

    assert mapping == {
        "src/pkg": ["tests/integration"],
        "src/pkg/feature": ["tests/unit"],
    }


def test_scan_test_imports_skips_malformed_python_and_continues(tmp_path: Path) -> None:
    write_file(tmp_path, "src/pkg/__init__.py", "")
    write_file(tmp_path, "src/pkg/feature.py", "VALUE = 1\n")
    write_file(tmp_path, "tests/unit/test_broken.py", "def test_broken(:\n")
    write_file(
        tmp_path,
        "tests/unit/test_feature.py",
        "from pkg.feature import VALUE\n\n\ndef test_value() -> None:\n    assert VALUE == 1\n",
    )

    mapping = _imports.scan_test_imports(tmp_path, ["tests"], ["src/pkg"])

    assert mapping == {"src/pkg/feature": ["tests/unit"]}
