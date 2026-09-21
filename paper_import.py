"""试卷导入模块：CSV 批量导入与管理员手动录入共用的解析与校验逻辑。

设计约束：
- 所有导入/录入数据只进 pending_paper 待审核表，绝不直接入库
- 逐行校验：合法行全部入库，错误行汇总"行号 + 原因"返回前端
- 来源链接只允许 http/https 网页地址，PDF 链接一律拒绝（铁律）
"""
import csv
import io

import models

# CSV 列（模板按此顺序输出；解析按表头名称定位，列顺序可不同）
CSV_COLUMNS = ["标题", "科目", "年级", "年份", "难度", "等级",
               "是否带解析", "来源链接", "来源学校"]
_HEADER_TO_KEY = {
    "标题": "title", "科目": "subject", "年级": "grade", "年份": "year",
    "难度": "difficulty", "等级": "level", "是否带解析": "has_answer",
    "来源链接": "source_url", "来源学校": "source_school",
}
CSV_MAX_ROWS = 500  # 单次导入行数上限，防止异常大文件

# 难度档位的中文标签 → 数值（与 models.DIFFICULTIES 对应）
_DIFFICULTY_LABELS = {label: key for key, label in models.DIFFICULTIES.items()}
_TRUE_VALUES = {"是", "1", "true", "yes", "y", "对"}
_FALSE_VALUES = {"否", "0", "false", "no", "n", "错"}


def parse_difficulty(value):
    """解析难度：支持"基础/中档/拔高/竞赛级"或 1-4 数字。

    空值返回 None（审核时再定）；非法值返回 None 且调用方需提示错误，
    由调用方先判断 value 非空再校验。
    """
    if not value:
        return None
    if value.isdigit():
        key = int(value)
        return key if key in models.DIFFICULTIES else None
    return _DIFFICULTY_LABELS.get(value)


def parse_bool(value):
    """解析"是否带解析"：是/否/1/0/true/false 等；空值视为 False，非法值返回 None。"""
    if not value:
        return False
    v = value.strip().lower()
    if v in _TRUE_VALUES:
        return True
    if v in _FALSE_VALUES:
        return False
    return None


def validate_paper_fields(raw):
    """校验试卷字段（CSV 导入与手动录入共用）。

    raw 的键为英文字段名：title/subject/grade/year/difficulty/level/
    has_answer/source_url/source_school（值均为字符串）。

    返回 (fields, error)：校验通过时 fields 为收敛后的字段 dict
    （difficulty 为 int 或 None，has_answer 为 bool），error 为 None；
    失败时 fields 为 None，error 为中文原因。
    """
    title = (raw.get("title") or "").strip()
    subject = (raw.get("subject") or "").strip()
    grade = (raw.get("grade") or "").strip()
    year_raw = (raw.get("year") or "").strip()
    difficulty_raw = (raw.get("difficulty") or "").strip()
    level = (raw.get("level") or "").strip()
    has_answer_raw = (raw.get("has_answer") or "").strip()
    source_url = (raw.get("source_url") or "").strip()
    source_school = (raw.get("source_school") or "").strip()

    if not title:
        return None, "标题不能为空"
    if subject not in models.SUBJECTS:
        return None, f"科目必须是：{'、'.join(models.SUBJECTS)} 之一"
    if grade and grade not in models.GRADES:
        return None, f"年级必须是：{'、'.join(models.GRADES)} 之一（可留空）"
    try:
        year = int(year_raw)
    except ValueError:
        return None, "年份必须是数字"
    if not 2000 <= year <= 2100:
        return None, "年份需在 2000 至 2100 之间"
    if difficulty_raw and parse_difficulty(difficulty_raw) is None:
        return None, "难度必须是：基础/中档/拔高/竞赛级 或 1-4（可留空）"
    if level and level not in models.LEVELS:
        return None, f"等级必须是：{'、'.join(models.LEVELS)} 之一（可留空）"
    if not source_url:
        return None, "来源链接不能为空"
    if not source_url.lower().startswith(("http://", "https://")):
        return None, "来源链接必须是 http/https 网页地址"
    if source_url.lower().endswith(".pdf") or ".pdf?" in source_url.lower():
        return None, "禁止 PDF 文件链接（铁律：不托管 PDF）"
    if has_answer_raw and parse_bool(has_answer_raw) is None:
        return None, "是否带解析只能是：是/否/1/0/true/false（可留空）"

    return {
        "title": title,
        "subject": subject,
        "grade": grade,
        "year": year,
        "difficulty": parse_difficulty(difficulty_raw),
        "level": level,
        "has_answer": parse_bool(has_answer_raw),
        "source_url": source_url,
        "source_school": source_school,
    }, None


def parse_csv_content(text):
    """解析 CSV 文本，逐行校验。

    返回 (rows, errors)：rows 为 [(行号, 合法字段 dict)]，errors 为
    [{row: 行号, reason: 原因}]（数据行从第 2 行起算，第 1 行是表头）。
    表头缺列/文件为空/行数超限时抛 ValueError（调用方转友好提示）。
    """
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("CSV 文件为空")
    header = [h.strip() for h in header]
    missing = [c for c in CSV_COLUMNS if c not in header]
    if missing:
        raise ValueError("缺少列：" + "、".join(missing))
    idx = {c: header.index(c) for c in CSV_COLUMNS}

    rows, errors = [], []
    for line_no, cells in enumerate(reader, start=2):
        if not cells or all(not c.strip() for c in cells):
            continue  # 空行跳过
        raw = {
            key: (cells[idx[c]].strip() if idx[c] < len(cells) else "")
            for c, key in _HEADER_TO_KEY.items()
        }
        fields, error = validate_paper_fields(raw)
        if error:
            errors.append({"row": line_no, "reason": error})
        else:
            rows.append((line_no, fields))
        if line_no - 1 >= CSV_MAX_ROWS:
            raise ValueError(f"单次导入最多 {CSV_MAX_ROWS} 行")
    return rows, errors


def render_template_csv():
    """生成 CSV 导入模板内容（UTF-8 BOM，Excel 直接打开不乱码）。"""
    header = ",".join(CSV_COLUMNS)
    example = ("2025届浙江高三数学第一次联考试题及答案,数学,高三,2025,"
               "中档,名校联考,是,https://example.com/show/1,浙江")
    return "﻿" + header + "\n" + example + "\n"
