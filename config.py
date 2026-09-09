"""应用配置。"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # 会话密钥（生产环境请通过 .env 环境变量覆盖，.env 已加入 .gitignore）
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    # SQLite 数据库文件路径（instance 目录已加入 .gitignore，数据库不提交到 git）
    DATABASE = os.path.join(BASE_DIR, "instance", "paperhub.db")
