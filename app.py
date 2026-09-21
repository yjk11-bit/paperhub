"""PaperHub 应用入口。

启动服务时自动初始化数据库（自动建表）。
"""
import logging
import threading
from datetime import datetime
from functools import wraps

from flask import (
    Flask, Response, abort, jsonify, redirect, render_template, request,
    session, url_for,
)

import ai_review
import config
import models
import paper_import
from crawler import spider as crawler

app = Flask(__name__)
app.config.from_object(config.Config)

# 爬虫日志：独立 logger（INFO 级别），输出到服务控制台；
# 不提升根 logger 级别，避免影响 Flask/Werkzeug 自身日志。
_logging_handler = logging.StreamHandler()
_logging_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
)
paperhub_logger = logging.getLogger("paperhub")
paperhub_logger.addHandler(_logging_handler)
paperhub_logger.setLevel(logging.INFO)
paperhub_logger.propagate = False

# 启动时自动建表：数据库文件位于 instance/paperhub.db
# （instance 目录已加入 .gitignore，数据库文件不会提交到 git）
models.init_db(app.config["DATABASE"])


@app.cli.command("init-db")
def init_db_command():
    """手动初始化数据库：flask --app app init-db"""
    models.init_db(app.config["DATABASE"])
    print("数据库初始化完成:", app.config["DATABASE"])


@app.cli.command("crawl")
def crawl_command():
    """运行元数据爬虫（多频道采集，只采集元数据，写入待审核表）：
    flask --app app crawl"""
    out = crawler.crawl(app.config["DATABASE"])
    for r in out["results"]:
        line = (f"{r['path']}（{r['label']}）：{r['status']}，"
                f"解析 {r['parsed']} 条，新增 {r['added']} 条")
        if r["error"]:
            line += f"，{r['error']}"
        print(line)
    print(f"爬取完成，共新增待审核 {out['total_added']} 条（需管理员审核后上线）")


# 后台爬虫任务状态（内存态，只记录最近一次任务；重启服务后清空）。
# status: idle / running / done / error
_crawl_task = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "results": None,
    "total_added": 0,
    "error": "",
}
_crawl_task_lock = threading.Lock()


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_crawl_task():
    """后台线程执行多频道采集，结束后把结果写入 _crawl_task。

    采集数据只写入 pending_paper 待审核表，绝不自动上线。
    """
    try:
        out = crawler.crawl(app.config["DATABASE"])
    except Exception as exc:
        paperhub_logger.error("后台爬虫任务异常: %s", exc, exc_info=True)
        with _crawl_task_lock:
            _crawl_task.update(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=_now_str(),
            )
    else:
        with _crawl_task_lock:
            _crawl_task.update(
                status="done",
                results=out["results"],
                total_added=out["total_added"],
                finished_at=_now_str(),
            )


def login_required(view):
    """登录保护装饰器：未登录访问受限页面时重定向到登录页；
    会话中的用户已被删除时清除会话回登录页（避免外键错误 500）。"""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if models.get_user_by_id(app.config["DATABASE"], session["user_id"]) is None:
            # 会话指向的用户已被删除：清除会话，回到登录页
            session.clear()
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """管理员权限校验：未登录重定向登录页，普通用户返回 403。"""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user = models.get_user_by_id(app.config["DATABASE"], session["user_id"])
        if user is None:
            # 会话中的用户已被删除：清除会话回登录页
            session.clear()
            return redirect(url_for("login"))
        if not user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_is_admin():
    """向所有模板注入 is_admin，导航栏据此决定是否显示审核面板入口。"""
    is_admin = False
    if "user_id" in session:
        user = models.get_user_by_id(app.config["DATABASE"], session["user_id"])
        is_admin = bool(user and user["is_admin"])
    return {"is_admin": is_admin}


def _parse_page(default=1):
    """解析分页页码，非法输入回落默认值。"""
    try:
        return int(request.args.get("page", default))
    except ValueError:
        return default


