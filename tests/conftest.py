"""pytest 公共夹具：临时数据库 + 测试客户端。

全部测试共用 app.py 的模块级 Flask 应用实例，但每个测试使用独立的
临时数据库文件（先经环境变量让导入期的初始化落在临时路径，夹具再逐测试
换库），互不干扰、绝不触碰真实 instance/paperhub.db；后台爬虫任务状态
逐测试重置。严禁写死主键 id，测试数据全部动态生成。
"""
import os
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 必须在导入 app 之前设置：app.py 在导入时即按配置初始化数据库。
# 先指向导入期专用临时文件，避免测试进程触碰真实库。
_TMP_DB_DIR = tempfile.mkdtemp(prefix="paperhub_pytest_")
os.environ["PAPERHUB_DATABASE"] = os.path.join(_TMP_DB_DIR, "import_time.db")

import app as app_module  # noqa: E402
import models  # noqa: E402
from app import app as flask_app  # noqa: E402

flask_app.config["TESTING"] = True


@pytest.fixture()
def app(tmp_path):
    """每个测试：独立的临时数据库 + 干净的爬虫任务状态。"""
    db_path = str(tmp_path / "paperhub_test.db")
    flask_app.config["DATABASE"] = db_path
    models.init_db(db_path)
    with app_module._crawl_task_lock:
        app_module._crawl_task.update(
            status="idle", started_at=None, finished_at=None,
            results=None, total_added=0, error="",
        )
    yield flask_app


@pytest.fixture()
def client(app):
    """Flask 测试客户端（会话 Cookie 随请求自动维护）。"""
    return app.test_client()
