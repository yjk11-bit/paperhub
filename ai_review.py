"""AI 预审模块：调用 DeepSeek 大模型提取待审核试卷的结构化字段。

设计约束：
- 只做辅助预填：返回结果由管理员人工复核后手动入库，绝不自动入库
- 模型被要求只输出严格 JSON；解析失败/网络异常/超时统一抛 AIReviewError，
  由路由层返回友好提示，不让审核面板崩溃
- 提取结果全部经枚举白名单校验，无法判断/非法的字段按要求填空值，绝不编造
"""
import json

import requests

import models

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TIMEOUT = 20  # 秒；超时由 requests 抛异常并统一转成 AIReviewError


class AIReviewError(Exception):
    """AI 预审失败（网络异常、超时、返回格式非法等），路由层捕获后提示用户。"""


def _build_messages(title, page_summary):
    """构造 DeepSeek 对话消息：系统提示词声明只输出严格 JSON 并给出全部
    枚举值，用户消息携带试卷标题与网页摘要。"""
    system_prompt = (
        "你是一名高中试卷信息提取助手。根据用户提供的试卷标题与网页摘要，"
        "提取试卷的结构化信息。只输出一个严格的 JSON 对象，不要输出任何其他"
        "文字、注释或代码块标记。JSON 字段：\n"
        '- subject：学科，只能是以下之一：' + "、".join(models.SUBJECTS) + '；'
        '无法判断时为空字符串 ""\n'
        '- grade：年级，只能是以下之一：' + "、".join(models.GRADES) + '；'
        '无法判断时为空字符串 ""\n'
        "- exam_year：考试年份（整数）；无法判断时为 null\n"
        "- difficulty：难度档位整数，1=基础、2=中档、3=拔高、4=竞赛级；"
        "无法判断时为 null\n"
        '- level_type：试卷等级，只能是以下之一：' + "、".join(models.LEVELS) + '；'
        '无法判断时为空字符串 ""\n'
        "- has_analysis：是否附带答案解析，true 或 false；无法判断时为 false\n"
        "严禁编造信息：信息不足时严格按照上述规则输出空字符串或 null。"
    )
    summary = (page_summary or "").strip()
    user_prompt = (
        f"试卷标题：{title or '（无）'}\n"
        f"网页摘要：{summary or '（无摘要）'}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _chat_completion(api_key, messages, timeout=DEEPSEEK_TIMEOUT):
    """调用 DeepSeek chat/completions 接口，返回助手回复文本。

    任何网络异常/超时/非 2xx 响应统一转成 AIReviewError。
    （冒烟测试通过 monkeypatch 本函数离线模拟模型输出，不发起真实请求。）
    """
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.RequestException as exc:
        raise AIReviewError(f"DeepSeek API 调用失败：{exc}") from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIReviewError("DeepSeek API 返回格式异常") from exc


def parse_ai_json(text):
    """从模型输出中解析严格 JSON 对象：容忍代码块围栏与前后杂散文字。

    解析失败抛 AIReviewError。
    """
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise AIReviewError("模型未返回合法 JSON")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise AIReviewError("模型返回的 JSON 解析失败") from exc
    if not isinstance(data, dict):
        raise AIReviewError("模型返回的不是 JSON 对象")
    return data


def _as_int(value, lo, hi):
    """把模型返回值收敛为 lo~hi 区间内的整数，无法收敛返回 None。

    （布尔值是 int 的子类，单独排除，避免 True 被当成 1。）
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    return number if lo <= number <= hi else None


def _as_enum(value, allowed):
    """把模型返回值收敛为枚举值之一，不在枚举内返回空字符串。"""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if value in allowed else ""


def _normalize_suggestion(raw):
    """对模型返回的字段做枚举校验与类型收敛，规则与 _build_messages 一致：
    无法判断/非法的字段为空字符串/None/False，绝不编造。"""
    has_analysis = raw.get("has_analysis")
    if not isinstance(has_analysis, bool):
        has_analysis = str(has_analysis).strip().lower() in ("true", "1")
    return {
        "subject": _as_enum(raw.get("subject"), models.SUBJECTS),
        "grade": _as_enum(raw.get("grade"), models.GRADES),
        "exam_year": _as_int(raw.get("exam_year"), 2000, 2100),
        "difficulty": _as_int(raw.get("difficulty"), 1, 4),
        "level_type": _as_enum(raw.get("level_type"), models.LEVELS),
        "has_analysis": has_analysis,
    }


def suggest_paper_fields(title, page_summary, api_key):
    """AI 预审入口：调用 DeepSeek 提取试卷字段，返回经枚举校验的 dict：
    {subject, grade, exam_year, difficulty, level_type, has_analysis}。"""
    content = _chat_completion(api_key, _build_messages(title, page_summary))
    return _normalize_suggestion(parse_ai_json(content))
