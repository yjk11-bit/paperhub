"""PaperHub 应用入口。

启动服务时自动初始化数据库（自动建表）。
"""
from functools import wraps

from flask import Flask, abort, redirect, render_template, request, session, url_for

import config
import models
from crawler import spider as crawler

app = Flask(__name__)
app.config.from_object(config.Config)

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
    """运行元数据爬虫（只采集元数据，写入待审核表）：flask --app app crawl"""
    try:
        count = crawler.crawl(app.config["DATABASE"])
    except RuntimeError as exc:
        print("爬虫中止:", exc)
    else:
        print(f"爬取完成，新增待审核 {count} 条（需管理员审核后上线）")


def login_required(view):
    """登录保护装饰器：未登录访问受限页面时重定向到登录页。"""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
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


@app.route("/")
@login_required
def index():
    """题库主页（需登录）：分页展示 + 检索筛选（标题关键词、科目、难度，可叠加）。"""
    keyword = request.args.get("keyword", "").strip()
    subject = request.args.get("subject", "").strip()
    difficulty = request.args.get("difficulty", "").strip()
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1

    papers, total, total_pages = models.query_papers(
        app.config["DATABASE"],
        keyword=keyword,
        subject=subject,
        difficulty=difficulty,
        page=page,
        per_page=10,
    )
    return render_template(
        "index.html",
        username=session.get("username"),
        papers=papers,
        page=page,
        total=total,
        total_pages=total_pages,
        keyword=keyword,
        subject=subject,
        difficulty=difficulty,
        subjects=models.SUBJECTS,
        difficulties=models.DIFFICULTIES,
    )


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
    elif difficulty not in models.DIFFICULTIES:
        error = "难度无效，请从列表中选择"
    elif level not in models.LEVELS:
        error = "试卷等级无效，请从列表中选择"
    else:
        models.approve_pending_paper(
            app.config["DATABASE"], pending_id, title, subject, year, difficulty,
            level, has_answer, source_url, pending["source_school"],
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
        error=error,
    )


@app.route("/admin/pending/<int:pending_id>/reject", methods=["POST"])
@admin_required
def reject_pending(pending_id):
    """审核驳回：删除待审核记录。操作成功后停留在原分页。"""
    if not models.delete_pending_paper(app.config["DATABASE"], pending_id):
        abort(404)
    return redirect(url_for("admin_pending", page=request.args.get("page", 1)))


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
