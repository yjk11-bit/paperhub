# PaperHub 高中精品试卷库

> 高质量高中试卷索引聚合平台，**不托管 PDF 文件**，仅做元数据索引 + 人工审核筛选，规避版权风险。
> 本项目为个人学习作品集，基于 Flask + SQLite + TailwindCSS 开发，适合 VibeCoding 快速搭建 MVP。

## 🎯 项目定位

市面上试卷网站充斥大量重复、低质量模拟卷，缺少按含金量、难度、学校层级、年份的精准筛选。
PaperHub 核心思路：

1. 只收录高含金量试卷，机器采集元数据 + 人工审核过滤垃圾试卷
2. 多维筛选：标题关键词搜索 + 学科、难度筛选（条件可叠加）
3. **版权合规：不存储 PDF 文件，保存原始来源网页链接，用户跳转源站查看**
4. 提供用户收藏、浏览历史；管理员后台完成爬虫数据审核

> ⚠️ 重要约束（铁律）
> - ❌ 爬虫**不下载、不保存 PDF 二进制文件**，代码层面对 `.pdf` 地址直接抛异常拒绝
> - ✅ 爬虫只抓取试卷元信息：标题、学科、年份、来源、原始网页链接
> - ✅ 爬虫抓取的数据进入待审核表，必须管理员人工审核后才正式上线
> - ✅ 遵守 robots 协议，请求间隔限速 2 秒

## ✅ 已实现功能

| 模块 | 说明 |
|---|---|
| 用户注册 / 登录 / 退出 | 密码哈希存储（werkzeug），session 会话；已登录用户访问登录/注册页自动跳转题库 |
| 题库首页 | 分页列表（每页 10 条）+ 全站导航栏标题搜索框 + 科目/难度筛选（可叠加）；LIKE 通配符已转义 |
| 试卷详情页 | 完整元数据展示、跳转源站、收藏/取消收藏、自动记录浏览历史 |
| 我的收藏 | 分页展示本人收藏的试卷 |
| 浏览历史 | 打开详情页自动记录（同卷去重只留最新）、单条删除、一键清空、分页 |
| 个人中心 | 用户名、注册时间、收藏与浏览历史入口 |
| 元数据爬虫 | requests + BeautifulSoup 采集标题/学科/年份/来源链接，robots 逐路径检查、限速、按 source_url 去重 |
| 管理员审核面板 | 分页查看待审核元数据、审核通过入库（同一事务）、驳回删除；普通用户 403 且无入口 |

## 🛠️ 技术栈

### 后端
- Web 框架：Flask（Jinja2 模板）
- 数据库：SQLite（后期可迁移 MySQL）
- 爬虫：requests + BeautifulSoup（仅采集元数据）
- 密码：werkzeug.security 哈希存储

### 前端
- HTML + TailwindCSS（CDN）
- 模板继承：`templates/base.html` 提供全站统一导航栏、搜索框、容器宽度与分页样式

### 部署
本地开发；可部署到 PythonAnywhere / Vercel

## 🚀 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # Windows（macOS/Linux 用 .venv/bin/pip）

# 2. 初始化数据库（启动服务时也会自动建表，可跳过）
.venv\Scripts\flask --app app init-db

# 3. 启动开发服务器，访问 http://127.0.0.1:5000
.venv\Scripts\flask --app app run --debug

# 4.（可选）运行元数据爬虫，抓取结果进入待审核表
.venv\Scripts\flask --app app crawl

