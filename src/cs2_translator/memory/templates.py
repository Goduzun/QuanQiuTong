"""句式模板：带显式变量的固定句式复用。

安全规则（复用必须全部满足）：
- 模板已确认（confirmed=1）；
- 变量名属于已知类型，且捕获值通过该类型校验；
- 变量数量与顺序和模板定义一致；
- 整句完全匹配模板字面部分（不会改变原句语义）；
- 目标语言与翻译方向完全匹配。

不满足任一条件时返回 None，由调用方回退到在线 API。
本模块不做任何向量相似度匹配——相似不等于可互换。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 已知变量类型 -> 完整校验正则（按整值匹配）
# 新增类型时同步更新文档；未知变量名一律视为不可复用。
TEMPLATE_VARIABLE_TYPES: dict[str, str] = {
    # 点位：A/B/mid/long/short/A点/B点/中路等
    "site": r"[A-Za-z]{1,8}",
    "site_cn": r"[A-C中长短][A-Za-z]{0,6}",
    # 数字（血量、剩余人数、比分等）
    "number": r"\d{1,5}",
    # 武器名（原样保留，不翻译）
    "weapon": r"[A-Za-z][A-Za-z0-9\- ]{1,24}",
    # 方向
    "direction": r"(?i:left|right|mid|middle|centre|center)",
    # 阵营
    "team": r"(?i:CT|T)",
    # 玩家指代（昵称/代号）
    "player": r"[^\s:：]{1,32}",
}

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class TemplateError(ValueError):
    """模板定义非法。"""


@dataclass(frozen=True)
class CompiledTemplate:
    source_pattern: str
    translated_pattern: str
    target_language: str
    direction: str
    variables: tuple[str, ...]
    regex: re.Pattern


def extract_variables(pattern: str) -> list[str]:
    """提取模板中的变量名（按出现顺序，允许重复）。"""
    return [m.group(1) for m in _PLACEHOLDER_RE.finditer(pattern)]


def compile_template(
    source_pattern: str,
    translated_pattern: str,
    target_language: str,
    direction: str,
) -> CompiledTemplate:
    """编译模板；变量类型未知或译文模板变量不匹配时抛 TemplateError。"""
    source_vars = extract_variables(source_pattern)
    target_vars = extract_variables(translated_pattern)

    unknown = [v for v in source_vars if v not in TEMPLATE_VARIABLE_TYPES]
    if unknown:
        raise TemplateError(f"未知变量类型: {', '.join(sorted(set(unknown)))}")
    if sorted(set(source_vars)) != sorted(set(target_vars)):
        raise TemplateError("原文模板与译文模板的变量集合不一致")

    # 用命名捕获组替换占位符，字面部分全部转义
    parts = []
    last = 0
    for match in _PLACEHOLDER_RE.finditer(source_pattern):
        parts.append(re.escape(source_pattern[last : match.start()]))
        name = match.group(1)
        parts.append(f"(?P<{name}>{TEMPLATE_VARIABLE_TYPES[name]})")
        last = match.end()
    parts.append(re.escape(source_pattern[last:]))
    regex = re.compile("".join(parts), re.DOTALL)

    return CompiledTemplate(
        source_pattern=source_pattern,
        translated_pattern=translated_pattern,
        target_language=target_language.lower(),
        direction=direction,
        variables=tuple(source_vars),
        regex=regex,
    )


def fill_template(template: CompiledTemplate, values: dict[str, str]) -> str:
    """把捕获值填入译文模板（安全替换，不执行 format）。"""

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        value = values.get(name)
        return value if value is not None else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, template.translated_pattern)


@dataclass(frozen=True)
class TemplateMatch:
    translated: str
    variables: dict[str, str]
    template: CompiledTemplate


def match_template(template: CompiledTemplate, text: str) -> TemplateMatch | None:
    """尝试用模板翻译 text；不满足全部安全条件时返回 None。

    注意：调用方必须只传入已确认（confirmed=1）的模板；
    变量名必须属于已知类型（compile_template 阶段已保证）。
    """
    match = template.regex.fullmatch(text)
    if match is None:
        return None
    values = {name: match.group(name) for name in template.variables}
    # 变量值二次校验：必须非空且完整通过类型正则
    for name in template.variables:
        value = values.get(name) or ""
        if not value or not re.fullmatch(TEMPLATE_VARIABLE_TYPES[name], value):
            return None
    translated = fill_template(template, values)
    if not translated.strip():
        return None
    return TemplateMatch(translated=translated, variables=values, template=template)