def _paper_filters():
    """解析题库列表页的筛选/排序参数（主页与 AJAX 接口共用）。

    返回 (filter_args, sort, order)：
    - filter_args：全部非空筛选条件的 dict（值均为字符串），
      原样传给模型层参数化查询，同时用于模板拼接分页链接；
    - sort/order：经白名单收敛（非法值回落默认"上传时间倒序"）。
    """
    raw = {
        "keyword": request.args.get("keyword", "").strip(),
        "subject": request.args.get("subject", "").strip(),
        "difficulty": request.args.get("difficulty", "").strip(),
        "grade": request.args.get("grade", "").strip(),
        "year": request.args.get("year", "").strip(),
        "school": request.args.get("school", "").strip(),
    }
    sort = request.args.get("sort", "").strip()
    if sort not in models.PAPER_SORT_COLUMNS:
        sort = "create_time"
    order = request.args.get("order", "").strip()
    if order not in ("asc", "desc"):
        order = "desc"
    return {k: v for k, v in raw.items() if v}, sort, order


# 年份下拉选项：当前年份往前推（2020 至 2026）
YEARS = list(range(2026, 2019, -1))


@app.route("/")
@login_required
def index():
    """题库主页（需登录）：分页展示 + 多维检索筛选（标题关键词、科目、年级、
    年份、难度、来源学校，条件可叠加）+ 排序，前端 AJAX 无刷新更新列表。"""
    filter_args, sort, order = _paper_filters()
    page = _parse_page()
    papers, total, total_pages = models.query_papers(
        app.config["DATABASE"],
        keyword=filter_args.get("keyword"),
        subject=filter_args.get("subject"),
        difficulty=filter_args.get("difficulty"),
        grade=filter_args.get("grade"),
        year=filter_args.get("year"),
        school=filter_args.get("school"),
        page=page,
        per_page=10,
        sort=sort,
        order=order,
    )
    # 页码越界收敛（查询内部已收敛，这里同步用于分页高亮与回显）
    page = min(page, total_pages)
    return render_template(
        "index.html",
        username=session.get("username"),
        papers=papers,
        page=page,
        total=total,
        total_pages=total_pages,
        filter_args=filter_args,
        sort=sort,
        order=order,
        subjects=models.SUBJECTS,
        difficulties=models.DIFFICULTIES,
        grades=models.GRADES,
        years=YEARS,
        sort_columns=models.PAPER_SORT_COLUMNS,
        **filter_args,
    )


@app.route("/api/papers")
def api_papers():
    """试卷列表 AJAX 接口（需登录）：接收与主页相同的筛选/排序/分页参数，
    返回列表 HTML 片段（JSON 包装），供首页无刷新更新。

    所有筛选值均经参数化 SQL 与白名单校验（见 models.query_papers）。
    """
    if "user_id" not in session:
        return jsonify(error="请先登录"), 401
    filter_args, sort, order = _paper_filters()
    page = _parse_page()
    papers, total, total_pages = models.query_papers(
        app.config["DATABASE"],
        keyword=filter_args.get("keyword"),
        subject=filter_args.get("subject"),
        difficulty=filter_args.get("difficulty"),
        grade=filter_args.get("grade"),
        year=filter_args.get("year"),
        school=filter_args.get("school"),
        page=page,
        per_page=10,
        sort=sort,
        order=order,
    )
    page = min(page, total_pages)
    html = render_template(
        "_paper_list_fragment.html",
        papers=papers,
        page=page,
        total=total,
        total_pages=total_pages,
        filter_args=filter_args,
        difficulties=models.DIFFICULTIES,
        has_filters=bool(filter_args),
        **filter_args,
    )
    return jsonify(html=html, total=total, page=page, total_pages=total_pages)


@app.route("/paper/<int:paper_id>")
@login_required
def paper_detail(paper_id):
    """试卷详情页（需登录）：展示完整试卷信息 + 收藏/取消收藏。

    每打开一次详情页自动记录浏览历史；同一用户同一试卷只保留最新一条记录。
    """
    paper = models.get_paper_by_id(app.config["DATABASE"], paper_id)
    if paper is None:
        abort(404)
    models.add_view_history(app.config["DATABASE"], session["user_id"], paper_id)
    collected = models.is_collected(
        app.config["DATABASE"], session["user_id"], paper_id
    )
    return render_template(
        "paper_detail.html",
        username=session.get("username"),
        paper=paper,
        collected=collected,
        difficulties=models.DIFFICULTIES,
    )


