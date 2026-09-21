"""待审核面板核心接口回归测试：审核通过/驳回/AI 预审/权限控制。

AI 预审全部通过 monkeypatch 离线模拟 DeepSeek，绝不发起真实网络请求；
测试数据全部动态生成，严禁写死主键。
"""
import pytest

import ai_review
import models
from helpers import (add_pending, create_user, get_csrf_token, login,
                     unique_url)

APPROVE_FORM_BASE = {
    "subject": "数学", "grade": "", "year": "2025", "difficulty": "2",
    "level": "名校联考", "has_answer": "0",
}


def _admin_client(client, app, username="adm_p1"):
    db = app.config["DATABASE"]
    create_user(db, username, "pw123456", is_admin=True)
    login(client, username, "pw123456")
    return db


def _approve_data(**overrides):
    data = dict(APPROVE_FORM_BASE, title="2025届高三数学统考试题",
                source_url=unique_url())
    data.update(overrides)
    return data


# ---- 权限 ----

def test_panel_access_admin_ok(client, app):
    _admin_client(client, app)
    assert client.get("/admin/pending").status_code == 200


def test_panel_access_normal_user_403(client, app):
    create_user(app.config["DATABASE"], "adm_n1", "pw123456")
    login(client, "adm_n1", "pw123456")
    assert client.get("/admin/pending").status_code == 403


def test_panel_access_anonymous_redirects(client):
    resp = client.get("/admin/pending")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_normal_user_cannot_approve(client, app):
    db = app.config["DATABASE"]
    create_user(db, "adm_n2", "pw123456")
    login(client, "adm_n2", "pw123456")
    pending_id = add_pending(db)
    token = get_csrf_token(client)
    resp = client.post(
        f"/admin/pending/{pending_id}/approve",
        data={**_approve_data(title="标题", source_url=unique_url()), "csrf_token": token},
    )
    assert resp.status_code == 403


# ---- 审核通过 ----

def test_approve_happy_path(client, app):
    db = _admin_client(client, app)
    title = "2025届浙江高三数学第一次联考试题及答案"
    source_url = unique_url()
    pending_id = models.add_pending_paper(
        db, title, "数学", 2025, source_url, source_school="浙江",
        grade="高三", difficulty=3, level="名校联考", has_answer=1,
    )
    token = get_csrf_token(client)
    resp = client.post(
        f"/admin/pending/{pending_id}/approve",
        data={**_approve_data(title=title, source_url=source_url, subject="数学",
                              grade="高三", difficulty="3", has_answer="1"),
              "csrf_token": token},
    )
    assert resp.status_code == 302
    # 待审核删除、试卷入库，字段完整转移
    assert models.get_pending_paper_by_id(db, pending_id) is None
    papers, total, _ = models.query_papers(db)
    assert total == 1
    paper = papers[0]
    assert paper["title"] == title
    assert paper["grade"] == "高三"
    assert paper["difficulty"] == 3
    assert paper["level"] == "名校联考"
    assert paper["has_answer"] == 1
    assert paper["source_school"] == "浙江"


@pytest.mark.parametrize("overrides, fragment", [
    ({"year": "1999"}, "年份需在 2000 至 2100 之间"),
    ({"year": "abc"}, "标题、来源链接、年份、难度均不能为空"),
    ({"subject": "美术"}, "学科无效"),
    ({"difficulty": "9"}, "难度无效"),
    ({"level": "野鸡卷"}, "试卷等级无效"),
    ({"grade": "大四"}, "年级无效"),
    ({"title": ""}, "标题、来源链接、年份、难度均不能为空"),
    ({"source_url": ""}, "标题、来源链接、年份、难度均不能为空"),
])
def test_approve_validation_errors(client, app, overrides, fragment):
    """非法字段审核：页面返回错误提示，待审核保留、不建试卷。"""
    db = _admin_client(client, app)
    pending_id = add_pending(db)
    token = get_csrf_token(client)
    resp = client.post(
        f"/admin/pending/{pending_id}/approve",
        data={**_approve_data(**overrides), "csrf_token": token},
    )
    assert resp.status_code == 200
    assert fragment.encode("utf-8") in resp.data
    assert models.get_pending_paper_by_id(db, pending_id) is not None
    _, total, _ = models.query_papers(db)
    assert total == 0


