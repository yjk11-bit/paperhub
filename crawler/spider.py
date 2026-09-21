"""PaperHub 元数据爬虫：只采集试卷元信息，绝不下载 PDF。

目标站点：无忧考网高考频道 https://www.51test.net
（该站 robots.txt 声明 "User-agent: * / Allow: / / Disallow: /tag/"，
/gaokao/ 路径允许抓取。）

版权约束（必须严格遵守，违反即侵权）：
- ❌ 禁止下载、保存 PDF 二进制文件，禁止任何形式的 PDF 托管
- ✅ 只采集试卷元信息：标题、学科、年份、来源地区、原始网页链接、网页摘要
- ✅ 抓取结果写入 pending_paper 待审核表，经管理员人工审核后才入库上线
- 抓取限速：每两次请求之间至少间隔 CRAWL_DELAY_SECONDS 秒
- 遵守 robots 协议：抓取前检查 robots.txt，目标路径被禁止则中止
"""
import logging
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

import models

logger = logging.getLogger("paperhub.crawler")

BASE_URL = "https://www.51test.net"
# 采集频道（(路径, 名称)），共 40 个：
# - 学科频道 6 个（Step-10）：高考题库 / 数学 / 语文 / 英语 / 理综 / 文综
# - 省份真题频道 29 个（Step-10.5）：各省高考频道 + 高考改革 / 考试说明，
#   按全站子频道扫描结果选取"能解析出试卷且不在库"的频道，其余 48 个
#   资讯类频道（报名/查分/分数线等）不纳入采集
# - 边缘真题频道 5 个（Step-10.5）：语文答案 / 作文 / 压题 / 压题作文 / 作文预测
# 只采集各频道首页：该站翻页子域（key/top.51test.net）403 反爬且主站
# 列表页无翻页链接，翻页采集方案已放弃（Step-10 结论）。
# 注意：/gaokao/shanxi/ 是山西，/gaokao/shanxi1/ 是陕西（站点 URL 命名如此）。
CHANNELS = [
    # 学科频道
    ("/gaokao/gaokaotiku/", "高考题库"),
    ("/gaokao/shuxue/", "数学"),
    ("/gaokao/yuwen/", "语文"),
    ("/gaokao/yingyu/", "英语"),
    ("/gaokao/lizong/", "理综"),
    ("/gaokao/wenzong/", "文综"),
    # 省份真题频道
    ("/gaokao/anhui/", "安徽"),
    ("/gaokao/beijing/", "北京"),
    ("/gaokao/chongqing/", "重庆"),
    ("/gaokao/fujian/", "福建"),
    ("/gaokao/gansu/", "甘肃"),
    ("/gaokao/guangdong/", "广东"),
    ("/gaokao/guangxi/", "广西"),
    ("/gaokao/guizhou/", "贵州"),
    ("/gaokao/hainan/", "海南"),
    ("/gaokao/heilongjiang/", "黑龙江"),
    ("/gaokao/henan/", "河南"),
    ("/gaokao/hubei/", "湖北"),
    ("/gaokao/jiangsu/", "江苏"),
    ("/gaokao/jiangxi/", "江西"),
    ("/gaokao/jilin/", "吉林"),
    ("/gaokao/liaoning/", "辽宁"),
    ("/gaokao/neimeng/", "内蒙古"),
    ("/gaokao/ningxia/", "宁夏"),
    ("/gaokao/qinghai/", "青海"),
    ("/gaokao/shanghai/", "上海"),
    ("/gaokao/shanxi/", "山西"),
    ("/gaokao/shanxi1/", "陕西"),
    ("/gaokao/sichuan/", "四川"),
    ("/gaokao/tianjin/", "天津"),
    ("/gaokao/xizang/", "西藏"),
    ("/gaokao/yunnan/", "云南"),
    ("/gaokao/zhejiang/", "浙江"),
    ("/gaokao/gaige/", "高考改革"),
    ("/gaokao/kaoshishuoming/", "考试说明"),
    # 边缘真题频道
    ("/gaokao/yuwendaan/", "语文答案"),
    ("/gaokao/zuowen/", "作文"),
    ("/gaokao/yati/", "压题"),
    ("/gaokao/yatizuowen/", "压题作文"),
    ("/gaokao/zuowenyuce/", "作文预测"),
]
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
        # source_url 只允许保存网页地址，指向 PDF 文件的链接一律跳过
        # （与 fetch_html 的拒绝规则一致：.pdf 后缀或 .pdf? 查询串形式都拒绝）
        if href.lower().endswith(".pdf") or ".pdf?" in href.lower():
            continue
        title = a.get_text(strip=True)
        if (not title or title in _GENERIC_LABELS
                or ("试题" not in title and "试卷" not in title)):
            continue
        # 描述性链接（"无忧考网…整理发布…欢迎浏览，仅供参考…来源：…"）
        # 不是试卷标题；同一 URL 通常另有短标题链接，跳过不丢数据。
        if "无忧考网" in title or "欢迎浏览" in title or "来源：" in title:
            continue
        year_match = _YEAR_RE.search(title)
        subject = next(
            (s for kw, s in _SUBJECT_KEYWORDS.items() if kw in title), None
        )
        province = _PROVINCE_RE.search(title)
        # 网页摘要：链接父节点里标题之外的说明性文字（如"含答案解析"），
        # 存入 page_summary 供 AI 预审参考；父节点是整页容器（文字 >800 字）
        # 或没有多余文字时为空串。
        summary = ""
        parent = a.parent
        if parent is not None and parent.name not in ("body", "html"):
            parent_text = parent.get_text(" ", strip=True)
            if len(parent_text) <= 800:
                summary = parent_text.replace(title, "", 1).strip()[:500]
        papers.append({
            "title": title,
            "subject": subject,
            "year": int(year_match.group(1)) if year_match else None,
            "source_url": href,
            "source_school": province.group(1) if province else None,
            "page_summary": summary,
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


def crawl_channel(db_path, path, label):
    """采集单个频道并写入待审核表，任何异常都不上抛（逐频道容错）。

    返回 dict 结果：status 为 ok（成功）/ robots_denied（robots 禁止，跳过）/
    failed（请求或解析失败），error 为失败原因（成功时为空串）；
    parsed 为解析到的条数、added 为去重后新增条数。
    """
    result = {
        "path": path, "label": label, "status": "ok",
        "parsed": 0, "added": 0, "error": "",
    }
    try:
        if not robots_allowed(path):
            result["status"] = "robots_denied"
            result["error"] = "robots 协议禁止抓取该频道"
            logger.warning("跳过频道 %s（%s）：robots 禁止", path, label)
            return result
        html = fetch_html(BASE_URL + path)
    except Exception as exc:
        # 单个频道请求失败不中断整体爬虫：记录错误日志，继续采集其余频道
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.error("频道 %s（%s）抓取失败: %s", path, label, exc, exc_info=True)
        return result
    try:
        items = parse_papers(html)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"解析失败: {type(exc).__name__}"
        logger.error("频道 %s（%s）解析失败: %s", path, label, exc, exc_info=True)
        return result
    result["parsed"] = len(items)
    for item in items:
        if item["source_url"] and not _url_exists(db_path, item["source_url"]):
            models.add_pending_paper(
                db_path,
                item["title"],
                item["subject"],
                item["year"],
                item["source_url"],
                item["source_school"],
                page_summary=item["page_summary"],
            )
            result["added"] += 1
    logger.info(
        "频道 %s（%s）采集完成：解析 %d 条，新增 %d 条",
        path, label, result["parsed"], result["added"],
    )
    return result


def crawl(db_path, channels=None):
    """依次采集各频道元数据并写入待审核表（可重复执行，去重靠 source_url）。

    返回 dict：results 为每频道结果列表、total_added 为全部新增条数。
    频道间限速；单个频道失败不影响其余频道。采集数据仅进入待审核表，
    经管理员人工审核后才上线（绝不自动入库）。
    """
    channels = channels or CHANNELS
    results = []
    total_added = 0
    for i, (path, label) in enumerate(channels):
        if i:
            # 频道间限速：第一次请求前不等待，之后每次抓取前间隔 CRAWL_DELAY_SECONDS
            time.sleep(CRAWL_DELAY_SECONDS)
        result = crawl_channel(db_path, path, label)
        results.append(result)
        total_added += result["added"]
    logger.info("爬虫采集结束：共 %d 个频道，新增待审核 %d 条", len(results), total_added)
    return {"results": results, "total_added": total_added}
