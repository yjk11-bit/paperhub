"""应用配置。"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# DeepSeek API Key 优先级：环境变量 > 本地配置文件 local_config.py。
# local_config.py 保存本机密钥，已加入 .gitignore，严禁提交 git；
# 复制 local_config.example.py 改名为 local_config.py，填入密钥即可。
try:
    from local_config import DEEPSEEK_API_KEY as _LOCAL_DEEPSEEK_API_KEY
except ImportError:
    _LOCAL_DEEPSEEK_API_KEY = ""


class Config:
    # 会话密钥（生产环境请通过环境变量覆盖）
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    # CSRF 防御纵深：会话 Cookie 限定 SameSite=Lax（浏览器层面拒绝跨站 POST 携带 Cookie）
    SESSION_COOKIE_SAMESITE = "Lax"
    # SQLite 数据库文件路径（instance 目录已加入 .gitignore，数据库不提交到 git；
    # 测试与 CI 通过环境变量 PAPERHUB_DATABASE 指向临时数据库，避免触碰真实库）
    DATABASE = os.environ.get(
        "PAPERHUB_DATABASE", os.path.join(BASE_DIR, "instance", "paperhub.db")
    )
    # DeepSeek API Key（未配置时 AI 预审接口返回友好提示，不影响其他功能）
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", _LOCAL_DEEPSEEK_API_KEY)
    # 上传文件大小上限（CSV 导入），超出返回 413 友好提示
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024