def test_approve_missing_404(client, app):
    _admin_client(client, app)
    token = get_csrf_token(client)
    resp = client.post(
        "/admin/pending/999999/approve",
        data={**_approve_data(title="标题", source_url=unique_url()), "csrf_token": token},
    )
    assert resp.status_code == 404


# ---- 驳回 ----

def test_reject_happy_path(client, app):
    db = _admin_client(client, app)
    pending_id = add_pending(db)
    token = get_csrf_token(client)
    resp = client.post(
        f"/admin/pending/{pending_id}/reject", data={"csrf_token": token}
    )
    assert resp.status_code == 302
    assert models.get_pending_paper_by_id(db, pending_id) is None


def test_reject_missing_404(client, app):
    _admin_client(client, app)
    token = get_csrf_token(client)
    resp = client.post("/admin/pending/999999/reject", data={"csrf_token": token})
    assert resp.status_code == 404


# ---- AI 预审 ----

def _ai_post(client, pending_id, token):
    return client.post(
        f"/admin/pending/{pending_id}/ai_suggest",
        headers={"X-CSRFToken": token},
    )


def test_ai_suggest_without_key_friendly(client, app):
    app.config["DEEPSEEK_API_KEY"] = ""
    db = _admin_client(client, app)
    pending_id = add_pending(db)
    token = get_csrf_token(client)
    resp = _ai_post(client, pending_id, token)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "未配置 DeepSeek API Key" in data["error"]


def test_ai_suggest_success_prefills(client, app, monkeypatch):
    app.config["DEEPSEEK_API_KEY"] = "test-key"
    db = _admin_client(client, app)
    pending_id = models.add_pending_paper(
        db, "2025届广东高三物理一模试题及答案", "物理", 2025, unique_url(),
        page_summary="含答案解析",
    )

    def fake_suggest(title, page_summary, api_key):
        assert api_key == "test-key"
        assert title == "2025届广东高三物理一模试题及答案"
        assert page_summary == "含答案解析"
        return {
            "subject": "物理", "grade": "高三", "exam_year": 2025,
            "difficulty": 3, "level_type": "名校联考", "has_analysis": True,
        }

    monkeypatch.setattr(ai_review, "suggest_paper_fields", fake_suggest)
    token = get_csrf_token(client)
    resp = _ai_post(client, pending_id, token)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["subject"] == "物理"
    assert data["exam_year"] == 2025
    assert data["has_analysis"] is True
    # 仅预填不自动入库：待审核仍在、paper 表无新记录
    assert models.get_pending_paper_by_id(db, pending_id) is not None
    _, total, _ = models.query_papers(db)
    assert total == 0


def test_ai_suggest_failure_friendly(client, app, monkeypatch):
    app.config["DEEPSEEK_API_KEY"] = "test-key"
    db = _admin_client(client, app)
    pending_id = add_pending(db)

    def fake_raise(title, page_summary, api_key):
        raise ai_review.AIReviewError("模拟失败")

    monkeypatch.setattr(ai_review, "suggest_paper_fields", fake_raise)
    token = get_csrf_token(client)
    resp = _ai_post(client, pending_id, token)
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"] == "AI预审失败，请手动填写"


def test_ai_suggest_missing_404(client, app):
    app.config["DEEPSEEK_API_KEY"] = "test-key"
    _admin_client(client, app)
    token = get_csrf_token(client)
    resp = _ai_post(client, 999999, token)
    assert resp.status_code == 404
