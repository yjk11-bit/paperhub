"""CSRF 防护冒烟测试（Step-11 验收标准 1）：

- 不带 CSRF Token 访问全部 POST 接口一律 403 拒绝；
- 携带合法 Token（表单隐藏域 / X-CSRFToken 请求头）可正常调用；
- GET 接口不受影响。
测试数据全部动态生成，严禁写死主键。
"""
import io

import models
from helpers import (add_pending, create_user, get_csrf_token, login,
                     make_paper, unique_url)


# ---- 登录 / 注册 ----

def test_login_without_token_403(client, app):
    create_user(app.config["DATABASE"], "csrf_u1", "pw123456")
    resp = client.post("/login", data={"username": "csrf_u1", "password": "pw123456"})
    assert resp.status_code == 403


def test_login_with_token_ok(client, app):
    create_user(app.config["DATABASE"], "csrf_u2", "pw123456")
    token = get_csrf_token(client)
    resp = client.post("/login", data={
        "username": "csrf_u2", "password": "pw123456", "csrf_token": token,
    })
    assert resp.status_code == 302


def test_login_wrong_token_403(client):
    token = get_csrf_token(client)
    resp = client.post("/login", data={
        "username": "x", "password": "y", "csrf_token": token + "tampered",
    })
    assert resp.status_code == 403


def test_register_without_token_403(client):
    resp = client.post("/register", data={"username": "csrf_u3", "password": "pw123456"})
    assert resp.status_code == 403


def test_register_with_token_ok(client, app):
    token = get_csrf_token(client)
    resp = client.post("/register", data={
        "username": "csrf_u3", "password": "pw123456", "csrf_token": token,
    })
    assert resp.status_code == 302
    assert models.get_user_by_username(app.config["DATABASE"], "csrf_u3") is not None


# ---- 收藏 / 浏览历史（登录用户）----

def _login_user(client, app, username):
    db = app.config["DATABASE"]
    user_id = create_user(db, username, "pw123456")
    login(client, username, "pw123456")
    return db, user_id


def _history_id(db, user_id):
    rows, _, _ = models.query_view_history(db, user_id)
    return rows[0]["history_id"]


def test_collect_without_token_403(client, app):
    db, _ = _login_user(client, app, "csrf_c1")
    resp = client.post(f"/paper/{make_paper(db)}/collect")
    assert resp.status_code == 403


def test_collect_with_token_ok(client, app):
    db, user_id = _login_user(client, app, "csrf_c2")
    paper_id = make_paper(db)
    token = get_csrf_token(client)
    resp = client.post(f"/paper/{paper_id}/collect", data={"csrf_token": token})
    assert resp.status_code == 302
    assert models.is_collected(db, user_id, paper_id)


def test_history_delete_without_token_403(client, app):
    db, user_id = _login_user(client, app, "csrf_h1")
    client.get(f"/paper/{make_paper(db)}")  # 详情页自动记录浏览历史
    resp = client.post(f"/history/{_history_id(db, user_id)}/delete")
    assert resp.status_code == 403


def test_history_delete_with_token_ok(client, app):
    db, user_id = _login_user(client, app, "csrf_h2")
    client.get(f"/paper/{make_paper(db)}")
    token = get_csrf_token(client)
    resp = client.post(
        f"/history/{_history_id(db, user_id)}/delete", data={"csrf_token": token}
    )
    assert resp.status_code == 302
    rows, _, _ = models.query_view_history(db, user_id)
    assert len(rows) == 0


def test_history_clear_without_token_403(client, app):
    db, _ = _login_user(client, app, "csrf_h3")
    client.get(f"/paper/{make_paper(db)}")
    resp = client.post("/history/clear")
    assert resp.status_code == 403


def test_history_clear_with_token_ok(client, app):
    db, user_id = _login_user(client, app, "csrf_h4")
    client.get(f"/paper/{make_paper(db)}")
    token = get_csrf_token(client)
    resp = client.post("/history/clear", data={"csrf_token": token})
    assert resp.status_code == 302
    rows, _, _ = models.query_view_history(db, user_id)
    assert len(rows) == 0


# ---- 管理员 POST 接口：无 Token 一律 403 ----

def _admin_client(client, app, username="csrf_a1"):
    db = app.config["DATABASE"]
    create_user(db, username, "pw123456", is_admin=True)
    login(client, username, "pw123456")
    return db


def test_admin_post_endpoints_without_token_403(client, app):
    """审核通过/驳回、AI预审、CSV导入、手动录入、爬虫触发：无 Token 一律 403。"""
    db = _admin_client(client, app)
    pending_id = add_pending(db)
    approve_form = {
        "title": "2025届高三数学统考试题", "subject": "数学", "grade": "",
        "year": "2025", "difficulty": "2", "level": "名校联考",
        "has_answer": "0", "source_url": unique_url(),
    }
    cases = [
        ("post", f"/admin/pending/{pending_id}/approve", {"data": approve_form}),
        ("post", f"/admin/pending/{pending_id}/reject", {}),
        ("post", f"/admin/pending/{pending_id}/ai_suggest", {}),
        ("post", "/admin/pending/csv_import",
         {"data": {"file": (io.BytesIO(b"x"), "t.csv")}}),
        ("post", "/admin/pending/manual_add", {"json": approve_form}),
        ("post", "/admin/crawl", {}),
    ]
    for method, path, kwargs in cases:
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 403, f"{method} {path} 应返回 403"


def test_header_token_accepted(client, app):
    """JSON 通道（fetch 全局封装场景）：X-CSRFToken 请求头方式放行。"""
    db = _admin_client(client, app, "csrf_a2")
    token = get_csrf_token(client)
    payload = {
        "title": "2026届高三英语一模试卷", "subject": "英语", "year": "2026",
        "source_url": unique_url(),
    }
    resp = client.post(
        "/admin/pending/manual_add", json=payload,
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert models.get_pending_paper_by_id(db, data["id"]) is not None


def test_get_endpoints_need_no_token(client, app):
    """GET 接口不校验 CSRF：登录页/注册页/题库/审核面板/爬虫状态均正常。"""
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200
    _admin_client(client, app, "csrf_g1")
    assert client.get("/").status_code == 200
    assert client.get("/admin/pending").status_code == 200
    assert client.get("/admin/crawl/status").status_code == 200
