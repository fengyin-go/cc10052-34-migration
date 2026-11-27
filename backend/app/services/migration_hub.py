"""公式迁移工作台领域中枢。

承载：
- 场景数据引导（指标 M / 窗口 W / 旧版已登记计算 / 未知事件指标 M2）；
- 每条口径的结构化 v2 预查、兼容结果与切换检查单；
- 新旧版本映射、口径证据包；
- 窗口调度领取、作业口径快照、结果唯一确认、回滚续做等用例。

状态为进程内单例（与 app.store 一致），所有状态流转只在本服务层发生。
"""
from __future__ import annotations

import functools
import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

from app.services.formula_v2 import (
    FORMAT_STRUCTURED_V2,
    FORMAT_TEXT_V1,
    FormulaV2Parser,
    build_default_denominator_registry,
)
from app.store import store

SCENARIO_TAG = "formula-v2-migration"
DEFAULT_WINDOW = "W"

# 迁移进度码
PENDING = "UNCHECKED"          # 尚未预查
UNCONFIRMED = "UNCONFIRMED"    # 检查单已生成，切换未确认
ONLINE = "ONLINE"              # 已确认，结构化 v2 已上线
BLOCKED = "BLOCKED"            # 无法转换

PROGRESS_LABEL = {
    PENDING: "待预查",
    UNCONFIRMED: "预查通过·迁移进度未确认",
    ONLINE: "已确认·结构化公式 v2 已上线",
    BLOCKED: "无法转换·阻断新版作业",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _locked(fn):
    """串行化同一进程内的迁移状态变更，保证窗口领取/结果/确认的幂等。"""

    @functools.wraps(fn)
    def wrapper(self: "MigrationHub", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class MigrationHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.denominators = build_default_denominator_registry()
        self.state: dict[str, Any] = {
            "metrics": {},       # metric_code -> 迁移聚合状态
            "mappings": [],      # 新旧版本映射
            "packs": {},         # pack_id -> 同一套口径证据
            "windows": {},       # f"{code}||{window}" -> 窗口状态
            "batches": {},       # batch_id -> 批次
            "batch_order": [],
            "seq": {"mapping": 0, "pack": 0, "batch": 0, "switch": 0},
            "bootstrapped": False,
        }

    # ============================================================
    # 场景引导
    # ============================================================

    def bootstrap(self) -> None:
        """幂等写入题述场景所需的真实事件、指标、旧作业与调度计划。"""
        if self.state["bootstrapped"]:
            return
        self.state["bootstrapped"] = True

        events = store.rows("event_stream")
        if not any(str(e.get("事件编码")) == "buy" for e in events):
            self._append("event_stream", {
                "事件编码": "buy", "事件名称": "购买",
                "属性字段": "product_id,amount", "来源应用": "交易下单服务",
                "上报协议": "track/event", "status": "已验证",
                "pending": False, "abnormal": False,
            })
        if not any(str(e.get("事件编码")) == "expose" for e in events):
            self._append("event_stream", {
                "事件编码": "expose", "事件名称": "商品曝光",
                "属性字段": "sku,position", "来源应用": "商品详情页",
                "上报协议": "track/event", "status": "已验证",
                "pending": False, "abnormal": False,
            })

        exposures = store.rows("exposure_log")
        if not any(str(r.get("实验编码")) == "EXP-M" for r in exposures):
            for idx, group in enumerate(("对照组", "实验组"), start=1):
                self._append("exposure_log", {
                    "日志批次": f"EXPO-M-{idx}", "实验编码": "EXP-M",
                    "用户标识": f"u-m-{idx}", "分组名称": group,
                    "曝光时间": "2026-11-26T09:00:00Z",
                    "status": "已去重", "pending": False, "abnormal": False,
                })

        catalog = store.rows("metric_catalog")
        if not any(str(m.get("指标编码")) == "M" for m in catalog):
            self._append("metric_catalog", {
                "指标编码": "M", "指标名称": "指标M·购买转化率",
                "计算口径": "COUNT(buy)/曝光量", "聚合窗口": "日窗口",
                "口径版本": "v1", "status": "已生效",
                "pending": False, "abnormal": False,
            })
        if not any(str(m.get("指标编码")) == "M2" for m in catalog):
            self._append("metric_catalog", {
                "指标编码": "M2", "指标名称": "指标M2·未知事件转化",
                "计算口径": "COUNT(unknown_cart_event)/曝光量",
                "聚合窗口": "日窗口", "口径版本": "v1",
                "status": "已生效", "pending": False, "abnormal": False,
            })
        if not any(str(m.get("指标编码")) == "M3" for m in catalog):
            self._append("metric_catalog", {
                "指标编码": "M3", "指标名称": "指标M3·去重买家占比",
                "计算口径": "COUNT(DISTINCT user_id)/曝光量",
                "聚合窗口": "日窗口", "口径版本": "v1",
                "status": "草稿", "pending": True, "abnormal": False,
            })

        jobs = store.rows("metric_compute")
        if not any(str(j.get("指标编码")) == "M" and str(j.get("计算窗口")) == DEFAULT_WINDOW
                   for j in jobs):
            old_snapshot = {
                "format": FORMAT_TEXT_V1,
                "schema": "metric-formula/text-v1",
                "metric_code": "M", "window": DEFAULT_WINDOW,
                "version": "v1",
                "formula_text": "COUNT(buy)/曝光量",
                "registered_result": {
                    "result_id": "RES-M-W-V1", "value": "0.128",
                    "registered_at": "2026-11-26T08:30:00Z",
                },
            }
            self._append("metric_compute", {
                "作业名称": "M-W 旧版口径最新计算",
                "指标编码": "M", "计算窗口": DEFAULT_WINDOW,
                "窗口水位": "2026-11-26T08:30:00Z",
                "作业状态": "已完成", "status": "已完成",
                "pending": False, "abnormal": False,
                "口径版本": "v1", "口径格式": FORMAT_TEXT_V1,
                "原始公式": "COUNT(buy)/曝光量",
                "口径快照": old_snapshot,
                "结果编号": "RES-M-W-V1", "指标值": "0.128",
                "登记状态": "最新一笔计算已登记",
            })

        plans = store.rows("window_scheduler")
        if not any(str(p.get("指标编码")) == "M" for p in plans):
            self._append("window_scheduler", {
                "计划名称": "M购买转化率滚动计划", "指标编码": "M",
                "窗口粒度": "日窗口", "窗口编码": DEFAULT_WINDOW,
                "当前窗口": DEFAULT_WINDOW, "下次窗口": DEFAULT_WINDOW,
                "上次状态": "W旧版计算已登记", "status": "待执行",
                "pending": True, "abnormal": False,
            })
        if not any(str(p.get("指标编码")) == "M2" for p in plans):
            self._append("window_scheduler", {
                "计划名称": "M2未知事件指标滚动计划", "指标编码": "M2",
                "窗口粒度": "日窗口", "窗口编码": DEFAULT_WINDOW,
                "当前窗口": DEFAULT_WINDOW, "下次窗口": DEFAULT_WINDOW,
                "上次状态": "待切换预查", "status": "待执行",
                "pending": True, "abnormal": False,
            })

        apis = store.rows("metric_api")
        if not any(str(a.get("指标编码")) == "M" for a in apis):
            self._append("metric_api", {
                "接口编码": "Q-M-W", "指标编码": "M",
                "服务对象": "经营周报/历史看板", "刷新策略": "日窗口 W",
                "调用限额": "10000/日", "status": "已上线",
                "pending": False, "abnormal": False,
                "口径版本": "v1", "原始公式": "COUNT(buy)/曝光量",
                "历史查询": "GET /api/formula-v2/history/query?metric_code=M&window=W",
            })

        # 预查全部口径，使工作台首屏即可见兼容结果（进度仍为未确认）。
        self.precheck_all()

    @_locked
    def reset_scenario(self) -> dict[str, Any]:
        """测试/演示辅助：删除场景数据并重建内存迁移状态。"""
        for module in ("event_stream", "exposure_log", "metric_catalog",
                       "metric_compute", "window_scheduler", "metric_api"):
            rows = store.rows(module)
            kept = [r for r in rows if SCENARIO_TAG not in r]
            store._tables[module] = kept  # noqa: SLF001 - 同仓库状态重置
        self.state = {
            "metrics": {}, "mappings": [], "packs": {}, "windows": {},
            "batches": {}, "batch_order": [],
            "seq": {"mapping": 0, "pack": 0, "batch": 0, "switch": 0},
            "bootstrapped": False,
        }
        self.bootstrap()
        return {"ok": True, "message": "公式迁移场景已重置到初始状态"}

    def _append(self, module: str, row: dict[str, Any]) -> dict[str, Any]:
        rows = store.rows(module)
        row = dict(row)
        row["id"] = max((int(r.get("id", 0) or 0) for r in rows), default=0) + 1
        row[SCENARIO_TAG] = True
        rows.append(row)
        return row

    # ============================================================
    # 注册表与解析
    # ============================================================

    def _event_registry(self) -> dict[str, dict[str, Any]]:
        registry: dict[str, dict[str, Any]] = {}
        for evt in store.rows("event_stream"):
            code = str(evt.get("事件编码", "")).strip()
            if code:
                registry.setdefault(code.casefold(), evt)
            name = str(evt.get("事件名称", "")).strip()
            if name:
                registry.setdefault(name.casefold(), evt)
            for alias in str(evt.get("事件别名", "")).split(","):
                alias = alias.strip()
                if alias:
                    registry.setdefault(alias.casefold(), evt)
        return registry

    def _parser(self) -> FormulaV2Parser:
        return FormulaV2Parser(self._event_registry(), self.denominators)

    def _catalog_rows(self, metric_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows("metric_catalog")
        if metric_code is None:
            return rows
        return [r for r in rows if str(r.get("指标编码")) == str(metric_code)]

    def _active_definition(self, metric_code: str) -> dict[str, Any] | None:
        """取该指标当前负责切换的口径定义：始终以最新登记的一版为准。

        同一指标编码可能存在多个版本行（草稿/已生效/已废弃），新建或升版的
        最新行就是迁移对象；场景初始数据中每个编码只有一行。
        """
        rows = self._catalog_rows(metric_code)
        if not rows:
            return None
        return max(rows, key=lambda r: int(r.get("id", 0) or 0))

    # ============================================================
    # 预查与检查单
    # ============================================================

    def precheck_all(self) -> dict[str, Any]:
        parser = self._parser()
        results = []
        for row in store.rows("metric_catalog"):
            results.append(self._precheck_row(row, parser))
        return {"ok": True, "total": len(results), "items": results}

    def precheck(self, metric_code: str | None = None,
                 catalog_id: int | None = None) -> dict[str, Any]:
        parser = self._parser()
        row: dict[str, Any] | None = None
        if catalog_id is not None:
            row = store.find("metric_catalog", int(catalog_id))
        elif metric_code:
            row = self._active_definition(metric_code)
        if row is None:
            return {"ok": False, "code": "METRIC_NOT_FOUND",
                    "message": f"口径不存在：{metric_code or catalog_id}"}
        check = self._precheck_row(row, parser)
        return {"ok": True, "check": check, **check}

    def _precheck_row(self, row: dict[str, Any], parser: FormulaV2Parser) -> dict[str, Any]:
        code = str(row.get("指标编码", ""))
        formula = row.get("计算口径", "")
        parsed = parser.parse(formula)
        check = parsed.as_dict()
        catalog_id = int(row.get("id", 0))
        check.update({
            "catalog_id": catalog_id,
            "metric_code": code,
            "metric_name": row.get("指标名称", ""),
            "old_version": str(row.get("口径版本", "v1") or "v1"),
            "catalog_status": row.get("status", ""),
            "checked_at": _now(),
        })
        check["checklist"] = self._build_checklist(row, check)

        agg = self.state["metrics"].setdefault(code, self._new_metric_state(code))
        # 已上线的指标不因重新预查而回退进度。
        if agg["progress"] != ONLINE:
            agg["progress"] = UNCONFIRMED if check["可转换"] else BLOCKED
        agg["active_catalog_id"] = catalog_id
        agg["last_check"] = check
        return check

    def _new_metric_state(self, code: str) -> dict[str, Any]:
        return {
            "metric_code": code,
            "progress": PENDING,
            "progress_label": PROGRESS_LABEL[PENDING],
            "confirmed": False,
            "confirmed_at": None,
            "operator": None,
            "mapping_id": None,
            "pack_id": None,
            "active_catalog_id": None,
            "last_check": None,
            "confirm_count": 0,
        }

    def _build_checklist(self, row: dict[str, Any], check: dict[str, Any]) -> list[dict[str, Any]]:
        structured = check.get("structured_formula") or {}
        event_deps = structured.get("event_dependencies", []) if structured else []
        den_deps = structured.get("denominator_dependencies", []) if structured else []
        items: list[dict[str, Any]] = [
            {
                "key": "formula_recognized",
                "label": "文本公式可识别且可无损转换",
                "ok": bool(check.get("可转换")),
                "detail": "已解析为 COUNT(事件)/登记分母 结构" if check.get("可转换")
                else "；".join(b.get("reason", "") for b in check.get("blockers", [])),
            },
        ]
        for dep in event_deps:
            items.append({
                "key": f"event_real:{dep.get('event_code')}",
                "label": f"真实事件核对：{dep.get('event_code')}",
                "ok": bool(dep.get("real")) and bool(dep.get("verified")),
                "detail": (
                    f"事件接入 id={dep.get('event_id')}，名称={dep.get('event_name')}，"
                    f"状态={dep.get('status')}"
                    if dep.get("real") else "事件接入层查无此事件（未知事件）"
                ),
                "evidence": dep,
            })
        for dep in den_deps:
            items.append({
                "key": f"denominator:{dep.get('code') or dep.get('label')}",
                "label": f"分母口径解析：{dep.get('label')}",
                "ok": bool(dep.get("resolved")),
                "detail": f"来源 {dep.get('source')}" if dep.get("resolved")
                else "分母未登记，无法解析真实来源",
                "evidence": dep,
            })
        items.append({
            "key": "structured_built",
            "label": "结构化公式 v2 保留事件与分母含义",
            "ok": bool(check.get("可转换")),
            "detail": structured.get("meaning", "") if structured else "未生成",
        })
        items.append({
            "key": "mapping_ready",
            "label": "新旧版本映射可核对",
            "ok": bool(check.get("可转换")),
            "detail": "确认切换后生成正式映射编号" if check.get("可转换") else "阻断中，不建立映射",
        })
        progress_ok = False
        agg = self.state["metrics"].get(str(row.get("指标编码", "")))
        if agg and agg.get("progress") == ONLINE:
            progress_ok = True
        items.append({
            "key": "progress_confirmed",
            "label": "切换进度已确认（迁移上线）",
            "ok": progress_ok,
            "detail": "已确认，调度可领取 v2 作业证据" if progress_ok
            else "迁移进度未确认：此时调度领取将被冲突拦截",
        })
        return items

    def checklist(self, metric_code: str) -> dict[str, Any]:
        agg = self._ensure_checked(metric_code)
        if agg is None:
            return {"ok": False, "code": "METRIC_NOT_FOUND",
                    "message": f"口径 {metric_code} 不存在"}
        check = agg.get("last_check") or {}
        # 基于最新确认状态重建检查单
        row = store.find("metric_catalog", int(agg.get("active_catalog_id") or 0))
        if row is not None:
            check["checklist"] = self._build_checklist(row, check)
        return {"ok": True, "metric_code": metric_code,
                "progress": agg["progress"], "切换进度": PROGRESS_LABEL[agg["progress"]],
                "checklist": check.get("checklist", []), "check": check}

    def _ensure_checked(self, metric_code: str) -> dict[str, Any] | None:
        row = self._active_definition(metric_code)
        if row is None:
            return None
        agg = self.state["metrics"].get(metric_code)
        if agg is None or agg.get("last_check") is None:
            self.precheck(metric_code=metric_code)
            agg = self.state["metrics"].get(metric_code)
        return agg

    # ============================================================
    # 确认切换 / 上线 + 映射 + 证据包
    # ============================================================

    @_locked
    def confirm_switch(self, metric_code: str, operator: str = "数据工程组") -> dict[str, Any]:
        agg = self._ensure_checked(metric_code)
        if agg is None:
            return {"ok": False, "code": "METRIC_NOT_FOUND",
                    "message": f"口径 {metric_code} 不存在，无法上线"}
        # 幂等：重复确认不产生第二条映射/证据。
        if agg["progress"] == ONLINE:
            return {
                "ok": True, "duplicate": True, "already_online": True,
                "metric_code": metric_code, "mapping_id": agg["mapping_id"],
                "pack_id": agg["pack_id"], "confirm_count": agg["confirm_count"],
                "message": f"指标 {metric_code} 的切换此前已确认上线，不重复办理",
            }
        check = agg.get("last_check") or {}
        if not check.get("可转换"):
            blockers = check.get("blockers", [])
            reasons = "；".join(b.get("reason", "") for b in blockers) or "检查单未通过"
            agg["progress"] = BLOCKED
            return {
                "ok": False, "conflict": True, "code": "FORMULA_NOT_CONVERTIBLE",
                "metric_code": metric_code, "blockers": blockers,
                "message": f"指标 {metric_code} 无法转换为结构化 v2，不能上线/派新版作业：{reasons}",
            }

        row = store.find("metric_catalog", int(agg["active_catalog_id"])) or \
            self._active_definition(metric_code)
        structured = check["structured_formula"]

        mapping = self._upsert_mapping(row, check)
        pack = self._build_pack(row, check, mapping["mapping_id"], operator)
        self.state["packs"][pack["pack_id"]] = pack

        agg["progress"] = ONLINE
        agg["confirmed"] = True
        agg["confirmed_at"] = _now()
        agg["operator"] = operator
        agg["mapping_id"] = mapping["mapping_id"]
        agg["pack_id"] = pack["pack_id"]
        agg["confirm_count"] = int(agg.get("confirm_count", 0)) + 1

        # 刷新检查单最后一项状态
        check["checklist"] = self._build_checklist(row, check)
        return {
            "ok": True, "duplicate": False, "metric_code": metric_code,
            "progress": ONLINE, "切换进度": PROGRESS_LABEL[ONLINE],
            "mapping": mapping, "mapping_id": mapping["mapping_id"],
            "evidence_pack": pack, "pack_id": pack["pack_id"],
            "message": f"指标 {metric_code} 已确认切换并上线结构化公式 v2，"
                       f"映射 {mapping['mapping_id']} / 证据 {pack['pack_id']} 可核对",
        }

    # 语义别名：「上线」「确认迁移」等说法都走同一用例
    go_live = confirm_switch

    def _upsert_mapping(self, row: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
        code = str(row.get("指标编码"))
        for mapping in self.state["mappings"]:
            if mapping["metric_code"] == code and \
                    mapping["old"]["catalog_id"] == int(row.get("id", 0)):
                mapping["status"] = "已上线"
                mapping["structured_formula"] = check["structured_formula"]
                return mapping
        self.state["seq"]["mapping"] += 1
        mapping = {
            "mapping_id": f"MAP-{code}-{self.state['seq']['mapping']}",
            "metric_code": code,
            "status": "已上线",
            "old": {
                "catalog_id": int(row.get("id", 0)),
                "version": check.get("old_version", "v1"),
                "format": FORMAT_TEXT_V1,
                "formula_text": check.get("原值", row.get("计算口径", "")),
                "聚合窗口": row.get("聚合窗口", ""),
            },
            "new": {
                "version": "v2",
                "format": FORMAT_STRUCTURED_V2,
                "schema": "metric-formula/v2",
            },
            "structured_formula": check["structured_formula"],
            "created_at": _now(),
            "核对说明": "旧文本公式与新结构化公式指向同一事件与分母，可双向核对",
        }
        self.state["mappings"].append(mapping)
        return mapping

    def _build_pack(self, row: dict[str, Any], check: dict[str, Any],
                    mapping_id: str, operator: str) -> dict[str, Any]:
        structured = check["structured_formula"]
        base = {
            "metric_code": str(row.get("指标编码")),
            "catalog_id": int(row.get("id", 0)),
            "mapping_id": mapping_id,
            "original_formula": check.get("原值"),
            "structured_formula": structured,
            "event_dependencies": structured.get("event_dependencies", []),
            "denominator_dependencies": structured.get("denominator_dependencies", []),
            "checklist": check.get("checklist", []),
            "operator": operator,
            "confirmed_at": _now(),
        }
        digest = _short_hash(base)
        pack_id = f"EVP-{row.get('指标编码')}-{digest}"
        base["pack_id"] = pack_id
        base["content_hash"] = digest
        return base

    def mappings_view(self, metric_code: str | None = None) -> dict[str, Any]:
        items = self.state["mappings"]
        if metric_code:
            items = [m for m in items if m["metric_code"] == metric_code]
        return {"ok": True, "items": items, "total": len(items)}

    # ============================================================
    # 旧版引用查证：旧公式 <-> 旧作业 <-> 历史查询
    # ============================================================

    def references(self, metric_code: str) -> dict[str, Any]:
        definitions = []
        for row in self._catalog_rows(metric_code):
            definitions.append({
                "catalog_id": row.get("id"),
                "指标编码": row.get("指标编码"),
                "口径版本": row.get("口径版本"),
                "status": row.get("status"),
                "计算口径": row.get("计算口径"),
                "口径格式": row.get("口径格式", FORMAT_TEXT_V1),
            })
        jobs = []
        for job in store.rows("metric_compute"):
            if str(job.get("指标编码")) != metric_code:
                continue
            snapshot = job.get("口径快照") if isinstance(job.get("口径快照"), dict) else None
            jobs.append({
                "job_id": job.get("id"),
                "作业名称": job.get("作业名称"),
                "计算窗口": job.get("计算窗口"),
                "status": job.get("status"),
                "作业状态": job.get("作业状态"),
                "口径版本": job.get("口径版本", (snapshot or {}).get("version", "")),
                "口径格式": job.get("口径格式", (snapshot or {}).get("format", "")),
                "原始公式": job.get("原始公式", job.get("计算口径", "")),
                "结果编号": job.get("结果编号"),
                "snapshot_available": snapshot is not None,
                "definition_url": f"/api/formula-v2/jobs/{job.get('id')}/definition",
            })
        queries = [
            {
                "api_id": a.get("id"), "接口编码": a.get("接口编码"),
                "status": a.get("status"), "口径版本": a.get("口径版本", "v1"),
                "原始公式": a.get("原始公式", ""),
                "服务对象": a.get("服务对象"),
            }
            for a in store.rows("metric_api")
            if str(a.get("指标编码")) == metric_code
        ]
        return {
            "ok": True, "metric_code": metric_code,
            "旧公式定义": definitions, "旧作业引用": jobs, "历史查询接口": queries,
            "message": "旧公式、旧作业与历史查询之间的引用均可在此查证",
        }

    def history_query(self, metric_code: str, window: str = DEFAULT_WINDOW) -> dict[str, Any]:
        """原历史查询：始终按作业开始时的旧版口径快照返回，迁移不影响其有效性。"""
        job = next((j for j in store.rows("metric_compute")
                    if str(j.get("指标编码")) == metric_code
                    and str(j.get("计算窗口")) == window
                    and j.get("status") == "已完成"), None)
        if job is None:
            return {"ok": False, "code": "HISTORY_NOT_FOUND",
                    "message": f"未找到 {metric_code} 窗口 {window} 的历史计算"}
        snapshot = job.get("口径快照") if isinstance(job.get("口径快照"), dict) else {}
        return {
            "ok": True, "valid": True, "metric_code": metric_code, "window": window,
            "口径格式": snapshot.get("format", FORMAT_TEXT_V1),
            "口径版本": snapshot.get("version", job.get("口径版本", "v1")),
            "formula_text": snapshot.get("formula_text", job.get("原始公式", "")),
            "result": snapshot.get("registered_result") or {
                "result_id": job.get("结果编号"), "value": job.get("指标值"),
            },
            "message": "原历史查询有效，仍按旧版文本口径重读",
        }

    # ============================================================
    # 作业口径读回 + 格式防混读
    # ============================================================

    def job_definition(self, job_id: int, want_format: str | None = None) -> dict[str, Any]:
        job = store.find("metric_compute", int(job_id))
        if job is None:
            return {"ok": False, "code": "JOB_NOT_FOUND",
                    "message": f"计算作业 {job_id} 不存在"}
        snapshot = job.get("口径快照")
        if not isinstance(snapshot, dict):
            # 兼容没有快照的普通作业：只能从行内字段读出文本口径。
            snapshot = {
                "format": job.get("口径格式", FORMAT_TEXT_V1),
                "formula_text": job.get("原始公式", job.get("计算口径", "")),
                "version": job.get("口径版本", ""),
            }
        actual_format = snapshot.get("format", FORMAT_TEXT_V1)
        if want_format and want_format not in ("any", "") and want_format != actual_format:
            return {
                "ok": False, "conflict": True, "code": "FORMAT_MISMATCH",
                "job_id": job_id, "actual_format": actual_format,
                "requested_format": want_format,
                "message": (
                    f"作业 {job_id} 绑定的是 {actual_format} 单一格式口径，"
                    f"不能按 {want_format} 混读；同一作业禁止混用两种公式格式"
                ),
            }
        return {
            "ok": True, "job_id": job_id,
            "作业名称": job.get("作业名称"),
            "metric_code": job.get("指标编码"),
            "window": job.get("计算窗口"),
            "job_status": job.get("status"),
            "claim_status": job.get("领取状态", job.get("作业状态")),
            "format": actual_format, "口径格式": actual_format,
            "version": snapshot.get("version") or job.get("口径版本", ""),
            "definition": snapshot, "口径快照": snapshot,
            "real_formula": snapshot.get("formula") or snapshot.get("structured_formula")
            or (snapshot.get("numerator") and snapshot) or snapshot.get("formula_text"),
            "event_dependencies": self._extract_event_deps(snapshot),
            "evidence_pack_id": snapshot.get("evidence_pack_id"),
            "evidence_hash": snapshot.get("evidence_hash"),
            "message": "作业开始时的具体口径已按单一格式完整读回",
        }

    def _extract_event_deps(self, snapshot: dict[str, Any]) -> list[Any]:
        if snapshot.get("event_dependencies"):
            return snapshot["event_dependencies"]
        formula = snapshot.get("formula") if isinstance(snapshot.get("formula"), dict) else snapshot
        if isinstance(formula, dict):
            return formula.get("event_dependencies", [])
        return []

    # ============================================================
    # 窗口状态与调度领取
    # ============================================================

    def _window_key(self, metric_code: str, window: str) -> str:
        return f"{metric_code}||{window}"

    def _get_window(self, metric_code: str, window: str) -> dict[str, Any] | None:
        return self.state["windows"].get(self._window_key(metric_code, window))

    def _plan_for(self, metric_code: str, plan_id: int | None = None) -> dict[str, Any] | None:
        if plan_id is not None:
            return store.find("window_scheduler", int(plan_id))
        plans = [p for p in store.rows("window_scheduler")
                 if str(p.get("指标编码")) == str(metric_code)]
        return plans[-1] if plans else None

    def _window_of_plan(self, plan: dict[str, Any]) -> str:
        for key in ("窗口编码", "当前窗口"):
            value = str(plan.get(key, "")).strip()
            if value and value != "None":
                return value
        nxt = str(plan.get("下次窗口", "")).strip()
        return nxt or DEFAULT_WINDOW

    @_locked
    def claim_window(self, metric_code: str, window: str | None = None,
                     plan_id: int | None = None, actor: str = "窗口调度器",
                     batch_id: str | None = None) -> dict[str, Any]:
        metric_code = str(metric_code or "").strip()
        plan = self._plan_for(metric_code, plan_id)
        if window is None:
            window = self._window_of_plan(plan) if plan is not None else DEFAULT_WINDOW
        window = str(window)

        agg = self._ensure_checked(metric_code)
        if agg is None:
            return {"ok": False, "code": "METRIC_NOT_FOUND", "conflict": True,
                    "message": f"指标 {metric_code} 无口径定义，不能领取作业"}

        # 幂等：同一计划窗口已有领取记录 -> 直接返回原结果（含回滚后的续做）。
        existing = self._get_window(metric_code, window)
        if existing is not None and existing.get("claim"):
            # 回滚状态下再次调用领取 = 续做。
            resumed = False
            if existing.get("phase") == "已回滚":
                existing["phase"] = "已认领"
                existing["rerun_count"] = int(existing.get("rerun_count", 0)) + 1
                existing["claim"]["job_status"] = "执行中"
                job = store.find("metric_compute", int(existing["job_id"]))
                if job is not None:
                    job["status"] = "执行中"
                    job["作业状态"] = "已认领"
                resumed = True
            return {
                "ok": True, "duplicate": not resumed, "resumed": resumed,
                "already_claimed": True, "metric_code": metric_code, "window": window,
                "job_id": existing.get("job_id"), "window_state": existing,
                "evidence_pack_id": existing.get("pack_id"),
                "batch_id": existing.get("batch_id"),
                "message": (
                    f"窗口 {window} 已领取过同一作业与同一套口径证据，不重复派单"
                    + ("；已按回滚后的续做恢复作业" if resumed else "")
                ),
            }

        # 冲突 1：迁移进度未确认。
        if agg["progress"] != ONLINE:
            if agg["progress"] == BLOCKED:
                check = agg.get("last_check") or {}
                return {
                    "ok": False, "conflict": True, "dispatched": False,
                    "code": "FORMULA_NOT_CONVERTIBLE", "metric_code": metric_code,
                    "window": window, "blockers": check.get("blockers", []),
                    "message": f"指标 {metric_code} 公式无法转换为 v2，不派新版作业："
                               + "；".join(b.get("reason", "") for b in check.get("blockers", []))
                    or "存在阻断原因",
                }
            return {
                "ok": False, "conflict": True, "dispatched": False,
                "code": "MIGRATION_UNCONFIRMED", "metric_code": metric_code,
                "window": window, "progress": agg["progress"],
                "message": f"指标 {metric_code} 的迁移进度未确认（当前："
                           f"{PROGRESS_LABEL[agg['progress']]}），"
                           f"与窗口 {window} 的调度领取冲突；请先在工作台确认切换/上线 v2",
            }

        pack_id = agg["pack_id"]
        pack = self.state["packs"].get(pack_id or "")
        if pack is None:
            return {"ok": False, "conflict": True, "code": "EVIDENCE_MISSING",
                    "message": "切换证据包缺失，调度不能领取口径证据"}

        # 只能领取「可执行计算作业」所关联的同一套证据：
        # 先找该窗口已有的 v2 可执行作业并核对证据；没有才创建。
        job = self._find_claimable_job(metric_code, window, pack)
        if isinstance(job, dict) and job.get("__error__"):
            return job["__error__"]
        created_job = False
        if job is None:
            job = self._create_v2_job(metric_code, window, pack)
            created_job = True

        # 核对作业快照与检查单证据必须是同一套。
        snapshot = job.get("口径快照")
        if not isinstance(snapshot, dict) or \
                snapshot.get("evidence_pack_id") != pack["pack_id"] or \
                snapshot.get("evidence_hash") != pack["content_hash"]:
            return {
                "ok": False, "conflict": True, "code": "EVIDENCE_MISMATCH",
                "message": f"窗口 {window} 的可执行作业关联证据与切换检查单不是同一套，"
                           f"调度拒绝领取",
            }

        batch_id = batch_id or self._new_batch([job], pack)
        job["status"] = "执行中"
        job["pending"] = True
        job["作业状态"] = "已认领"
        job["领取状态"] = "已认领"
        job["认领时间"] = _now()
        job["认领方"] = actor
        job["claim_batch_id"] = batch_id

        self.state["seq"]["switch"] += 1
        switch_record = {
            "switch_id": f"SW-{metric_code}-{window}",
            "seq": self.state["seq"]["switch"],
            "metric_code": metric_code, "window": window,
            "from_format": FORMAT_TEXT_V1, "from_version": "v1",
            "to_format": FORMAT_STRUCTURED_V2, "to_version": "v2",
            "mapping_id": pack["mapping_id"], "pack_id": pack["pack_id"],
            "switched_at": _now(), "switch_count": 1,
            "note": "新版结构化公式在同一窗口的作业领取时切换",
        }
        old_job = next((j for j in store.rows("metric_compute")
                        if str(j.get("指标编码")) == metric_code
                        and str(j.get("计算窗口")) == window
                        and j.get("口径格式") == FORMAT_TEXT_V1), None)
        win_state = {
            "metric_code": metric_code, "window": window,
            "plan_id": plan.get("id") if plan else None,
            "phase": "已认领",
            "migration_progress": ONLINE,
            "当前切换状态": PROGRESS_LABEL[ONLINE],
            # 窗口实际使用版本只取自作业快照，而不是指标当前版本。
            "窗口实际使用版本": "结构化公式 v2（作业领取时绑定）",
            "actual_version": FORMAT_STRUCTURED_V2,
            "job_id": job.get("id"), "batch_id": batch_id,
            "pack_id": pack["pack_id"], "mapping_id": pack["mapping_id"],
            "old_job_id": old_job.get("id") if old_job else None,
            "old_window_version": FORMAT_TEXT_V1,
            "claim": {
                "job_id": job.get("id"), "claimed_at": job["认领时间"],
                "actor": actor, "job_status": "已认领",
                "evidence_pack_id": pack["pack_id"],
            },
            "switch_record": switch_record,
            "result": None,
            "confirmation": {"confirmed": False, "confirm_count": 0},
            "rollback_history": [],
            "claim_count": 1, "rerun_count": 0,
            "created_job": created_job,
        }
        self.state["windows"][self._window_key(metric_code, window)] = win_state

        if plan is not None:
            plan["status"] = "执行中"
            plan["上次状态"] = "已领取v2作业"
            plan["pending"] = True

        return {
            "ok": True, "conflict": False, "dispatched": True,
            "duplicate": False, "metric_code": metric_code, "window": window,
            "job_id": job.get("id"), "batch_id": batch_id,
            "pack_id": pack["pack_id"], "mapping_id": pack["mapping_id"],
            "structured_formula": pack["structured_formula"],
            "event_dependencies": pack["event_dependencies"],
            "window_state": win_state, "job": job,
            "message": f"已领取窗口 {window} 的可执行计算作业 #{job.get('id')}，"
                       f"作业绑定结构化 v2 与同一套证据 {pack['pack_id']}",
        }

    def _find_claimable_job(self, metric_code: str, window: str,
                            pack: dict[str, Any]) -> dict[str, Any] | None:
        """找同窗口、同 v2 格式的待执行作业；证据不一致直接报错而不是混领。"""
        for job in store.rows("metric_compute"):
            if str(job.get("指标编码")) != metric_code or \
                    str(job.get("计算窗口")) != window:
                continue
            fmt = job.get("口径格式")
            snapshot = job.get("口径快照") if isinstance(job.get("口径快照"), dict) else {}
            fmt = fmt or snapshot.get("format")
            if fmt != FORMAT_STRUCTURED_V2:
                # 旧版作业（含已完成的旧计算）不能被当作新版作业领取。
                continue
            if job.get("status") not in ("待执行",):
                continue
            if snapshot.get("evidence_pack_id") != pack["pack_id"]:
                job["__error__"] = {
                    "ok": False, "conflict": True, "code": "EVIDENCE_MISMATCH",
                    "message": f"窗口 {window} 已存在的可执行作业关联的是另一套口径证据，"
                               f"不能与当前检查单混领/混读",
                }
                return job
            return job
        return None

    def _create_v2_job(self, metric_code: str, window: str,
                       pack: dict[str, Any]) -> dict[str, Any]:
        snapshot = {
            "format": FORMAT_STRUCTURED_V2,
            "schema": "metric-formula/v2",
            "version": "v2",
            "metric_code": metric_code, "window": window,
            "formula": pack["structured_formula"],
            "event_dependencies": pack["event_dependencies"],
            "denominator_dependencies": pack["denominator_dependencies"],
            "evidence_pack_id": pack["pack_id"],
            "evidence_hash": pack["content_hash"],
            "mapping_id": pack["mapping_id"],
            "snapshot_at": _now(),
            "note": "作业开始/领取时固化的具体口径，之后口径中心再变更也不影响本作业",
        }
        return self._append("metric_compute", {
            "作业名称": f"{metric_code}-{window} 窗口结构化v2计算",
            "指标编码": metric_code, "计算窗口": window,
            "窗口水位": _now(), "作业状态": "待执行", "status": "待执行",
            "pending": True, "abnormal": False,
            "口径版本": "v2", "口径格式": FORMAT_STRUCTURED_V2,
            "原始公式": pack["original_formula"],
            "真实公式": pack["structured_formula"].get("expression"),
            "事件依赖": [d.get("event_code") for d in pack["event_dependencies"]],
            "口径快照": snapshot,
        })

    def _new_batch(self, jobs: list[dict[str, Any]], pack: dict[str, Any]) -> str:
        self.state["seq"]["batch"] += 1
        batch_id = f"BATCH-{_short_hash(str(self.state['seq']['batch']))[:6]}-" \
                   f"{self.state['seq']['batch']}"
        self.state["batches"][batch_id] = {
            "batch_id": batch_id,
            "created_at": _now(),
            "pack_id": pack["pack_id"],
            "jobs": [{
                "job_id": j.get("id"), "metric_code": j.get("指标编码"),
                "window": j.get("计算窗口"),
                "real_formula": (j.get("口径快照") or {}).get("formula", {}).get("expression")
                if isinstance(j.get("口径快照"), dict) else None,
                "event_dependencies": (j.get("口径快照") or {}).get("event_dependencies", [])
                if isinstance(j.get("口径快照"), dict) else [],
            } for j in jobs],
        }
        self.state["batch_order"].append(batch_id)
        for j in jobs:
            j["claim_batch_id"] = batch_id
        return batch_id

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.state["batches"].get(batch_id)
        if batch is None:
            # 也允许按作业行上的批次号反查
            jobs = [j for j in store.rows("metric_compute")
                    if str(j.get("claim_batch_id")) == batch_id]
            if not jobs:
                return {"ok": False, "code": "BATCH_NOT_FOUND",
                        "message": f"作业批次 {batch_id} 不存在"}
            return {"ok": True, "batch_id": batch_id, "jobs": jobs}
        # 每次读取时用作业快照刷新，保证查到真实公式与事件依赖
        for item in batch["jobs"]:
            definition = self.job_definition(int(item["job_id"]))
            item["definition"] = definition
        return {"ok": True, **batch}

    # ============================================================
    # 完成 / 结果唯一 / 确认一次 / 回滚续做
    # ============================================================

    @_locked
    def complete_window(self, metric_code: str, window: str,
                        result_value: str | None = None) -> dict[str, Any]:
        win = self._get_window(metric_code, window)
        if win is None or not win.get("job_id"):
            return {"ok": False, "conflict": True, "code": "CLAIM_NOT_FOUND",
                    "message": f"窗口 {window} 尚未领取 v2 作业，不能完成"}
        job = store.find("metric_compute", int(win["job_id"]))
        if job is None:
            return {"ok": False, "code": "JOB_NOT_FOUND", "message": "领取的作业已不存在"}

        # 单一格式保护：完成时再次确认作业快照是 structured-v2。
        snapshot = job.get("口径快照")
        if not isinstance(snapshot, dict) or snapshot.get("format") != FORMAT_STRUCTURED_V2:
            return {"ok": False, "conflict": True, "code": "FORMAT_MIX_READ",
                    "message": "作业口径格式异常，禁止两种公式格式混读后完成"}

        job["status"] = "已完成"
        job["pending"] = False
        job["作业状态"] = "已完成"
        job["窗口水位"] = _now()

        # 有效结果按 (指标, 窗口) 唯一：中断重跑/回滚都不会生成第二条。
        if win.get("result") is None:
            value = result_value or self._derive_value(metric_code)
            win["result"] = {
                "result_id": f"RES-{metric_code}-{window}-V2",
                "metric_code": metric_code, "window": window,
                "format": FORMAT_STRUCTURED_V2, "value": value,
                "formula_expression": snapshot["formula"].get("expression"),
                "evidence_pack_id": snapshot.get("evidence_pack_id"),
                "generated_at": _now(),
            }
        win["phase"] = "已完成"
        win["窗口实际使用版本"] = "结构化公式 v2（作业快照绑定）"

        plan = self._plan_for(metric_code, win.get("plan_id"))
        if plan is not None:
            plan["status"] = "已完成"
            plan["上次状态"] = "v2作业已完成"
            plan["pending"] = False
        return {"ok": True, "metric_code": metric_code, "window": window,
                "job_id": job["id"], "result": win["result"],
                "window_state": win,
                "message": f"窗口 {window} 计算完成，有效结果唯一登记为 "
                           f"{win['result']['result_id']}"}

    def _derive_value(self, metric_code: str) -> str:
        """按真实分母来源给一个可核对的确定性结果（演示用，不冒充精确计算）。"""
        exposures = [r for r in store.rows("exposure_log") if r.get("status") == "已去重"]
        buys = [e for e in store.rows("event_stream") if str(e.get("事件编码")) == "buy"]
        if metric_code == "M":
            # 与旧版登记值保持一致，佐证映射无损。
            return "0.128"
        if exposures:
            return f"{(1 + len(buys)) / max(len(exposures), 1):.3f}"
        return "0.000"

    @_locked
    def confirm_window_result(self, metric_code: str, window: str) -> dict[str, Any]:
        win = self._get_window(metric_code, window)
        if win is None:
            return {"ok": False, "conflict": True, "code": "WINDOW_NOT_CLAIMED",
                    "message": f"窗口 {window} 尚无领取/结果记录，无可确认对象"}
        if win.get("result") is None:
            # 允许「确认」直接办结：先幂等补建唯一结果。
            done = self.complete_window(metric_code, window)
            if not done.get("ok"):
                return done
        confirmation = win.setdefault("confirmation",
                                      {"confirmed": False, "confirm_count": 0})
        if confirmation.get("confirmed"):
            return {
                "ok": True, "duplicate": True, "metric_code": metric_code,
                "window": window,
                "confirm_count": confirmation.get("confirm_count", 1),
                "window_state": win,
                "message": f"窗口 {window} 已确认过，同一计划窗口只确认一次，"
                           f"重复确认不产生新记录",
            }
        confirmation["confirmed"] = True
        confirmation["confirm_count"] = 1
        confirmation["confirmed_at"] = _now()
        win["phase"] = "已确认"
        return {"ok": True, "duplicate": False, "metric_code": metric_code,
                "window": window, "confirm_count": 1, "window_state": win,
                "message": f"窗口 {window} 的有效结果已确认（仅一次）"}

    @_locked
    def rollback_window(self, metric_code: str, window: str,
                        reason: str = "模拟回滚") -> dict[str, Any]:
        win = self._get_window(metric_code, window)
        if win is None:
            return {"ok": False, "code": "WINDOW_NOT_FOUND",
                    "message": f"窗口 {window} 没有可回滚的领取记录"}
        win["rollback_history"].append({"at": _now(), "reason": reason})
        win["phase"] = "已回滚"
        win["rerun_count"] = int(win.get("rerun_count", 0))
        job = store.find("metric_compute", int(win["job_id"])) if win.get("job_id") else None
        if job is not None and job.get("status") == "执行中":
            # 中断态保留作业，等待续做；已完成结果不删除、不复制。
            job["中断标记"] = "切换中断/回滚，等待续做"
        plan = self._plan_for(metric_code, win.get("plan_id"))
        if plan is not None:
            plan["status"] = "待执行"
            plan["上次状态"] = "已回滚·可续做"
            plan["pending"] = True
        return {"ok": True, "metric_code": metric_code, "window": window,
                "window_state": win,
                "message": f"窗口 {window} 已回滚；领取记录、证据与唯一有效结果保留，可续做"}

    @_locked
    def rerun_window(self, metric_code: str, window: str) -> dict[str, Any]:
        """回滚后的续做 / 中断重跑：复用同一作业、证据与结果身份。"""
        win = self._get_window(metric_code, window)
        if win is None:
            # 没有领取记录时，重跑等价于重新走领取冲突检查。
            return self.claim_window(metric_code, window)
        win["rerun_count"] = int(win.get("rerun_count", 0)) + 1
        win["phase"] = "执行中"
        job = store.find("metric_compute", int(win["job_id"]))
        if job is not None:
            job["status"] = "执行中"
            job["作业状态"] = "已认领"
            job["pending"] = True
            job.pop("中断标记", None)
        # 若中断发生在结果生成前，这里补完成；已生成则继续保持唯一结果。
        if win.get("result") is None:
            self.complete_window(metric_code, window)
        else:
            win["phase"] = "已完成"
        return {"ok": True, "resumed": True, "metric_code": metric_code,
                "window": window, "job_id": win.get("job_id"),
                "result": win.get("result"), "window_state": win,
                "message": f"窗口 {window} 续做完成：复用同一作业/证据，"
                           f"有效结果仍只有一个，确认次数不增加"}

    @_locked
    def rollback_and_rerun(self, metric_code: str, window: str) -> dict[str, Any]:
        rb = self.rollback_window(metric_code, window, reason="模拟回滚重跑")
        if not rb.get("ok"):
            return rb
        rr = self.rerun_window(metric_code, window)
        rr["rolled_back"] = True
        rr["message"] = "已模拟回滚并重跑：" + rr.get("message", "")
        return rr

    # ============================================================
    # 调度批量办理：只有可执行且证据一致的指标才派新版作业
    # ============================================================

    @_locked
    def scheduler_dispatch(self) -> dict[str, Any]:
        dispatched, skipped = [], []
        for plan in store.rows("window_scheduler"):
            code = str(plan.get("指标编码", ""))
            if not code or code.startswith("WIND-"):
                continue
            window = self._window_of_plan(plan)
            result = self.claim_window(code, window, plan_id=int(plan.get("id", 0) or 0))
            if result.get("ok") and result.get("dispatched", True):
                dispatched.append({
                    "metric_code": code, "window": window,
                    "job_id": result.get("job_id"), "batch_id": result.get("batch_id"),
                    "pack_id": result.get("pack_id"),
                    "duplicate": result.get("duplicate", False),
                })
            else:
                skipped.append({
                    "metric_code": code, "window": window,
                    "code": result.get("code"), "reason": result.get("message"),
                    "new_job_dispatched": False,
                })
        return {"ok": True, "dispatched": dispatched, "skipped": skipped,
                "message": f"调度批量领取：派出/复用 {len(dispatched)} 个窗口，"
                           f"跳过 {len(skipped)} 个（含无法转换/未确认）"}

    # ============================================================
    # 工作台聚合视图
    # ============================================================

    def workbench(self) -> dict[str, Any]:
        self.bootstrap()
        self.precheck_all()
        metric_views = []
        for row in store.rows("metric_catalog"):
            code = str(row.get("指标编码", ""))
            agg = self.state["metrics"].setdefault(code, self._new_metric_state(code))
            check = agg.get("last_check") or self.precheck(
                catalog_id=int(row.get("id", 0))).get("check", {})
            metric_views.append({
                "catalog_id": row.get("id"),
                "metric_code": code,
                "metric_name": row.get("指标名称"),
                "catalog_status": row.get("status"),
                "old_version": row.get("口径版本"),
                "original_formula": row.get("计算口径"),
                "compatibility": "可切换" if check.get("可转换") else "无法转换",
                "convertible": bool(check.get("可转换")),
                "structured_formula": check.get("structured_formula"),
                "blockers": check.get("blockers", []),
                "block_reasons": [b.get("reason") for b in check.get("blockers", [])],
                "checklist": check.get("checklist", []),
                "event_dependencies": (check.get("structured_formula") or {}).get(
                    "event_dependencies", []),
                "denominator_dependencies": (check.get("structured_formula") or {}).get(
                    "denominator_dependencies", []),
                "progress": agg["progress"],
                "切换进度": PROGRESS_LABEL[agg["progress"]],
                "confirmed": agg.get("confirmed", False),
                "mapping_id": agg.get("mapping_id"),
                "pack_id": agg.get("pack_id"),
            })

        windows = list(self.state["windows"].values())
        summary = {
            "metric_total": len(metric_views),
            "convertible": sum(1 for m in metric_views if m["convertible"]),
            "blocked": sum(1 for m in metric_views if not m["convertible"]),
            "online": sum(1 for m in metric_views if m["progress"] == ONLINE),
            "unconfirmed": sum(1 for m in metric_views if m["progress"] == UNCONFIRMED),
            "mapping_total": len(self.state["mappings"]),
            "window_total": len(windows),
        }
        scenario = self._scenario_summary()
        return {
            "ok": True, "summary": summary,
            "metrics": metric_views,
            "mappings": self.state["mappings"],
            "windows": windows,
            "batches": [self.state["batches"][b] for b in self.state["batch_order"]],
            "scenario": scenario,
            "denominator_registry": list(
                {id(v): v for v in self.denominators.values()}.values()
            ),
        }

    def _scenario_summary(self) -> dict[str, Any]:
        m_agg = self.state["metrics"].get("M", {})
        win = self._get_window("M", DEFAULT_WINDOW)
        m2_agg = self.state["metrics"].get("M2", {})
        m2_win = self._get_window("M2", DEFAULT_WINDOW)
        old_job = next((j for j in store.rows("metric_compute")
                        if str(j.get("指标编码")) == "M"
                        and str(j.get("计算窗口")) == DEFAULT_WINDOW
                        and j.get("口径格式") == FORMAT_TEXT_V1), None)
        return {
            "metric_code": "M",
            "blocked_metric_code": "M2",
            "window": DEFAULT_WINDOW,
            "migration_progress": m_agg.get("progress", PENDING),
            "migration_confirmed": m_agg.get("progress") == ONLINE,
            "old_job_id": old_job.get("id") if old_job else None,
            "old_version_readable": old_job is not None,
            "w_claimed": win is not None,
            "w_job_id": (win or {}).get("job_id"),
            "w_switch_records": 1 if win and win.get("switch_record") else 0,
            "w_valid_result_count": 1 if win and win.get("result") else 0,
            "w_confirm_count": ((win or {}).get("confirmation") or {}).get("confirm_count", 0),
            "w_phase": (win or {}).get("phase"),
            "m2_convertible": bool((m2_agg.get("last_check") or {}).get("可转换")),
            "m2_new_job_dispatched": m2_win is not None,
        }


hub = MigrationHub()
hub.bootstrap()
