"""爬虫解析/合规单元回归测试（离线 mock，绝不发起真实网络请求）。

覆盖：三重过滤（试题/试卷关键词、通用标签、描述性链接）、PDF 双形态拒绝、
robots 协议、逐频道容错、限速、source_url 去重。
"""
import requests

import models
from crawler import spider

PAGE_TEMPLATE = """<html><body>
{links}
</body></html>"""


def _page(*links):
    return PAGE_TEMPLATE.format(links="\n".join(links))


def test_parse_basic_fields():
    html = _page(
        '<div><a href="/show/123.html">'
        '2026年贵州普通高中学业水平选择性考试化学试题及答案</a></div>'
    )
    items = spider.parse_papers(html)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "2026年贵州普通高中学业水平选择性考试化学试题及答案"
    assert item["subject"] == "化学"
    assert item["year"] == 2026
    assert item["source_school"] == "贵州"
    assert item["source_url"] == "https://www.51test.net/show/123.html"


def test_parse_skips_generic_labels():
    html = _page(
        '<a href="/show/1.html">试题</a>',
        '<a href="/show/2.html">答案</a>',
        '<a href="/show/3.html">下载</a>',
        '<a href="/show/4.html">2025届高三数学统考试题及答案</a>',
    )
    items = spider.parse_papers(html)
    assert [i["title"] for i in items] == ["2025届高三数学统考试题及答案"]


def test_parse_skips_descriptive_links():
    html = _page(
        '<a href="/show/1.html">无忧考网整理发布2025届高三数学试题，'
        '欢迎浏览，仅供参考，来源：无忧考网</a>',
        '<a href="/show/2.html">2025届高三数学统考试题及答案</a>',
    )
    items = spider.parse_papers(html)
    assert len(items) == 1


def test_parse_skips_non_paper_titles():
    """资讯类标题（不含试题/试卷关键词）不采集。"""
    html = _page(
        '<a href="/show/1.html">2026年高考分数线公布</a>',
        '<a href="/show/2.html">2026年高考报名时间安排</a>',
    )
    assert spider.parse_papers(html) == []


def test_parse_skips_pdf_links():
    """PDF 双形态（.pdf 后缀 / .pdf? 查询串）一律跳过（铁律）。"""
    html = _page(
        '<a href="/show/9.pdf">2025届高三数学试卷</a>',
        '<a href="/show/10.pdf?download=1">2025届高三数学试卷</a>',
        '<a href="/show/11.html">2025届高三数学试卷</a>',
    )
    items = spider.parse_papers(html)
    assert [i["source_url"] for i in items] == ["https://www.51test.net/show/11.html"]


def test_fetch_html_refuses_pdf():
    for url in ("https://x.com/a.pdf", "https://x.com/a.pdf?dl=1"):
        try:
            spider.fetch_html(url)
        except ValueError as exc:
            assert "PDF" in str(exc)
        else:
            raise AssertionError(f"应拒绝 PDF 链接：{url}")


def test_fetch_html_ok(monkeypatch):
    class FakeResp:
        encoding = "utf-8"
        text = "<html>ok</html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(spider.requests, "get", lambda url, **kw: FakeResp())
    assert spider.fetch_html("https://x.com/show/1.html") == "<html>ok</html>"


def test_robots_allowed(monkeypatch):
    class FakeResp:
        text = "User-agent: *\nAllow: /\nDisallow: /tag/\n"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(spider.requests, "get", lambda url, **kw: FakeResp())
    assert spider.robots_allowed("/gaokao/x/") is True
    assert spider.robots_allowed("/tag/x/") is False


def test_robots_unreachable_allowed(monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(spider.requests, "get", boom)
    # robots.txt 无法访问时按允许处理（依赖限速），不中断爬取
    assert spider.robots_allowed("/gaokao/x/") is True


def test_crawl_channel_failure_isolation(app, monkeypatch):
    """单频道请求异常：返回 failed 结果、不上抛，不中断整体。"""
    monkeypatch.setattr(spider, "robots_allowed", lambda path: True)

    def boom(url):
        raise requests.ConnectionError("连接失败")

    monkeypatch.setattr(spider, "fetch_html", boom)
    result = spider.crawl_channel(app.config["DATABASE"], "/gaokao/x/", "X频道")
    assert result["status"] == "failed"
    assert "ConnectionError" in result["error"]


def test_crawl_channel_robots_denied(app, monkeypatch):
    monkeypatch.setattr(spider, "robots_allowed", lambda path: False)
    result = spider.crawl_channel(app.config["DATABASE"], "/gaokao/x/", "X频道")
    assert result["status"] == "robots_denied"
    assert result["parsed"] == 0 and result["added"] == 0


def test_crawl_channel_writes_pending(app, monkeypatch):
    monkeypatch.setattr(spider, "robots_allowed", lambda path: True)
    monkeypatch.setattr(
        spider, "fetch_html",
        lambda url: _page(
            '<a href="/show/201.html">2025届湖南高三历史试题</a>',
            '<a href="/show/202.html">2025届湖南高三地理试题</a>',
        ),
    )
    result = spider.crawl_channel(app.config["DATABASE"], "/gaokao/hunan/", "湖南")
    assert result["status"] == "ok"
    assert result["parsed"] == 2
    assert result["added"] == 2
    conn = models.get_db(app.config["DATABASE"])
    try:
        rows = conn.execute(
            "SELECT * FROM pending_paper WHERE source_school = '湖南'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2


def test_crawl_channel_dedup_by_source_url(app, monkeypatch):
    """source_url 去重：已在待审核/已入库的链接不再新增。"""
    models.add_pending_paper(
        app.config["DATABASE"], "旧记录", "数学", 2025,
        "https://www.51test.net/show/old.html",
    )
    monkeypatch.setattr(spider, "robots_allowed", lambda path: True)
    monkeypatch.setattr(
        spider, "fetch_html",
        lambda url: _page(
            '<a href="/show/old.html">2025届高三数学试题</a>',
            '<a href="/show/new.html">2025届高三数学试题</a>',
        ),
    )
    result = spider.crawl_channel(app.config["DATABASE"], "/gaokao/x/", "X")
    assert result["parsed"] == 2
    assert result["added"] == 1


def test_crawl_rate_limit_between_channels(app, monkeypatch):
    """频道间限速：N 个频道 sleep N-1 次、每次 2 秒；失败频道不中断。"""
    sleeps = []
    monkeypatch.setattr(spider.time, "sleep", lambda s: sleeps.append(s))
    calls = []

    def fake_channel(db, path, label):
        calls.append(label)
        return {"path": path, "label": label, "status": "ok",
                "parsed": 1, "added": 1, "error": ""}

    monkeypatch.setattr(spider, "crawl_channel", fake_channel)
    channels = [("/a/", "A"), ("/b/", "B"), ("/c/", "C")]
    out = spider.crawl(app.config["DATABASE"], channels=channels)
    assert out["total_added"] == 3
    assert calls == ["A", "B", "C"]
    assert sleeps == [2, 2]


def test_channels_count_40():
    """频道集合回归：40 个频道（学科 6 + 省份 29 + 边缘 5）。"""
    assert len(spider.CHANNELS) == 40