# 5. 设置管理员（在数据库中手动提权）
sqlite3 instance\paperhub.db "UPDATE user SET is_admin=1 WHERE username='你的用户名';"
```

> 数据库文件位于 `instance/paperhub.db`（已加入 .gitignore，不提交到 git）。

## 📍 路由一览

| 路由 | 方法 | 权限 | 说明 |
|---|---|---|---|
| `/` | GET | 登录 | 题库主页：搜索 + 筛选 + 分页 |
| `/register` `/login` `/logout` | GET/POST | 游客 | 注册、登录、退出 |
| `/paper/<id>` | GET | 登录 | 试卷详情（自动记录浏览历史） |
| `/paper/<id>/collect` | POST | 登录 | 收藏 / 取消收藏切换 |
| `/collects` | GET | 登录 | 我的收藏 |
| `/history` | GET | 登录 | 我的浏览历史 |
| `/history/<id>/delete` `/history/clear` | POST | 登录 | 删除单条 / 清空浏览历史 |
| `/profile` | GET | 登录 | 个人中心 |
| `/admin/pending` | GET | 管理员 | 待审核面板（分页） |
| `/admin/pending/<id>/approve` | POST | 管理员 | 审核通过入库（校验年份 2000–2100 等字段） |
| `/admin/pending/<id>/reject` | POST | 管理员 | 驳回删除 |

## 🗃️ 数据库设计（SQLite，共 5 张表）

| 表 | 说明 |
|---|---|
| `paper` | 试卷：标题、学科、年份、难度（1–4）、等级、是否带解析、来源链接、来源学校、录入时间 |
| `user` | 用户：用户名、密码哈希、注册时间、is_admin 管理员标记 |
| `collect` | 收藏：user_id + paper_id 多对多，收藏时间 |
| `view_history` | 浏览历史：user_id + paper_id，浏览时间（同卷去重只留最新） |
| `pending_paper` | 爬虫待审核：标题、学科、年份、来源链接、来源、抓取时间；存在即待审核，通过转正、驳回删除 |

> 约束：不保存 PDF 本地路径；`source_url` 只保存外部网页地址。爬虫采集的原始数据必须经管理员人工审核才会上线。

## 📂 项目目录结构

```
vibecoding/
├── app.py               # Flask 应用入口：路由、登录/管理员装饰器、init-db / crawl CLI 命令
├── models.py            # 数据模型：建表、迁移、全部增删改查函数
├── config.py            # 应用配置（SECRET_KEY、数据库路径）
├── crawler/
│   └── spider.py        # 元数据爬虫：robots 检查、限速、PDF 下载拒绝、解析、去重
├── templates/
│   ├── base.html        # 全站统一布局：导航栏 + 标题搜索框 + 容器
│   ├── index.html       # 题库主页（搜索/筛选/分页）
│   ├── paper_detail.html # 试卷详情
│   ├── collects.html    # 我的收藏
│   ├── history.html     # 浏览历史
│   ├── profile.html     # 个人中心
│   ├── admin_pending.html # 管理员待审核面板
│   ├── login.html / register.html # 登录 / 注册
├── static/              # 静态资源目录（预留）
├── instance/            # SQLite 数据库（git 忽略）
├── requirements.txt     # 依赖清单
└── 项目方案.md.txt       # 项目方案文档
```

## 🧪 测试

每个功能模块交付前执行两套测试：

1. **单元冒烟测试**：Flask test client + 临时数据库，覆盖路由权限、搜索/筛选、收藏、浏览历史、审核流程、爬虫解析与 robots 合规（离线 mock）；严禁写死主键，全部动态查询真实 id。
2. **真实服务 curl 集成测试**：启动真实服务，用 curl + cookie jar 走完整用户流程，中文表单用百分号编码提交。

测试全部通过、清理测试数据与临时脚本后，才允许 git commit。

## 📌 已知取舍与后续规划

- **驳回 = 删除**：被驳回的待审核记录若仍在源站列表页，下次爬取会重新进入待审核；如需“永久拒绝记忆”需新增字段（当前版本按方案设计执行删除语义）。
- 爬虫学科/年份识别基于标题关键词与正则，匹配不到时字段为 `未识别`，由管理员审核时人工补全。
- 后续规划：试卷上下架、标签编辑、管理员手动录入、CSRF 防护、生产环境部署（PythonAnywhere / Vercel）。
