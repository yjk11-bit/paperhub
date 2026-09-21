"""认证流程回归测试（验收标准 4：原有业务功能不受 CSRF 改造影响）。"""
import models
from helpers import create_user, get_csrf_token, login


def test_register_login_logout_flow(client, app):
    db = app.config["DATABASE"]
    token = get_csrf_token(client)
    resp = client.post("/register", data={
        "username": "auth_u1", "password": "pw123456", "csrf_token": token,
    })
    assert resp.status_code == 302
    user = models.get_user_by_username(db, "auth_u1")
    assert user is not None
    # 密码必须哈希存储，禁止明文
    assert user["password_hash"] != "pw123456"

    resp = login(client, "auth_u1", "pw123456")
    assert resp.status_code == 200  # 登录后跟随重定向到题库
    with client.session_transaction() as sess:
        assert sess["user_id"] == user["id"]

    resp = client.get("/logout")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_register_duplicate_username(client, app):
    create_user(app.config["DATABASE"], "auth_u2", "pw123456")
    token = get_csrf_token(client)
    resp = client.post("/register", data={
        "username": "auth_u2", "password": "pw123456", "csrf_token": token,
    })
    assert resp.status_code == 200
    assert "用户名已存在".encode("utf-8") in resp.data


def test_register_empty_fields(client):
    token = get_csrf_token(client)
    resp = client.post("/register", data={"csrf_token": token})
    assert resp.status_code == 200
    assert "用户名和密码不能为空".encode("utf-8") in resp.data


def test_login_wrong_password(client, app):
    create_user(app.config["DATABASE"], "auth_u3", "pw123456")
    resp = login(client, "auth_u3", "wrong-pass")
    assert "用户名不存在或密码错误".encode("utf-8") in resp.data


def test_logged_in_user_visits_login_redirects(client, app):
    create_user(app.config["DATABASE"], "auth_u4", "pw123456")
    login(client, "auth_u4", "pw123456")
    resp = client.get("/login")
    assert resp.status_code == 302
