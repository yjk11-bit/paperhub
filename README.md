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
| 题库首页 | 分页列表（每页 10 条）+ 全站导航栏标题搜索框 + 学科/年级/年份/难度/学校多维筛选（可叠加，AJAX 无刷新）+ 年份/难度/上传时间升降序排序；LIKE 通配符已转义 |
| 试卷详情页 | 完整元数据展示、跳转源站、收藏/取消收藏、自动记录浏览历史 |
| 我的收藏 | 分页展示本人收藏的试卷 |
| 浏览历史 | 打开详情页自动记录（同卷去重只留最新）、单条删除、一键清空、分页 |
| 个人中心 | 用户名、注册时间、收藏与浏览历史入口 |
| 元数据爬虫 | requests + BeautifulSoup 采集标题/学科/年份/来源链接/网页摘要；**40 个频道**顺序采集（学科 6：高考题库/数学/语文/英语/理综/文综；省份真题 29：各省高考频道+高考改革/考试说明；边缘真题 5：语文答案/作文/压题等），三重过滤（标题须含试题/试卷、跳过下载按钮、跳过描述性链接）挡掉资讯类内容，逐频道容错（单频道失败记录日志并继续），robots 逐路径检查、2 秒限速、按 source_url 去重、可重复执行 |
| 后台爬虫任务 | 管理员在审核面板一键【启动爬虫】：后台线程执行采集（40 频道全程约 3 分钟），前端轮询展示每频道状态/解析/新增明细；同一时间只允许一个任务，重复触发友好提示；采集数据仅入待审核表，绝不自动上线 |
| 管理员审核面板 | 分页查看待审核元数据、审核通过入库（同一事务）、驳回删除；导入/录入字段自动预填审核表单；普通用户 403 且无入口 |
| CSRF 防护 | 全部 POST 接口统一校验会话 Token（表单隐藏域 `csrf_token` / 请求头 `X-CSRFToken`），无合法 Token 一律 403；前端表单自动携带隐藏域、全局 fetch 封装自动附带请求头，会话 Cookie `SameSite=Lax` 纵深防御 |
| CSV 批量导入 / 手动录入 | 管理员上传 CSV（UTF-8/GBK，附模板下载、前端预览）或弹窗手动录入；逐行校验（枚举/年份/链接合法性，**拒绝 PDF 链接**），合法行入待审核表、错误行汇总"行号+原因"；**不直接入库** |
| AI 预审（审核辅助） | DeepSeek 大模型读取待审核记录的标题+网页摘要，自动提取学科/年级/年份/难度/等级/是否带解析并预填审核表单；**仅预填不自动入库**，人工复核后手动入库；调用失败/超时友好提示不崩溃 |

## 🛠️ 技术栈

### 后端
- Web 框架：Flask（Jinja2 模板）
- 数据库：SQLite（后期可迁移 MySQL）
- 爬虫：requests + BeautifulSoup（仅采集元数据）
- AI 预审：DeepSeek API（deepseek-chat），仅辅助提取字段，人工复核后入库
- 密码：werkzeug.security 哈希存储

### 前端
- HTML + TailwindCSS（CDN）
- 模板继承：`templates/base.html` 提供全站统一导航栏、搜索框、容器宽度与分页样式

### 部署 / 测试 / CI
- 生产部署：香港轻量服务器（腾讯云，免备案、大陆可访问）Ubuntu 22.04 + gunicorn + systemd，一键脚本 `deploy/deploy.py`（分阶段执行），线上地址 http://119.28.46.242
- 测试：pytest 正式测试套件（`tests/`，77 个用例，临时数据库、绝不触碰真实库）
- CI：GitHub Actions（`.github/workflows/ci.yml`），推送自动跑全部测试