@app.route("/paper/<int:paper_id>/collect", methods=["POST"])
@login_required
def toggle_collect(paper_id):
    """收藏/取消收藏切换（需登录）：同一用户不会重复收藏同一份试卷。"""
    if models.get_paper_by_id(app.config["DATABASE"], paper_id) is None:
        abort(404)
    user_id = session["user_id"]
    if models.is_collected(app.config["DATABASE"], user_id, paper_id):
        models.remove_collect(app.config["DATABASE"], user_id, paper_id)
    else:
        models.add_collect(app.config["DATABASE"], user_id, paper_id)
    return redirect(url_for("paper_detail", paper_id=paper_id))


@app.route("/collects")
@login_required
def my_collects():
    """我的收藏（需登录）：分页展示当前登录用户收藏的试卷。"""
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    papers, total, total_pages = models.query_user_collects(
        app.config["DATABASE"], session["user_id"], page=page, per_page=10
    )
    return render_template(
        "collects.html",
        username=session.get("username"),
        papers=papers,
        page=page,
        total=total,
        total_pages=total_pages,
        difficulties=models.DIFFICULTIES,
    )


@app.route("/history")
@login_required
def browsing_history():
    """我的浏览历史（需登录）：按浏览时间倒序分页展示，每页 10 条，只显示本人记录。"""
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    rows, total, total_pages = models.query_view_history(
        app.config["DATABASE"], session["user_id"], page=page, per_page=10
    )
    return render_template(
        "history.html",
        username=session.get("username"),
        rows=rows,
        page=page,
        total=total,
        total_pages=total_pages,
        difficulties=models.DIFFICULTIES,
    )


@app.route("/history/<int:history_id>/delete", methods=["POST"])
@login_required
def delete_history(history_id):
    """删除一条浏览记录（需登录，仅能删除本人的记录，否则 404）。"""
    if not models.delete_view_history(
        app.config["DATABASE"], session["user_id"], history_id
    ):
        abort(404)
    return redirect(url_for("browsing_history"))


@app.route("/history/clear", methods=["POST"])
@login_required
def clear_history():
    """一键清空浏览历史（需登录，仅清空本人的记录）。"""
    models.clear_view_history(app.config["DATABASE"], session["user_id"])
    return redirect(url_for("browsing_history"))


@app.route("/profile")
@login_required
def profile():
    """个人中心（需登录）：展示用户名、注册时间，提供收藏与浏览历史入口。"""
    user = models.get_user_by_id(app.config["DATABASE"], session["user_id"])
    if user is None:
        # 会话中的用户已被删除：清除会话回登录页
        session.clear()
        return redirect(url_for("login"))
    return render_template(
        "profile.html", username=session.get("username"), user=user
    )


@app.route("/admin/pending")
@admin_required
def admin_pending():
    """管理员待审核面板：分页查看爬虫采集的待审核元数据，审核通过入库或驳回删除。"""
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    pendings, total, total_pages = models.query_pending_papers(
        app.config["DATABASE"], page=page, per_page=10
    )
    return render_template(
        "admin_pending.html",
        username=session.get("username"),
        pendings=pendings,
        page=page,
        total=total,
        total_pages=total_pages,
        subjects=models.SUBJECTS,
        difficulties=models.DIFFICULTIES,
        levels=models.LEVELS,
        grades=models.GRADES,
    )


