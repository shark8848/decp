"""decp_core 包导出。"""
from decp_core.config import Settings, settings
from decp_core.storage import create_storage

__version__ = "0.1.0"

__all__ = ["Settings", "settings", "create_storage", "__version__"]
