"""CSRF 防护模块：会话级 Token 校验，统一覆盖全部 POST 请求。

轻量自实现（不引入额外依赖，逻辑透明便于学习与测试）：
- 每个会话持有一个随机 token（secrets.token_hex(32)），保存在 session 中，
  任何请求进入时惰性生成（渲染表单隐藏域 / 页面 meta 标签需要）；
- 所有 POST 请求必须携带该 token：表单隐藏域 csrf_token 或请求头
  X-CSRFToken，缺失或与会话不一致时一律返回 403，防范跨站请求伪造；
- GET/HEAD/OPTIONS 等无副作用方法不校验；
- 模板经上下文处理器注入 csrf_token 变量渲染隐藏域；base.html 中的
  全局 fetch 封装读取 <meta name="csrf-token">，自动为所有同源的非 GET
  请求附带 X-CSRFToken 请求头，前端无需逐处改造。

对比校验使用 secrets.compare_digest，恒定时间比较防时序侧信道。
"""
import secrets

from flask import abort, request, session

# session 中保存 token 的键
SESSION_KEY = "_csrf_token"
# 表单隐藏域字段名 / 请求头名称
FORM_FIELD = "csrf_token"
HEADER_NAME = "X-CSRFToken"


def get_csrf_token():
    """返回当前会话的 CSRF token；会话中尚不存在时生成并保存（惰性生成）。"""
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_hex(32)
        session[SESSION_KEY] = token
    return token


def _ensure_csrf_token():
    """before_request 钩子：确保会话持有 CSRF token（页面渲染需要）。"""
    if not session.get(SESSION_KEY):
        session[SESSION_KEY] = secrets.token_hex(32)
    return None


def _validate_csrf():
    """before_request 钩子：POST 请求必须携带与会话一致的 CSRF token，
    缺失或错误一律 403（Flask 默认 403 错误页，前端 fetch 收到 403 状态码）。"""
    if request.method != "POST":
        return None
    submitted = request.form.get(FORM_FIELD) or request.headers.get(HEADER_NAME)
    token = session.get(SESSION_KEY)
    if not token or not submitted or not secrets.compare_digest(token, submitted):
        abort(403)
    return None


def init_csrf(app):
    """把 CSRF 防护挂载到 Flask 应用。

    before_request 顺序：先确保会话持有 token，再统一校验全部 POST；
    上下文处理器向所有模板注入 csrf_token 变量（含登录/注册等独立页面）。
    """
    app.before_request(_ensure_csrf_token)
    app.before_request(_validate_csrf)
    app.context_processor(lambda: {"csrf_token": get_csrf_token()})
