"""PaperHub 应用入口。

启动服务时自动初始化数据库（自动建表）。
"""
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for

import config
import models

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


def login_required(view):
    """登录保护装饰器：未登录访问受限页面时重定向到登录页。"""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.route("/")
@login_required
def index():
    """首页（需登录）：后续阶段再实现页面业务逻辑。"""
    return render_template("index.html", username=session.get("username"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """用户注册：用户名不能重复，用户名、密码不能为空，密码哈希存储。"""
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
