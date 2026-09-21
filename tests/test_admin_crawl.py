"""后台爬虫任务接口回归测试：启动/状态轮询/运行守卫/权限/CLI。

crawler.crawl 一律 monkeypatch 离线模拟（真实爬虫由集成测试覆盖），
绝不发起真实网络请求；任务状态逐测试重置（conftest 的 app 夹具）。
"""
import time

import app as app_module
from helpers import create_user, get_csrf_token, login

FAKE_RESULTS = {
    "results": [
        {"path": "/gaokao/shuxue/", "label": "数学", "status": "ok",
         "parsed": 5, "added": 2, "error": ""},
        {"path": "/gaokao/yuwen/", "label": "语文", "status": "robots_denied",
         "parsed": 0, "added": 0, "error": "robots 协议禁止抓取该频道"},
    ],
    "total_added": 2,
}


def _admin_client(client, app, username="craw_a1"):
    db = app.config["DATABASE"]
    create_user(db, username, "pw123456", is_admin=True)
    login(client, username, "pw123456")
    return db


def _start(client):
    token = get_csrf_token(client)
    return client.post("/admin/crawl", headers={"X-CSRFToken": token})


def _status(client):
    return client.get("/admin/crawl/status").get_json()


def _wait_done(client, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _status(client)
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError(f"爬虫任务在 {timeout}s 内未结束：{_status(client)}")


def test_crawl_start_and_finish(client, app, monkeypatch):
    _admin_client(client, app)
    monkeypatch.setattr(app_module.crawler, "crawl", lambda db: FAKE_RESULTS)
    resp = _start(client)
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "status": "running"}
    data = _wait_done(client)
    assert data["status"] == "done"
    assert data["total_added"] == 2
    assert data["results"] == FAKE_RESULTS["results"]
    assert data["started_at"] and data["finished_at"]


def test_crawl_running_guard(client, app, monkeypatch):
    """任务运行中重复触发：友好提示，不启动第二个任务。"""
    _admin_client(client, app)

    def slow_crawl(db):
        time.sleep(0.3)
        return FAKE_RESULTS

    monkeypatch.setattr(app_module.crawler, "crawl", slow_crawl)
    assert _start(client).get_json()["ok"] is True
    second = _start(client)
    assert second.status_code == 200
    assert second.get_json()["ok"] is False
    assert "正在运行中" in second.get_json()["error"]
    _wait_done(client)


def test_crawl_task_exception_status_error(client, app, monkeypatch):
    _admin_client(client, app)

    def boom(db):
        raise RuntimeError("模拟爬虫崩溃")

    monkeypatch.setattr(app_module.crawler, "crawl", boom)
    _start(client)
    data = _wait_done(client)
    assert data["status"] == "error"
    assert "RuntimeError" in data["error"]
    assert "模拟爬虫崩溃" in data["error"]


def test_crawl_status_idle_by_default(client, app):
    _admin_client(client, app)
    data = _status(client)
    assert data["status"] == "idle"
    assert data["results"] is None
    assert data["total_added"] == 0


def test_crawl_permissions_normal_user_403(client, app):
    create_user(app.config["DATABASE"], "craw_n1", "pw123456")
    login(client, "craw_n1", "pw123456")
    token = get_csrf_token(client)
    resp = client.post("/admin/crawl", headers={"X-CSRFToken": token})
    assert resp.status_code == 403
    assert client.get("/admin/crawl/status").status_code == 403


def test_crawl_anonymous_redirects(client):
    token = get_csrf_token(client)
    resp = client.post("/admin/crawl", headers={"X-CSRFToken": token})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert client.get("/admin/crawl/status").status_code == 302


def test_crawl_cli_command(client, app, monkeypatch):
    """flask crawl CLI：逐频道输出 + 总计（经 test_cli_runner 运行，
    直接调用 click 命令会触发 sys.exit 静默终止解释器）。"""
    _admin_client(client, app)  # 确保数据库已初始化
    monkeypatch.setattr(app_module.crawler, "crawl", lambda db: FAKE_RESULTS)
    result = app_module.app.test_cli_runner().invoke(args=["crawl"])
    assert result.exit_code == 0
    assert "数学" in result.output
    assert "robots_denied" in result.output
    assert "共新增待审核 2 条" in result.output
