"""PaperHub 元数据爬虫：只采集试卷元信息，绝不下载 PDF。

目标站点：无忧考网高考频道 https://www.51test.net
（该站 robots.txt 声明 "User-agent: * / Allow: / / Disallow: /tag/"，
/gaokao/ 路径允许抓取。）

版权约束（必须严格遵守，违反即侵权）：
- ❌ 禁止下载、保存 PDF 二进制文件，禁止任何形式的 PDF 托管
- ✅ 只采集试卷元信息：标题、学科、年份、来源地区、原始网页链接
- ✅ 抓取结果写入 pending_paper 待审核表，经管理员人工审核后才入库上线
- 抓取限速：每两次请求之间至少间隔 CRAWL_DELAY_SECONDS 秒
- 遵守 robots 协议：抓取前检查 robots.txt，目标路径被禁止则中止
"""
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

import models

BASE_URL = "https://www.51test.net"
# 列表页路径：高考真题 / 高考模拟试题
LIST_PATHS = ["/gaokao/gkst/", "/gaokao/st/"]
# 限速：每两次请求间隔秒数
CRAWL_DELAY_SECONDS = 2
TIMEOUT = 15
HEADERS = {
    "User-Agent": "PaperHubMetadataBot/1.0 (metadata-only indexer)",
}

# 标题中的年份，例：2026年贵州普通高中学业水平选择性考试化学试题及答案 → 2026
_YEAR_RE = re.compile(r"(20\d{2})")
# 标题中的省份/地区关键词 → 来源学校/机构字段
_PROVINCE_RE = re.compile(
    r"(北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|"
    r"山东|河南|湖北|湖南|广东|广西|海南|四川|贵州|云南|西藏|陕西|甘肃|青海|宁夏|"
    r"新疆|内蒙古)"
)
# 标题关键词 → 学科（匹配不到时 subject 为 None，由管理员审核时人工选定）
_SUBJECT_KEYWORDS = {
    "语文": "语文",
    "数学": "数学",
    "英语": "英语",
    "物理": "物理",
    "化学": "化学",
    "生物": "生物",
    "历史": "历史",
    "地理": "地理",
    "政治": "政治",
}
# 列表页表格里的下载按钮文字（如"试题""答案""作文"），不是试卷标题，
# 且其指向的详情页可能已失效（404），一律跳过不采集。
_GENERIC_LABELS = {"试题", "答案", "作文", "试卷", "下载"}


def robots_allowed(path):
    """简单遵守 robots 协议：抓取 robots.txt，目标路径被禁止时返回 False。

    仅解析 User-agent: * 规则下的 Disallow 行；robots.txt 无法访问时按
    允许处理（依赖限速降低影响），不因此中断爬取。
    """
    try:
        resp = requests.get(
            BASE_URL + "/robots.txt", headers=HEADERS, timeout=TIMEOUT
        )
        resp.raise_for_status()
    except requests.RequestException:
        return True
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key != "disallow":
            continue
        if value == "/":
            return False  # Disallow: / 禁止全站
        if value and value.endswith("/") and path.startswith(value):
            return False
    return True


def fetch_html(url):
    """抓取网页 HTML 文本。仅限网页；任何 PDF/二进制下载一律拒绝。"""
    if url.lower().endswith(".pdf") or ".pdf?" in url.lower():
        raise ValueError(f"禁止下载 PDF 文件：{url}")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    # 源站未在 Content-Type 声明字符集时（requests 默认按 ISO-8859-1 解码会
    # 产生乱码），改用内容推断的编码（如 GB2312），避免中文标题解析失败。
    if resp.encoding is None or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def parse_papers(html, base_url=BASE_URL):
    """从列表页 HTML 解析试卷元数据。

    返回 list[dict]：title/subject/year/source_url/source_school。
    subject 匹配不到时为 None、year 匹配不到时为 None，留待管理员审核时补充。
    """
    soup = BeautifulSoup(html, "html.parser")
    papers = []
    for a in soup.select("a[href*='/show/']"):
        href = urllib.parse.urljoin(base_url, a["href"])
        title = a.get_text(strip=True)
        if (not title or title in _GENERIC_LABELS
                or ("试题" not in title and "试卷" not in title)):
            continue
        year_match = _YEAR_RE.search(title)
        subject = next(
            (s for kw, s in _SUBJECT_KEYWORDS.items() if kw in title), None
        )
        province = _PROVINCE_RE.search(title)
        papers.append({
            "title": title,
            "subject": subject,
            "year": int(year_match.group(1)) if year_match else None,
            "source_url": href,
            "source_school": province.group(1) if province else None,
        })
    return papers


def _url_exists(db_path, source_url):
    """source_url 已存在于待审核表或试卷表时返回 True（去重，避免重复审核）。"""
    conn = models.get_db(db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM pending_paper WHERE source_url = ?", (source_url,)
        ).fetchone():
            return True
        return (
            conn.execute(
                "SELECT 1 FROM paper WHERE source_url = ?", (source_url,)
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def crawl(db_path, list_paths=None):
    """抓取列表页元数据并写入待审核表。返回新增条数。

    流程：逐个路径 robots 检查（任一被禁即中止）→ 逐页抓取（页间隔限速）→
    解析 → 按 source_url 去重 → 写 pending_paper。
    """
    list_paths = list_paths or LIST_PATHS
    for path in list_paths:
        if not robots_allowed(path):
            raise RuntimeError(f"robots 协议禁止抓取目标路径 {path}，爬虫已中止")
    added = 0
    for i, path in enumerate(list_paths):
        if i:
            # 页间限速：第一次请求前不等待，之后每次抓取前间隔 CRAWL_DELAY_SECONDS
            time.sleep(CRAWL_DELAY_SECONDS)
        html = fetch_html(BASE_URL + path)
        for item in parse_papers(html):
            if item["source_url"] and not _url_exists(db_path, item["source_url"]):
                models.add_pending_paper(
                    db_path,
                    item["title"],
                    item["subject"],
                    item["year"],
                    item["source_url"],
                    item["source_school"],
                )
                added += 1
    return added
