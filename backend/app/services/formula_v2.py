"""结构化公式 v2：文本口径的无损解析、真实事件/分母校验与证据构造。

设计目标：
- 把 ``COUNT(buy)/曝光量`` 这类可识别文本公式解析成结构化 v2 对象，
  完整保留「事件」与「分母」的业务含义；
- 解析时向服务端真实注册表核对：事件必须来自事件接入（event_stream），
  分母必须来自登记过的分母口径（denominator_registry）；
- 任何无法无损转换的表达式都返回阻断原因与原值，绝不猜测着转换。

该模块只放纯领域逻辑，状态读写由 migration_hub 负责。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FORMULA_SCHEMA = "metric-formula/v2"
FORMAT_TEXT_V1 = "text-v1"
FORMAT_STRUCTURED_V2 = "structured-v2"

# 目前 v2 能无损表达的聚合函数。其它聚合（UV / SUM 去重等）一律阻断，
# 因为强行结构化会丢失原口径语义。
SUPPORTED_AGG = {"COUNT": "count", "CNT": "count", "计数": "count"}

# COUNT(event) 形式：事件标识允许英文/数字/下划线/点/中划线/中文。
_COUNT_EVENT_RE = re.compile(
    r"^\s*(?P<agg>COUNT|CNT|计数)\s*\(\s*(?P<event>[A-Za-z0-9_.\-一-鿿]+)\s*\)\s*$",
    re.IGNORECASE,
)
# 比值形式：<聚合事件> / <分母>
_RATIO_RE = re.compile(r"^\s*(?P<num>.+?)\s*/\s*(?P<den>.+?)\s*$")


@dataclass
class Blocker:
    code: str
    reason: str
    original: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason, "原值": self.original,
                "blocked_reason": self.reason}


@dataclass
class ParseResult:
    original: str
    convertible: bool
    structured: dict[str, Any] | None = None
    blockers: list[Blocker] = field(default_factory=list)
    event_tokens: list[str] = field(default_factory=list)
    denominator_token: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "可转换": self.convertible,
            "convertible": self.convertible,
            "structured_formula": self.structured,
            "结构化公式": self.structured,
            "blockers": [b.as_dict() for b in self.blockers],
            "阻断原因": [b.reason for b in self.blockers],
            "event_tokens": self.event_tokens,
            "denominator_token": self.denominator_token,
            "原值": self.original,
        }


class FormulaV2Parser:
    """按真实事件注册表与分母注册表解析文本公式。"""

    def __init__(
        self,
        event_registry: dict[str, dict[str, Any]],
        denominator_registry: dict[str, dict[str, Any]],
    ) -> None:
        # key 统一为 casefold 后的编码/别名
        self.events = event_registry
        self.denominators = denominator_registry

    # ---- 注册表解析 ----

    def resolve_event(self, token: str) -> dict[str, Any] | None:
        return self.events.get(token.casefold())

    def resolve_denominator(self, token: str) -> dict[str, Any] | None:
        token = token.strip()
        return self.denominators.get(token.casefold())

    # ---- 主解析入口 ----

    def parse(self, raw_formula: Any) -> ParseResult:
        original = str(raw_formula if raw_formula is not None else "").strip()
        if not original:
            return ParseResult(
                original=original,
                convertible=False,
                blockers=[Blocker("EMPTY_FORMULA", "公式为空，无法转换为结构化 v2", original)],
            )

        ratio = _RATIO_RE.match(original)
        if not ratio:
            return ParseResult(
                original=original,
                convertible=False,
                blockers=[Blocker(
                    "UNRECOGNIZED_EXPRESSION",
                    "不是可识别的「聚合事件 / 分母」比值形式，无法无损转换",
                    original,
                )],
            )

        num_text = ratio.group("num").strip()
        den_text = ratio.group("den").strip()

        matched = _COUNT_EVENT_RE.match(num_text)
        if not matched:
            # 区分：用了不支持的聚合函数，还是分子结构完全无法识别。
            agg_head = re.match(r"^\s*([A-Za-z一-鿿]+)\s*\(", num_text)
            if agg_head and agg_head.group(1).upper() not in SUPPORTED_AGG:
                code = "UNSUPPORTED_AGGREGATION"
                reason = (
                    f"聚合函数 {agg_head.group(1)} 不在结构化 v2 的无损子集"
                    f"（仅支持 COUNT(event)），转换会丢失口径语义"
                )
            else:
                code = "UNRECOGNIZED_NUMERATOR"
                reason = "分子不是可识别的 COUNT(事件) 形式，无法无损转换"
            return ParseResult(
                original=original,
                convertible=False,
                denominator_token=den_text,
                blockers=[Blocker(code, reason, original)],
            )

        agg_token = matched.group("agg")
        event_token = matched.group("event").strip()
        agg_code = SUPPORTED_AGG.get(agg_token.upper(), "count")

        blockers: list[Blocker] = []

        event = self.resolve_event(event_token)
        if event is None:
            blockers.append(Blocker(
                "UNKNOWN_EVENT",
                f"事件「{event_token}」未在事件接入中登记（未知事件），"
                f"无法确认其真实事件含义与依赖",
                original,
            ))
            event_dep: dict[str, Any] = {
                "event_code": event_token, "real": False, "verified": False,
            }
        else:
            event_status = str(event.get("status", ""))
            event_dep = {
                "event_code": event.get("事件编码", event_token),
                "event_name": event.get("事件名称", ""),
                "event_id": event.get("id"),
                "status": event_status,
                "real": True,
                "verified": event_status == "已验证",
            }
            if event_status != "已验证":
                blockers.append(Blocker(
                    "EVENT_NOT_VERIFIED",
                    f"事件「{event_token}」当前状态为{event_status or '未知'}，"
                    f"未通过验证，不能作为可执行口径证据",
                    original,
                ))

        denominator = self.resolve_denominator(den_text)
        if denominator is None:
            blockers.append(Blocker(
                "UNKNOWN_DENOMINATOR",
                f"分母「{den_text}」不是已登记的分母口径，无法解析其真实数据来源",
                original,
            ))
            den_dep: dict[str, Any] = {
                "label": den_text, "resolved": False,
            }
        else:
            den_dep = {
                "code": denominator.get("code"),
                "label": denominator.get("label", den_text),
                "source": denominator.get("source"),
                "source_module": denominator.get("source_module"),
                "resolved": True,
                "aliases": denominator.get("aliases", []),
            }

        if blockers:
            return ParseResult(
                original=original,
                convertible=False,
                blockers=blockers,
                event_tokens=[event_token],
                denominator_token=den_text,
            )

        structured = {
            "schema": FORMULA_SCHEMA,
            "format": FORMAT_STRUCTURED_V2,
            "numerator": {
                "aggregation": agg_code,
                "aggregation_text": agg_token.upper() if agg_token.isascii() else agg_token,
                "event": event_dep["event_code"],
                "event_name": event_dep.get("event_name", ""),
            },
            "denominator": {
                "code": den_dep["code"],
                "label": den_dep["label"],
                "source": den_dep["source"],
            },
            "expression": f"{agg_token.upper()}({event_dep['event_code']})/{den_dep['code']}",
            "meaning": (
                f"统计窗口内事件 {event_dep['event_code']}"
                f"（{event_dep.get('event_name', '')}）的触发次数，"
                f"除以{den_dep['label']}（{den_dep['source']}）"
            ),
            "event_dependencies": [event_dep],
            "denominator_dependencies": [den_dep],
        }
        return ParseResult(
            original=original,
            convertible=True,
            structured=structured,
            event_tokens=[event_token],
            denominator_token=den_text,
        )


def build_default_denominator_registry() -> dict[str, dict[str, Any]]:
    """登记可解析的真实分母口径。

    key 为别名的 casefold；曝光量来自曝光日志接入的去重记录数，
    与 MetricComputeService.consume_from_exposure_log 的口径一致。
    """
    exposure = {
        "code": "exposure_count",
        "label": "曝光量",
        "source": "exposure_log:已去重记录数",
        "source_module": "exposure_log",
        "aliases": ["曝光量", "曝光次数", "曝光数", "曝光", "exposure",
                    "exposure_count", "exposures", "impressions", "impression"],
    }
    registry: dict[str, dict[str, Any]] = {}
    for alias in exposure["aliases"]:
        registry[alias.casefold()] = exposure
    return registry
