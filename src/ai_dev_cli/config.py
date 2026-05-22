"""Public configuration API."""

from ai_dev_cli._project import ProjectConfig as DevCliConfig
from ai_dev_cli._project import load_config

__all__ = ("DevCliConfig", "load_config")
