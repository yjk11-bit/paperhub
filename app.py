"""PaperHub 应用入口。

启动服务时自动初始化数据库（自动建表）。
"""
from flask import Flask

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


@app.route("/")
def index():
    """骨架占位首页：后续阶段再实现页面业务逻辑。"""
    return "PaperHub skeleton is running."


if __name__ == "__main__":
    app.run(debug=True)
