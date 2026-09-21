"""测试公共辅助函数：全部动态生成数据，严禁写死主键。"""
import itertools

import models

_url_counter = itertools.count(1)


def unique_url(prefix="https://example.com/show/"):
    """生成全局唯一的来源链接（去重校验场景用）。"""
    return f"{prefix}{next(_url_counter)}"


def create_user(db, username, password, is_admin=False):
    """动态创建测试用户，返回用户 id（is_admin=True 时提权）。"""
    user_id = models.create_user(db, username, password)
    if is_admin:
        conn = models.get_db(db)
        try:
            conn.execute("UPDATE user SET is_admin = 1 WHERE id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
    return user_id


def get_csrf_token(client):
    """任意页面 GET 一次（会话惰性生成 token），再从会话中读出。

    已登录时 /login 会 302 跳转题库，但 token 已由 before_request 生成，
    不影响读取。
    """
    client.get("/login")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def login(client, username, password):
    """携带 CSRF token 登录，返回响应（跟随重定向）。"""
    token = get_csrf_token(client)
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=True,
    )


def add_pending(db, title="2025届高三数学统考试题", subject="数学", year=2025,
                source_url=None, source_school=None, page_summary="",
                grade="", difficulty=None, level="", has_answer=0):
    """写入一条待审核记录（source_url 缺省时自动唯一），返回记录 id。"""
    return models.add_pending_paper(
        db, title, subject, year, source_url or unique_url(),
        source_school, page_summary=page_summary, grade=grade,
        difficulty=difficulty, level=level, has_answer=has_answer,
    )


def make_paper(db, title="2025届高三数学统考试题"):
    """经审核流程动态创建一张已入库试卷，返回 paper id（不写死主键）。"""
    source_url = unique_url("https://example.com/paper/")
    pending_id = models.add_pending_paper(db, title, "数学", 2025, source_url)
    return models.approve_pending_paper(
        db, pending_id, title, "数学", 2025, 2, "名校联考", 1, source_url
    )
