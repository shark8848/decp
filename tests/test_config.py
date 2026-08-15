"""配置解析测试：data_dir 驱动的数据路径默认值（pip 安装 / 容器部署形态）。"""
import pytest

from decp_core.config import Settings, PROJECT_ROOT


class TestDataDirResolution:
    """回归：sqlite_path / reports_dir 默认值必须基于 data_dir，而非仅 PROJECT_ROOT。

    历史 bug：默认值写死 PROJECT_ROOT/data，pip 安装后 PROJECT_ROOT 推断为
    site-packages 路径，容器（DECP_DATA_DIR=/app/data）部署时路径错误。
    """

    def test_defaults_without_data_dir(self):
        """未显式设置任何路径时，回落项目根 data/（源码开发形态）。"""
        s = Settings(storage_backend="sqlite")
        assert s.sqlite_path == str(PROJECT_ROOT / "data" / "decp.db")
        assert s.reports_dir == str(PROJECT_ROOT / "data" / "reports")

    def test_data_dir_drives_defaults(self):
        """设置 data_dir 后，sqlite/reports 默认值随之解析到其下。"""
        s = Settings(storage_backend="sqlite", data_dir="/app/data")
        assert s.sqlite_path == "/app/data/decp.db"
        assert s.reports_dir == "/app/data/reports"

    def test_explicit_paths_override_data_dir(self):
        """显式 sqlite_path / reports_dir 始终最高优先。"""
        s = Settings(
            storage_backend="sqlite",
            data_dir="/app/data",
            sqlite_path="/custom/db.sqlite",
            reports_dir="/custom/reports",
        )
        assert s.sqlite_path == "/custom/db.sqlite"
        assert s.reports_dir == "/custom/reports"

    def test_env_prefix_maps_data_dir(self):
        """环境变量 DECP_DATA_DIR（前缀 DECP_）能映射到 data_dir 字段。"""
        s = Settings(_env_file=None, data_dir="/mnt/data")
        assert s.data_dir == "/mnt/data"
