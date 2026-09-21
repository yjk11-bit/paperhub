"""数据模型：严格按照《项目方案.md》第 3 节数据库设计建表。

共 5 张表：paper、user、collect、view_history、pending_paper。
- 密码使用哈希存储，禁止明文（werkzeug.security）
- 版权约束：任何表都不保存 PDF 文件路径或二进制，只存元数据与外部网页链接
"""
import math
import os
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

# 建表 SQL（字段严格对照项目方案文档）
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS paper (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,                          -- 试卷完整标题
    subject       TEXT NOT NULL,                          -- 学科：语文/数学/英语/物理/化学/生物/历史/地理/政治
    year          INTEGER NOT NULL,                       -- 试卷年份，例：2025
    grade         TEXT NOT NULL DEFAULT '',               -- 年级：高一/高二/高三，空=未标注
    difficulty    INTEGER NOT NULL,                       -- 难度：1=基础 2=中档 3=拔高 4=竞赛级
    level         TEXT NOT NULL,                          -- 含金量等级：高考真题/省级统考/名校联考/地市统考/优质模考
    has_answer    BOOLEAN NOT NULL,                       -- 是否附带完整解析
    source_url    TEXT NOT NULL,                          -- 原始网页链接（跳转源站，不存 PDF）
    source_school TEXT,                                   -- 来源学校/机构
    create_time   DATETIME DEFAULT CURRENT_TIMESTAMP      -- 录入时间
);

CREATE TABLE IF NOT EXISTS user (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,                          -- 加密存储密码，禁止明文
    create_time   DATETIME DEFAULT CURRENT_TIMESTAMP,     -- 注册时间（个人中心展示）
    is_admin      BOOLEAN NOT NULL DEFAULT 0              -- 管理员标记：1=管理员（可进入审核面板）
);

CREATE TABLE IF NOT EXISTS collect (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,                        -- 外键关联 user
    paper_id     INTEGER NOT NULL,                        -- 外键关联 paper
    collect_time DATETIME DEFAULT CURRENT_TIMESTAMP,      -- 收藏时间
    FOREIGN KEY (user_id) REFERENCES user (id),
    FOREIGN KEY (paper_id) REFERENCES paper (id)
);

CREATE TABLE IF NOT EXISTS view_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,                           -- 用户 id
    paper_id  INTEGER NOT NULL,                           -- 试卷 id
    view_time DATETIME DEFAULT CURRENT_TIMESTAMP,         -- 浏览时间
    FOREIGN KEY (user_id) REFERENCES user (id),
    FOREIGN KEY (paper_id) REFERENCES paper (id)
);

