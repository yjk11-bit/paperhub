"""数据模型：严格按照《项目方案.md》第 3 节数据库设计建表。

共 5 张表：paper、user、collect、view_history、pending_paper。
- 密码使用哈希存储，禁止明文（werkzeug.security）
- 版权约束：任何表都不保存 PDF 文件路径或二进制，只存元数据与外部网页链接
"""
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
    password_hash TEXT NOT NULL                           -- 加密存储密码，禁止明文
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
            "INSERT INTO user (username, password_hash) VALUES (?, ?)",
            (username, hash_password(password)),
        )
        conn.commit()
        return cur.lastrowid
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
