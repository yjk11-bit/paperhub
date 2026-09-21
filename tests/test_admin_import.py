"""CSV 批量导入 / 手动录入接口回归测试（含 CSRF token 正常放行场景）。"""
import io

import models
from helpers import create_user, get_csrf_token, login, unique_url

CSV_HEADER = "标题,科目,年级,年份,难度,等级,是否带解析,来源链接,来源学校\n"


def _admin_client(client, app, username="imp_a1"):
    db = app.config["DATABASE"]
    create_user(db, username, "pw123456", is_admin=True)
    login(client, username, "pw123456")
    return db


def _csv_post(client, content_bytes, filename="papers.csv"):
    token = get_csrf_token(client)
    return client.post(
        "/admin/pending/csv_import",
        data={
            "file": (io.BytesIO(content_bytes), filename),
            "csrf_token": token,
        },
        content_type="multipart/form-data",
    )


def _pending_count(db):
    conn = models.get_db(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM pending_paper").fetchone()[0]
    finally:
        conn.close()


# ---- CSV 导入 ----

def test_csv_import_happy_path(client, app):
    db = _admin_client(client, app)
    text = CSV_HEADER + (
        "2025届江苏高三数学一模试题,数学,高三,2025,中档,名校联考,是,"
        "https://example.com/csv/1,江苏\n"
        "2025届江苏高三语文一模试题,语文,高三,2025,,,否,"
        "https://example.com/csv/2,江苏\n"
    )
    resp = _csv_post(client, text.encode("utf-8"))
    data = resp.get_json()
    assert data["ok"] is True
    assert data["imported"] == 2
    assert data["errors"] == []
    assert _pending_count(db) == 2


def test_csv_import_gbk_encoding(client, app):
    db = _admin_client(client, app)
    text = CSV_HEADER + "2025届高三英语试卷,英语,,2025,,,否,https://example.com/csv/g1,\n"
    resp = _csv_post(client, text.encode("gbk"))
    data = resp.get_json()
    assert data["ok"] is True
    assert data["imported"] == 1
    assert _pending_count(db) == 1


def test_csv_import_partial_success_with_errors(client, app):
    """合法行全部入库；错误行返回行号+原因（部分成功策略）。"""
    db = _admin_client(client, app)
    text = CSV_HEADER + (
        "2025届高三数学试卷,数学,,2025,,,,https://example.com/csv/ok1,\n"
        ",数学,,2025,,,否,https://example.com/csv/err1,\n"
        "2025届高三数学试卷,数学,,1800,,,,https://example.com/csv/err2,\n"
        "2025届高三数学试卷,数学,,2025,,,,https://example.com/x.pdf,\n"
    )
    resp = _csv_post(client, text.encode("utf-8"))
    data = resp.get_json()
    assert data["ok"] is True
    assert data["imported"] == 1
    assert [e["row"] for e in data["errors"]] == [3, 4, 5]
    reasons = "；".join(e["reason"] for e in data["errors"])
    assert "标题不能为空" in reasons
    assert "年份需在 2000 至 2100 之间" in reasons
    assert "禁止 PDF 文件链接" in reasons
    assert _pending_count(db) == 1


def test_csv_import_duplicate_source_url(client, app):
    db = _admin_client(client, app)
    models.add_pending_paper(db, "已存在的试卷", "数学", 2025, "https://example.com/dup/1")
    text = CSV_HEADER + "2025届高三数学试卷,数学,,2025,,,,https://example.com/dup/1,\n"
    resp = _csv_post(client, text.encode("utf-8"))
    data = resp.get_json()
    assert data["ok"] is True
    assert data["imported"] == 0
    assert data["errors"][0]["reason"] == "来源链接已存在（待审核或已入库）"


def test_csv_import_missing_file(client, app):
    _admin_client(client, app)
    token = get_csrf_token(client)
    resp = client.post("/admin/pending/csv_import", data={"csrf_token": token})
    data = resp.get_json()
    assert data["ok"] is False
    assert "请选择要上传的 CSV 文件" in data["error"]


def test_csv_import_empty_file(client, app):
    _admin_client(client, app)
    resp = _csv_post(client, b"")
    data = resp.get_json()
    assert data["ok"] is False
    assert "CSV 文件为空" in data["error"]


def test_csv_import_undecodable_encoding(client, app):
    _admin_client(client, app)
    resp = _csv_post(client, b"\xff\xfe\x00\x01\x02")
    data = resp.get_json()
    assert data["ok"] is False
    assert "编码无法识别" in data["error"]


def test_csv_import_missing_column(client, app):
    _admin_client(client, app)
    text = "标题,科目\n2025届高三数学试卷,数学\n"
    resp = _csv_post(client, text.encode("utf-8"))
    data = resp.get_json()
    assert data["ok"] is False
    assert "缺少列" in data["error"]


def test_csv_import_too_many_rows(client, app):
    _admin_client(client, app)
    lines = [CSV_HEADER] + [
        f"2025届高三数学试卷,数学,,2025,,,,https://example.com/csv/m{i},\n"
        for i in range(501)
    ]
    resp = _csv_post(client, "".join(lines).encode("utf-8"))
    data = resp.get_json()
    assert data["ok"] is False
    assert "最多 500 行" in data["error"]


def test_csv_template_download(client, app):
    _admin_client(client, app)
    resp = client.get("/admin/pending/csv_template")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["Content-Type"]
    assert "标题".encode("utf-8") in resp.data


# ---- 手动录入 ----

def test_manual_add_form_channel(client, app):
    db = _admin_client(client, app)
    token = get_csrf_token(client)
    resp = client.post(
        "/admin/pending/manual_add",
        data={
            "csrf_token": token,
            "title": "2026届高三化学试卷", "subject": "化学", "grade": "高三",
            "year": "2026", "difficulty": "1", "level": "地市统考",
            "has_answer": "1", "source_url": unique_url(), "source_school": "湖北",
        },
    )
    data = resp.get_json()
    assert data["ok"] is True
    pending = models.get_pending_paper_by_id(db, data["id"])
    assert pending is not None
    assert pending["grade"] == "高三"
    assert pending["difficulty"] == 1
    assert pending["has_answer"] == 1


def test_manual_add_json_channel(client, app):
    db = _admin_client(client, app)
    token = get_csrf_token(client)
    resp = client.post(
        "/admin/pending/manual_add",
        json={
            "title": "2026届高三生物试卷", "subject": "生物", "year": "2026",
            "source_url": unique_url(),
        },
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert models.get_pending_paper_by_id(db, data["id"]) is not None


def test_manual_add_rejects_pdf(client, app):
    _admin_client(client, app)
    token = get_csrf_token(client)
    resp = client.post(
        "/admin/pending/manual_add",
        json={"title": "2025届高三数学试卷", "subject": "数学", "year": "2025",
              "source_url": "https://example.com/a.pdf"},
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert "禁止 PDF 文件链接" in data["error"]


def test_manual_add_invalid_subject(client, app):
    _admin_client(client, app)
    token = get_csrf_token(client)
    resp = client.post(
        "/admin/pending/manual_add",
        json={"title": "2025届高三美术试卷", "subject": "美术", "year": "2025",
              "source_url": unique_url()},
        headers={"X-CSRFToken": token},
    )
    assert resp.get_json()["ok"] is False


def test_manual_add_duplicate_url(client, app):
    db = _admin_client(client, app)
    url = unique_url()
    models.add_pending_paper(db, "已存在", "数学", 2025, url)
    token = get_csrf_token(client)
    resp = client.post(
        "/admin/pending/manual_add",
        json={"title": "重复", "subject": "数学", "year": "2025", "source_url": url},
        headers={"X-CSRFToken": token},
    )
    data = resp.get_json()
    assert data["ok"] is False
    assert "来源链接已存在" in data["error"]


def test_manual_add_normal_user_403(client, app):
    db = app.config["DATABASE"]
    create_user(db, "imp_n1", "pw123456")
    login(client, "imp_n1", "pw123456")
    token = get_csrf_token(client)
    resp = client.post(
        "/admin/pending/manual_add",
        json={"title": "x", "subject": "数学", "year": "2025",
              "source_url": unique_url()},
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 403
