"""Public API module smoke tests."""

from ai_dev_cli.config import DevCliConfig, load_config
from ai_dev_cli.lanes import LaneConfig, lane_from_path, lane_timeout_for_path, load_lanes
from ai_dev_cli.project import find_repo_root


def test_public_api_modules_export_documented_symbols() -> None:
    assert DevCliConfig.__name__ == "ProjectConfig"
    assert callable(load_config)
    assert LaneConfig.__name__ == "LaneConfig"
    assert callable(lane_from_path)
    assert callable(lane_timeout_for_path)
    assert callable(load_lanes)
    assert callable(find_repo_root)