@app.route("/admin/pending/<int:pending_id>/approve", methods=["POST"])
@admin_required
def approve_pending(pending_id):
    """审核通过：校验表单字段后把待审核元数据转正入库 paper 表。

    操作成功后停留在原分页（page 查询参数）；页码非法时回落第 1 页。
    """
    pending = models.get_pending_paper_by_id(app.config["DATABASE"], pending_id)
    if pending is None:
        abort(404)

    title = request.form.get("title", "").strip()
    subject = request.form.get("subject", "").strip()
    grade = request.form.get("grade", "").strip()
    level = request.form.get("level", "").strip()
    source_url = request.form.get("source_url", "").strip()
    try:
        year = int(request.form.get("year", "").strip())
        difficulty = int(request.form.get("difficulty", "").strip())
    except ValueError:
        year = difficulty = None
    has_answer = 1 if request.form.get("has_answer") == "1" else 0

    if not title or not source_url or not year or not difficulty:
        error = "标题、来源链接、年份、难度均不能为空"
    elif not (2000 <= year <= 2100):
        error = "年份需在 2000 至 2100 之间"
    elif subject not in models.SUBJECTS:
        error = "学科无效，请从列表中选择"
    elif grade and grade not in models.GRADES:
        # 年级允许留空（未标注，不参与年级筛选），非空值必须在枚举内
        error = "年级无效，请从列表中选择"
    elif difficulty not in models.DIFFICULTIES:
        error = "难度无效，请从列表中选择"
    elif level not in models.LEVELS:
        error = "试卷等级无效，请从列表中选择"
    else:
        models.approve_pending_paper(
            app.config["DATABASE"], pending_id, title, subject, year, difficulty,
            level, has_answer, source_url, pending["source_school"], grade=grade,
        )
        return redirect(url_for("admin_pending", page=request.args.get("page", 1)))

    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    pendings, total, total_pages = models.query_pending_papers(
        app.config["DATABASE"], page=page, per_page=10
    )
    return render_template(
        "admin_pending.html",
        username=session.get("username"),
        pendings=pendings,
        page=page,
        total=total,
        total_pages=total_pages,
        subjects=models.SUBJECTS,
        difficulties=models.DIFFICULTIES,
        levels=models.LEVELS,
        grades=models.GRADES,
        error=error,
    )


@app.route("/admin/pending/<int:pending_id>/reject", methods=["POST"])
@admin_required
def reject_pending(pending_id):
    """审核驳回：删除待审核记录。操作成功后停留在原分页。"""
    if not models.delete_pending_paper(app.config["DATABASE"], pending_id):
        abort(404)
    return redirect(url_for("admin_pending", page=request.args.get("page", 1)))


@app.route("/admin/pending/<int:pending_id>/ai_suggest", methods=["POST"])
@admin_required
def ai_suggest(pending_id):
    """AI 预审接口（仅管理员）：读取待审核记录的标题与网页摘要，调用 DeepSeek
    提取结构化字段，返回 JSON 供前端预填审核表单。

    仅辅助预填、不自动入库：管理员人工复核后仍需手动点「通过入库」。
    API 未配置/调用失败时返回 ok=False 与友好提示，页面不崩溃。
    """
    pending = models.get_pending_paper_by_id(app.config["DATABASE"], pending_id)
    if pending is None:
        abort(404)
    api_key = (app.config.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return jsonify(
            ok=False,
            error="未配置 DeepSeek API Key，请先在 local_config.py 中填写后重启服务",
        )
    try:
        result = ai_review.suggest_paper_fields(
            pending["title"] or "", pending["page_summary"] or "", api_key
        )
    except ai_review.AIReviewError:
        return jsonify(ok=False, error="AI预审失败，请手动填写")
    return jsonify(ok=True, **result)


@app.route("/admin/pending/csv_template")
@admin_required
def csv_template():
    """CSV 导入模板下载（仅管理员）。"""
    return Response(
        paper_import.render_template_csv(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=paperhub_import_template.csv",
        },
    )


@app.route("/admin/pending/csv_import", methods=["POST"])
@admin_required
def csv_import():
    """CSV 批量导入（仅管理员）：解析上传文件，逐行校验。

    合法行写入待审核表（不自动入库），错误行返回"行号 + 原因"汇总；
    部分成功策略：合法行全部入库。解析失败/编码无法识别时返回友好提示。
    """
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify(ok=False, error="请选择要上传的 CSV 文件")
    data = file.read()
    if not data:
        return jsonify(ok=False, error="CSV 文件为空")
    text = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return jsonify(ok=False, error="CSV 编码无法识别（请使用 UTF-8 或 GBK 编码）")
    try:
        rows, errors = paper_import.parse_csv_content(text)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc))

    db = app.config["DATABASE"]
    imported = 0
    for line_no, fields in rows:
        if models.source_url_exists(db, fields["source_url"]):
            errors.append({
                "row": line_no,
                "reason": "来源链接已存在（待审核或已入库）",
            })
            continue
        models.add_pending_paper(
            db,
            fields["title"],
            fields["subject"],
            fields["year"],
            fields["source_url"],
            fields["source_school"] or None,
            grade=fields["grade"],
            difficulty=fields["difficulty"],
            level=fields["level"],
            has_answer=1 if fields["has_answer"] else 0,
        )
        imported += 1
    errors.sort(key=lambda e: e["row"])
    return jsonify(ok=True, imported=imported, total=len(rows), errors=errors)