CREATE TABLE IF NOT EXISTS pending_paper (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT,                                   -- 抓取的试卷标题
    subject       TEXT,                                   -- 抓取学科
    year          INTEGER,                                -- 抓取年份
    source_url    TEXT,                                   -- 原始网页链接
    source_school TEXT,                                   -- 来源
    page_summary  TEXT NOT NULL DEFAULT '',               -- 网页原文摘要（AI 预审参考）
    grade         TEXT NOT NULL DEFAULT '',               -- 年级：高一/高二/高三，空=未标注
    difficulty    INTEGER,                                -- 难度：1-4，空=审核时再定
    level         TEXT NOT NULL DEFAULT '',               -- 含金量等级，空=审核时再定
    has_answer    BOOLEAN NOT NULL DEFAULT 0,             -- 是否带解析
    crawl_time    DATETIME                                -- 抓取时间
);
"""


def get_db(db_path):
    """获取数据库连接（自动创建 instance 目录）。"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path):
    """初始化数据库：创建全部数据表（幂等，可重复执行）。"""
    conn = get_db(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        # 旧库迁移：user 表补充 create_time 列（个人中心展示注册时间）。
        # 老用户该字段为 NULL，页面显示 "—"；新注册用户由 create_user 写入。
        user_cols = [row[1] for row in conn.execute("PRAGMA table_info(user)")]
        if "create_time" not in user_cols:
            conn.execute("ALTER TABLE user ADD COLUMN create_time DATETIME")
        # 旧库迁移：user 表补充 is_admin 列（管理员审核面板权限校验）。
        if "is_admin" not in user_cols:
            conn.execute(
                "ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"
            )
        # 旧库迁移：paper 表补充 grade 列（首页年级筛选）。
        paper_cols = [row[1] for row in conn.execute("PRAGMA table_info(paper)")]
        if "grade" not in paper_cols:
            conn.execute(
                "ALTER TABLE paper ADD COLUMN grade TEXT NOT NULL DEFAULT ''"
            )
            # 存量试卷从标题推断年级：含"高一/高二"直接归属；
            # 含"高考/高三"归高三；推断不出的留空（不参与年级筛选，
            # 管理员审核或后续编辑时可补充）。
            conn.execute(
                "UPDATE paper SET grade = CASE"
                " WHEN title LIKE '%高一%' THEN '高一'"
                " WHEN title LIKE '%高二%' THEN '高二'"
                " WHEN title LIKE '%高三%' OR title LIKE '%高考%' THEN '高三'"
                " ELSE grade END"
                " WHERE grade = ''"
            )
        # 旧库迁移：pending_paper 表补充 page_summary 列（网页原文摘要，
        # 供 AI 预审读取；旧记录为空，AI 预审时仅依据标题推断）。
        # 以及 CSV 导入/手动录入的预填字段（grade/difficulty/level/has_answer，
        # 审核表单据此预填，管理员最终确认）。
        pending_cols = [row[1] for row in conn.execute("PRAGMA table_info(pending_paper)")]
        if "page_summary" not in pending_cols:
            conn.execute(
                "ALTER TABLE pending_paper"
                " ADD COLUMN page_summary TEXT NOT NULL DEFAULT ''"
            )
        if "grade" not in pending_cols:
            conn.execute(
                "ALTER TABLE pending_paper"
                " ADD COLUMN grade TEXT NOT NULL DEFAULT ''"
            )
        if "difficulty" not in pending_cols:
            conn.execute("ALTER TABLE pending_paper ADD COLUMN difficulty INTEGER")
        if "level" not in pending_cols:
            conn.execute(
                "ALTER TABLE pending_paper"
                " ADD COLUMN level TEXT NOT NULL DEFAULT ''"
            )
        if "has_answer" not in pending_cols:
            conn.execute(
                "ALTER TABLE pending_paper"
                " ADD COLUMN has_answer BOOLEAN NOT NULL DEFAULT 0"
            )
        conn.commit()
    finally:
        conn.close()


def hash_password(password):
    """密码哈希存储（禁止明文）。"""
    return generate_password_hash(password)


def check_password(password_hash, password):
    """校验密码与哈希是否匹配。"""
    return check_password_hash(password_hash, password)


def create_user(db_path, username, password):
    """创建用户：密码使用 hash_password 哈希后存储，禁止明文。

    返回新用户的 id；用户名重复时抛出 sqlite3.IntegrityError（路由层应先行查重）。
    """
    conn = get_db(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO user (username, password_hash, create_time)"
            " VALUES (?, ?, CURRENT_TIMESTAMP)",
            (username, hash_password(password)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_user_by_id(db_path, user_id):
    """按 id 查询用户，不存在返回 None。"""
    conn = get_db(db_path)
    try:
        return conn.execute(
            "SELECT * FROM user WHERE id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()


def get_user_by_username(db_path, username):
    """按用户名查询用户，不存在返回 None。"""
    conn = get_db(db_path)
    try:
        return conn.execute(
            "SELECT * FROM user WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()


# 学科与难度枚举（与项目方案文档一致）
SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治"]
DIFFICULTIES = {1: "基础", 2: "中档", 3: "拔高", 4: "竞赛级"}
# 年级枚举（空字符串 = 未标注，不参与年级筛选）
GRADES = ["高一", "高二", "高三"]
# 试卷含金量等级（与项目方案文档一致）
LEVELS = ["高考真题", "省级统考", "名校联考", "地市统考", "优质模考"]
# 题库列表页允许的排序字段白名单（列名 → 界面文案）。
# ORDER BY 无法参数化，列名与方向都经白名单校验，杜绝 SQL 注入。
PAPER_SORT_COLUMNS = {"year": "年份", "difficulty": "难度", "create_time": "上传时间"}


def _escape_like(keyword):
    """转义 LIKE 通配符（\\、%、_），保证搜索关键词按字面匹配。

    否则用户搜索 "%" 会命中全部试卷、"1_2" 会把 "_" 当作任意单字符。
    """
    return (
        keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


def query_papers(db_path, keyword=None, subject=None, difficulty=None,
                 grade=None, year=None, school=None, page=1, per_page=10,
                 sort="create_time", order="desc"):
    """分页查询试卷：标题关键词模糊搜索 + 科目/年级/年份/难度筛选 + 来源学校
    模糊搜索（条件可叠加），支持按年份/难度/上传时间升序或降序排序。

    返回 (papers, total, total_pages)。页码越界时收敛到最后一页。
    """
    where = []
    params = []
    if keyword:
        where.append("title LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(keyword)}%")
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if difficulty:
        try:
            d = int(difficulty)
        except ValueError:
            d = None  # 非法难度参数，忽略该筛选条件
        if d is not None:
            where.append("difficulty = ?")
            params.append(d)
    if grade:
        where.append("grade = ?")
        params.append(grade)
    if year:
        try:
            y = int(year)
        except ValueError:
            y = None  # 非法年份参数，忽略该筛选条件
        if y is not None:
            where.append("year = ?")
            params.append(y)
    if school:
        where.append("source_school LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(school)}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # 排序：ORDER BY 无法参数化，列名与方向经白名单校验后拼接（防注入），
    # 非法值回落到默认"按上传时间倒序"。
    sort_col = sort if sort in PAPER_SORT_COLUMNS else "create_time"
    sort_dir = order if order in ("asc", "desc") else "desc"

    conn = get_db(db_path)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM paper {where_sql}", params
        ).fetchone()[0]
        total_pages = max(1, math.ceil(total / per_page))
        page = min(max(1, page), total_pages)
        rows = conn.execute(
            f"SELECT * FROM paper {where_sql}"
            f" ORDER BY {sort_col} {sort_dir}, id {sort_dir} LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
        return rows, total, total_pages
    finally:
        conn.close()


def get_paper_by_id(db_path, paper_id):
    """按 id 查询单条试卷，不存在返回 None。"""
    conn = get_db(db_path)
    try:
        return conn.execute(
            "SELECT * FROM paper WHERE id = ?", (paper_id,)
        ).fetchone()
    finally:
        conn.close()


def is_collected(db_path, user_id, paper_id):
    """判断用户是否已收藏该试卷。"""
    conn = get_db(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM collect WHERE user_id = ? AND paper_id = ?",
            (user_id, paper_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def add_collect(db_path, user_id, paper_id):
    """收藏试卷。已收藏时拦截不重复插入，返回 False；成功返回 True。"""
    conn = get_db(db_path)
    try:
        if conn.execute(
            "SELECT 1 FROM collect WHERE user_id = ? AND paper_id = ?",
            (user_id, paper_id),
        ).fetchone():
            return False
        conn.execute(
            "INSERT INTO collect (user_id, paper_id) VALUES (?, ?)",
            (user_id, paper_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def remove_collect(db_path, user_id, paper_id):
    """取消收藏。返回是否确实删除了记录。"""
    conn = get_db(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM collect WHERE user_id = ? AND paper_id = ?",
            (user_id, paper_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def query_user_collects(db_path, user_id, page=1, per_page=10):
    """分页查询用户收藏的试卷（JOIN paper），返回 (papers, total, total_pages)。"""
    conn = get_db(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM collect WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        total_pages = max(1, math.ceil(total / per_page))
        page = min(max(1, page), total_pages)
        rows = conn.execute(
            "SELECT paper.* FROM collect JOIN paper ON paper.id = collect.paper_id"
            " WHERE collect.user_id = ?"
            " ORDER BY collect.collect_time DESC, collect.id DESC LIMIT ? OFFSET ?",
            (user_id, per_page, (page - 1) * per_page),
        ).fetchall()
        return rows, total, total_pages
    finally:
        conn.close()


def add_view_history(db_path, user_id, paper_id):
    """记录一次试卷浏览：同一用户对同一试卷只保留最新一条记录（先删旧再插入）。"""
    conn = get_db(db_path)
    try:
        conn.execute(
            "DELETE FROM view_history WHERE user_id = ? AND paper_id = ?",
            (user_id, paper_id),
        )
        conn.execute(
            "INSERT INTO view_history (user_id, paper_id) VALUES (?, ?)",
            (user_id, paper_id),
        )
        conn.commit()
    finally:
        conn.close()


def query_view_history(db_path, user_id, page=1, per_page=10):
    """分页查询当前用户的浏览历史（JOIN paper，按浏览时间倒序）。

    返回 (rows, total, total_pages)。rows 中 history_id 为浏览记录 id，
    id 为试卷 id，view_time 为最近一次浏览时间。
    """
    conn = get_db(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM view_history WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        total_pages = max(1, math.ceil(total / per_page))
        page = min(max(1, page), total_pages)
        rows = conn.execute(
            "SELECT p.*, vh.id AS history_id, vh.view_time"
            " FROM view_history vh JOIN paper p ON p.id = vh.paper_id"
            " WHERE vh.user_id = ?"
            " ORDER BY vh.view_time DESC, vh.id DESC LIMIT ? OFFSET ?",
            (user_id, per_page, (page - 1) * per_page),
        ).fetchall()
        return rows, total, total_pages
    finally:
        conn.close()


def delete_view_history(db_path, user_id, history_id):
    """删除一条浏览记录（仅限本人的记录）。返回是否确实删除了记录。"""
    conn = get_db(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM view_history WHERE id = ? AND user_id = ?",
            (history_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_view_history(db_path, user_id):
    """清空当前用户的全部浏览历史。返回删除的记录条数。"""
    conn = get_db(db_path)
    try:
        cur = conn.execute("DELETE FROM view_history WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def add_pending_paper(db_path, title, subject, year, source_url,
                      source_school=None, page_summary="", grade="",
                      difficulty=None, level="", has_answer=0):
    """写入一条待审核元数据（爬虫采集或管理员录入，审核通过前不上线）。

    返回记录 id。page_summary 为爬虫抓到的网页原文摘要，供 AI 预审参考；
    grade/difficulty/level/has_answer 为可选预填值，审核时管理员最终确认。
    """
    conn = get_db(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO pending_paper (title, subject, year, source_url,"
            " source_school, page_summary, grade, difficulty, level,"
            " has_answer, crawl_time)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (title, subject, year, source_url, source_school, page_summary,
             grade, difficulty, level, has_answer),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def source_url_exists(db_path, source_url):
    """source_url 已存在于待审核表或试卷表时返回 True（导入/录入去重用）。"""
    conn = get_db(db_path)
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


def query_pending_papers(db_path, page=1, per_page=10):
    """分页查询待审核元数据（按抓取时间倒序）。返回 (rows, total, total_pages)。

    页码越界时收敛到最后一页；与全站其他列表页保持同样的分页规范。
    """
    conn = get_db(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM pending_paper").fetchone()[0]
        total_pages = max(1, math.ceil(total / per_page))
        page = min(max(1, page), total_pages)
        rows = conn.execute(
            "SELECT * FROM pending_paper ORDER BY crawl_time DESC, id DESC"
            " LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page),
        ).fetchall()
        return rows, total, total_pages
    finally:
        conn.close()


def get_pending_paper_by_id(db_path, pending_id):
    """按 id 查询待审核记录，不存在返回 None。"""
    conn = get_db(db_path)
    try:
        return conn.execute(
            "SELECT * FROM pending_paper WHERE id = ?", (pending_id,)
        ).fetchone()
    finally:
        conn.close()


def approve_pending_paper(db_path, pending_id, title, subject, year, difficulty,
                          level, has_answer, source_url, source_school=None,
                          grade=""):
    """审核通过：元数据转正入库 paper 表，并删除待审核记录（同一事务）。

    返回新试卷的 id。调用前需在路由层完成字段校验。
    """
    conn = get_db(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO paper (title, subject, year, grade, difficulty, level,"
            " has_answer, source_url, source_school)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, subject, year, grade, difficulty, level, has_answer,
             source_url, source_school),
        )
        conn.execute("DELETE FROM pending_paper WHERE id = ?", (pending_id,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def delete_pending_paper(db_path, pending_id):
    """审核驳回：删除待审核记录。返回是否确实删除了记录。"""
    conn = get_db(db_path)
    try:
        cur = conn.execute("DELETE FROM pending_paper WHERE id = ?", (pending_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