![CI](https://github.com/yjk11-bit/paperhub/actions/workflows/ci.yml/badge.svg)

## 🚀 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # Windows（macOS/Linux 用 .venv/bin/pip）

# 2. 初始化数据库（启动服务时也会自动建表，可跳过）
.venv\Scripts\flask --app app init-db

# 3. 启动开发服务器，访问 http://127.0.0.1:5000
.venv\Scripts\flask --app app run --debug

# 4.（可选）运行元数据爬虫（多频道采集），抓取结果进入待审核表
.venv\Scripts\flask --app app crawl
#    也可以在审核面板直接点【🕷️ 启动爬虫】，后台执行并展示每频道明细

# 5. 设置管理员（在数据库中手动提权）
sqlite3 instance\paperhub.db "UPDATE user SET is_admin=1 WHERE username='你的用户名';"

# 6.（可选）配置 AI 预审：复制 local_config.example.py 为 local_config.py，
#    填入 DeepSeek API Key（https://platform.deepseek.com 创建），重启服务后
#    审核面板每条待审核记录旁即可点【AI预审】。该文件已被 .gitignore 忽略，
#    绝不会提交到 git；未配置时接口返回友好提示，不影响其他功能。

# 7.（可选）本地运行全部测试用例（测试使用临时数据库，不触碰真实数据）
.venv\Scripts\python -m pytest tests -v

# 8.（可选）部署到公网服务器（香港轻量免备案，详见 deploy/DEPLOY.md）
PAPERHUB_DEPLOY_HOST=你的服务器IP PAPERHUB_DEPLOY_PASS=你的密码 .venv\Scripts\python deploy\deploy.py stage1
```

> 数据库文件位于 `instance/paperhub.db`（已加入 .gitignore，不提交到 git）。

## 📍 路由一览

| 路由 | 方法 | 权限 | 说明 |
|---|---|---|---|
| `/` | GET | 登录 | 题库主页：搜索 + 筛选 + 排序 + 分页 |
| `/api/papers` | GET | 登录 | 题库列表 AJAX 接口：筛选/排序/分页参数同主页，返回列表 HTML 片段（JSON 包装） |
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
| `/admin/pending/<id>/ai_suggest` | POST | 管理员 | AI 预审：调用 DeepSeek 提取结构化字段返回 JSON，前端预填表单（仅预填不自动入库） |
| `/admin/pending/csv_template` | GET | 管理员 | 下载 CSV 导入模板 |
| `/admin/pending/csv_import` | POST | 管理员 | CSV 批量导入：逐行校验后合法行入待审核表，返回"成功数 + 错误行汇总" |
| `/admin/pending/manual_add` | POST | 管理员 | 手动录入：校验字段后写入待审核表（支持表单或 JSON） |
| `/admin/crawl` | POST | 管理员 | 启动后台爬虫任务：多频道采集写入待审核表（运行中重复触发返回友好提示） |
| `/admin/crawl/status` | GET | 管理员 | 查询后台爬虫任务状态与每频道结果（供前端轮询） |

## 🗃️ 数据库设计（SQLite，共 5 张表）

| 表 | 说明 |
|---|---|
| `paper` | 试卷：标题、学科、年级（高一/高二/高三，空=未标注）、年份、难度（1–4）、等级、是否带解析、来源链接、来源学校、录入时间 |
| `user` | 用户：用户名、密码哈希、注册时间、is_admin 管理员标记 |
| `collect` | 收藏：user_id + paper_id 多对多，收藏时间 |
| `view_history` | 浏览历史：user_id + paper_id，浏览时间（同卷去重只留最新） |
| `pending_paper` | 待审核：标题、学科、年级、年份、难度、等级、是否带解析、来源链接、来源、网页摘要、抓取时间；来源为爬虫采集 / CSV 导入 / 手动录入，存在即待审核，通过转正、驳回删除 |

> 约束：不保存 PDF 本地路径；`source_url` 只保存外部网页地址。爬虫采集的原始数据必须经管理员人工审核才会上线。

## 📂 项目目录结构

```
vibecoding/
├── app.py               # Flask 应用入口：路由、登录/管理员装饰器、init-db / crawl CLI 命令
├── models.py            # 数据模型：建表、迁移、全部增删改查函数
├── ai_review.py         # AI 预审模块：DeepSeek 调用、严格 JSON 解析、字段枚举收敛
├── paper_import.py      # CSV 解析与字段校验（导入/手动录入共用，拒绝 PDF 链接）
├── csrf_protect.py      # CSRF 防护：会话 Token 生成/校验，全部 POST 统一 403 拦截
├── tests/               # pytest 正式测试套件（临时数据库，不触碰真实数据）
├── .github/workflows/   # GitHub Actions CI（推送自动跑测试，失败阻断合并 main）
├── deploy/              # 生产部署：deploy.py 分阶段脚本 + DEPLOY.md 部署指南（香港轻量 + gunicorn + systemd）
├── config.py            # 应用配置（SECRET_KEY、数据库路径、DeepSeek Key、上传上限）
├── local_config.example.py # 本地配置模板（复制为 local_config.py 填 DeepSeek Key，git 忽略）
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

### pytest 正式测试套件（`tests/`，77 个用例，可一键执行）

```bash
.venv\Scripts\python -m pytest tests -v    # Windows
```

| 文件 | 覆盖范围 |
|---|---|
| `tests/test_csrf.py` | CSRF 防护：全部 POST 接口无 Token 一律 403、合法 Token（隐藏域/请求头）正常放行、GET 不受影响 |
| `tests/test_auth.py` | 注册/登录/退出流程回归 |
| `tests/test_admin_pending.py` | 审核通过（含 8 种非法字段校验）/驳回/AI 预审（monkeypatch 离线模拟 DeepSeek，绝不发真实请求）/权限控制 |
| `tests/test_admin_import.py` | CSV 导入（UTF-8/GBK、部分成功、行号+原因、PDF 拒绝、500 行上限）/手动录入（表单/JSON 双通道）/CSV 模板 |
| `tests/test_admin_crawl.py` | 爬虫触发/状态轮询/运行守卫/异常转 error/权限/CLI（离线模拟爬虫） |
| `tests/test_spider.py` | 爬虫解析三重过滤、PDF 双形态拒绝、robots 合规、逐频道容错、2 秒限速、去重（离线 mock） |

约定：**严禁写死主键 id**，测试数据全部动态生成；每个测试使用独立临时数据库（环境变量 `PAPERHUB_DATABASE`），绝不触碰真实 `instance/paperhub.db`；离线 mock，不发真实网络请求。

### GitHub Actions CI（`.github/workflows/ci.yml`）

推送任意分支 / 向 main 发起 PR 时自动执行：安装依赖 → 运行 pytest 全部测试。测试不通过即失败；main 分支已配置保护规则（required status check `test`），测试失败的代码无法合并进 main。

### 真实服务 curl 集成测试（交付前执行）

启动真实服务，用 curl + cookie jar 走完整用户流程（注册/登录/CSRF 403 与放行/管理员操作），结束后清理测试数据与临时脚本，才允许 git commit。

## 📌 已知取舍与后续规划

- **驳回 = 删除**：被驳回的待审核记录若仍在源站列表页，下次爬取会重新进入待审核；如需“永久拒绝记忆”需新增字段（当前版本按方案设计执行删除语义）。
- **AI 预审仅预填、绝不自动入库**：大模型提取结果必须经管理员人工复核后手动点「通过入库」；字段经枚举白名单收敛，信息不足时填空值不编造；未配置 DeepSeek Key 或调用失败时接口返回友好提示，不影响审核流程。
- **CSV 导入采用部分成功策略**：合法行全部入库、错误行按"文件行号 + 原因"汇总返回；必填为标题/科目/年份/来源链接，难度支持中文标签（基础/中档/拔高/竞赛级）或 1-4 数字，其余可留空审核时再定；单次最多 500 行、文件上限 2MB。
- **导入/录入的年级/难度/等级/带解析会预填到审核表单**（爬虫记录无这些字段时保持默认），管理员人工复核后最终确认。
- **爬虫放弃翻页采集**：源站翻页子域 403 反爬且列表页无翻页链接，改为固定采集 40 个频道首页（学科 6 + 省份真题 29 + 边缘真题 5，经全站 82 个子频道扫描筛选"能解析出试卷且不在库"的频道，资讯类频道不采集），按 source_url 去重、可重复执行；单次全量采集约 3 分钟，增量数据可依靠 CSV 导入补充。
- **山西/陕西频道命名**：源站 URL 名为 `/gaokao/shanxi/`（山西）与 `/gaokao/shanxi1/`（陕西），代码已按页面实际内容命名标注。
- **后台爬虫任务为内存态**：任务状态只保留最近一次，重启服务后清空；采集中途重启服务会中断任务，重新点击【启动爬虫】即可。
- 爬虫学科/年份识别基于标题关键词与正则，匹配不到时字段为 `未识别`，由管理员审核时人工补全。
- 列表页表格中的下载按钮链接（文字为“试题/答案/作文”等通用标签，且指向的详情页可能已失效）会被爬虫直接跳过，不进入待审核。
- **CSRF 防护为轻量自实现**（`csrf_protect.py`，约 70 行，不引入额外依赖）：会话级随机 Token + `secrets.compare_digest` 恒定时间比较；所有 POST（含登录/注册）统一在 `before_request` 校验，无合法 Token 返回 403；GET 等无副作用方法不校验。前端配套：全部 POST 表单加隐藏域，base.html 的全局 fetch 封装自动为同源非 GET 请求附 `X-CSRFToken` 请求头（跨域请求不携带、不触发 CORS 预检）。
- **CI 与 main 分支保护**：GitHub Actions 在推送/PR 时跑 pytest；main 已配置 required status check `test`（不强制管理员：仓库主仍可按分步开发节奏直推 main，非管理员提交/PR 合并必须测试全绿）。
- **生产部署（2026-09-25 上线）**：腾讯云轻量香港服务器 + gunicorn + systemd（`deploy/DEPLOY.md`）；gunicorn 固定单 worker——后台爬虫任务为进程内内存态，多 worker 会状态不一致；`SECRET_KEY` 由部署脚本随机生成写入 `/etc/paperhub.env`（轮换会令在线会话失效，属预期）；数据库与 `local_config.py` 不随 git 下发，靠部署脚本 stage4 上传；线上库与本地库是两份数据，本地开发不会影响线上。
- 后续规划：试卷上下架、标签编辑。