@app.route("/admin/pending/manual_add", methods=["POST"])
@admin_required
def manual_add():
    """管理员手动录入（仅管理员）：校验字段后写入待审核表，不自动入库。

    入参支持表单（multipart/form-data）或 JSON，字段名：
    title/subject/grade/year/difficulty/level/has_answer/
    source_url/source_school。
    """
    data = request.get_json(silent=True)
    if not data:
        data = request.form.to_dict()
    fields, error = paper_import.validate_paper_fields(data)
    if error:
        return jsonify(ok=False, error=error)
    if models.source_url_exists(app.config["DATABASE"], fields["source_url"]):
        return jsonify(ok=False, error="来源链接已存在（待审核或已入库）")
    new_id = models.add_pending_paper(
        app.config["DATABASE"],
        fields["title"],
        fields["subject"],
        fields["year"],
        fields["source_url"],
        fields["source_school"] or None,
        grade=fields["grade"],
        difficulty=fields["difficulty"],
        level=fields["level"],
        has_answer=1 if fields["has_answer"] else 0,
    )
    return jsonify(ok=True, id=new_id)


@app.route("/admin/crawl", methods=["POST"])
@admin_required
def admin_start_crawl():
    """启动后台爬虫任务（仅管理员）：遍历全部 40 个频道采集元数据写入待审核表。

    任务在后台线程执行（频道间限速，全程约 3 分钟），前端轮询
    /admin/crawl/status 查看进度与结果；同一时间只允许一个任务在跑，
    运行中再次触发返回友好提示。采集数据仅入待审核表，不自动上线。
    """
    with _crawl_task_lock:
        if _crawl_task["status"] == "running":
            return jsonify(ok=False, error="爬虫任务正在运行中，请稍候再试")
        _crawl_task.update(
            status="running",
            started_at=_now_str(),
            finished_at=None,
            results=None,
            total_added=0,
            error="",
        )
    thread = threading.Thread(target=_run_crawl_task, daemon=True)
    thread.start()
    paperhub_logger.info("后台爬虫任务已启动（由管理员触发）")
    return jsonify(ok=True, status="running")


@app.route("/admin/crawl/status")
@admin_required
def admin_crawl_status():
    """后台爬虫任务状态查询（仅管理员），供前端轮询展示进度与结果。"""
    with _crawl_task_lock:
        task = dict(_crawl_task)
    return jsonify(**task)


@app.errorhandler(413)
def upload_too_large(error):
    """上传文件超过 MAX_CONTENT_LENGTH 时的友好提示。"""
    return jsonify(ok=False, error="上传文件过大（最大 2MB）"), 413


@app.route("/register", methods=["GET", "POST"])
def register():
    """用户注册：用户名不能重复，用户名、密码不能为空，密码哈希存储。"""
    if "user_id" in session:
        # 已登录用户访问注册页：直接回题库
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template(
                "register.html", error="用户名和密码不能为空", username=username
            )
        if models.get_user_by_username(app.config["DATABASE"], username):
            return render_template(
                "register.html", error="用户名已存在，请更换", username=username
            )
        models.create_user(app.config["DATABASE"], username, password)
        return redirect(url_for("login", registered=1))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """用户登录：用户名存在 + 密码哈希匹配，登录成功写入 session。"""
    if "user_id" in session:
        # 已登录用户访问登录页：直接回题库
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template(
                "login.html", error="用户名和密码不能为空", username=username
            )
        user = models.get_user_by_username(app.config["DATABASE"], username)
        if user is None or not models.check_password(user["password_hash"], password):
            return render_template(
                "login.html", error="用户名不存在或密码错误", username=username
            )
        # 登录成功：写入 session
        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        return redirect(url_for("index"))
    return render_template("login.html", registered=request.args.get("registered"))


@app.route("/logout")
def logout():
    """退出登录：清除 session，重定向回登录页。"""
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
