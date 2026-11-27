"""指标口径中心领域服务：管理实验指标的计算口径、聚合窗口、版本生效与废弃。

数据链路位置：口径治理层，是指标计算作业的唯一口径来源，负责把生效口径
下发给计算作业与指标查询服务，并阻断对已废弃口径的引用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class MetricCatalogService(BaseModuleService):
    MODULE = "metric_catalog"
    ENTITY = "指标定义"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["指标编码", "指标名称", "计算口径", "聚合窗口", "口径版本"]
    REQUIRED_FIELDS = ["指标编码", "指标名称", "计算口径"]
    STATUS_ORDER = ["草稿", "已生效", "已废弃"]
    ACTION_RULES = {"生效口径": "已生效", "废弃口径": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ---- 结构化公式 v2 迁移动作（富协议，供统一动作路由调用） ----

    def list_entries(self, **kwargs: Any):  # type: ignore[override]
        """列表上直接带兼容结果与切换进度，方便资源接口与页面表格查证。"""
        from app.services.migration_hub import PROGRESS_LABEL, hub

        items, total = super().list_entries(**kwargs)
        hub.bootstrap()
        annotated = []
        for row in items:
            item = dict(row)
            code = str(row.get("指标编码", ""))
            agg = hub.state["metrics"].get(code)
            if agg is None or agg.get("last_check") is None:
                result = hub.precheck(metric_code=code, catalog_id=int(row.get("id", 0)))
                check = result.get("check") or {}
                agg = hub.state["metrics"].get(code, {})
            else:
                check = agg.get("last_check") or {}
            item["可切换"] = bool(check.get("可转换"))
            item["兼容结果"] = "可切换" if check.get("可转换") else "无法转换"
            item["阻断原因"] = [b.get("reason") for b in check.get("blockers", [])]
            item["结构化公式v2"] = check.get("structured_formula")
            progress = agg.get("progress", "UNCHECKED")
            item["切换进度"] = PROGRESS_LABEL.get(progress, progress)
            item["mapping_id"] = agg.get("mapping_id")
            item["evidence_pack_id"] = agg.get("pack_id")
            annotated.append(item)
        return annotated, total

    PRECHECK_ACTIONS = {
        "预查公式", "公式预查", "预检查", "兼容预查", "预查",
        "生成检查单", "切换检查单", "检查公式兼容性", "预检查公式",
        "公式迁移", "迁移公式", "迁移到v2", "迁移到V2", "结构化迁移",
        "兼容性检查",
    }
    CONFIRM_ACTIONS = {
        "确认切换", "确认迁移", "确认并上线", "确认上线", "确认切换并上线", "上线",
        "上线v2", "上线V2", "上线结构化公式", "迁移上线", "切换上线",
    }
    MAPPING_ACTIONS = {"查看映射", "新旧映射", "版本映射", "映射核对"}
    REFERENCE_ACTIONS = {"引用查证", "查看引用", "旧版引用", "查证引用"}

    def handle_action(self, entry_id: int, action: str, remark: str | None = None) -> dict[str, Any]:
        """统一动作入口：迁移动作走 hub，其余动作保留基类状态流转。"""
        # 延迟导入避免模块初始化环。
        from app.services.migration_hub import hub

        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在或已归档"}
        code = str(entry.get("指标编码", ""))
        hub.bootstrap()

        if action in self.PRECHECK_ACTIONS:
            result = hub.precheck(metric_code=code, catalog_id=entry_id)
            result["entry"] = entry
            result.setdefault("message", "预查完成")
            return result
        if action in self.CONFIRM_ACTIONS:
            result = hub.confirm_switch(code, operator=(remark or "数据工程组"))
            result["entry"] = entry
            return result
        if action in self.MAPPING_ACTIONS:
            result = hub.mappings_view(code)
            result["ok"] = True
            result["entry"] = entry
            result["message"] = f"指标 {code} 的新旧版本映射共 {result.get('total', 0)} 条"
            return result
        if action in self.REFERENCE_ACTIONS:
            result = hub.references(code)
            result["entry"] = entry
            return result

        # 关键词兜底：允许近义动作名仍命中同一用例。
        if any(k in action for k in ("引用", "旧公式", "旧作业")):
            result = hub.references(code)
            result["entry"] = entry
            return result
        if "映射" in action:
            result = hub.mappings_view(code)
            result["ok"] = True
            result["entry"] = entry
            result["message"] = f"指标 {code} 的新旧版本映射共 {result.get('total', 0)} 条"
            return result
        if any(k in action for k in ("确认", "上线")) and "预查" not in action:
            result = hub.confirm_switch(code, operator=(remark or "数据工程组"))
            result["entry"] = entry
            return result
        if any(k in action for k in ("预查", "兼容", "检查单", "转换", "解析", "迁移", "结构化", "v2", "V2")):
            result = hub.precheck(metric_code=code, catalog_id=entry_id)
            result["entry"] = entry
            result.setdefault("message", "预查完成")
            return result

        row, message = self.run_action(entry_id, action)
        return {"ok": row is not None, "message": message, "entry": row}

    # ---- 业务扩展：口径治理与版本下发 ----

    def activate_metric(self, entry_id: int) -> dict[str, Any]:
        """生效指标口径并自动创建指标计算作业。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "草稿":
            return {"ok": False, "message": "仅草稿口径可生效"}
        entry["status"] = "已生效"
        entry["pending"] = False
        compute_rows = store.rows("metric_compute")
        compute_rows.append({
            "id": max((int(r.get("id", 0)) for r in compute_rows), default=0) + 1,
            "作业名称": f"{entry.get('指标名称', '指标')}日作业",
            "指标编码": entry.get("指标编码", ""),
            "计算窗口": entry.get("聚合窗口", "日窗口"),
            "窗口水位": datetime.now(timezone.utc).isoformat(),
            "作业状态": "待执行",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"口径 {entry.get('指标编码')} 已生效并创建计算作业"}

    def deprecate_metric(self, entry_id: int) -> dict[str, Any]:
        """废弃指标口径并联动下线引用该口径的查询接口。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "口径不存在"}
        metric_code = str(entry.get("指标编码", ""))
        entry["status"] = "已废弃"
        entry["pending"] = False
        api_rows = [
            a for a in store.rows("metric_api")
            if a.get("指标编码") == metric_code and a.get("status") == "已上线"
        ]
        for api in api_rows:
            api["status"] = "已下线"
            api["pending"] = False
        return {"ok": True, "message": f"口径已废弃，联动下线 {len(api_rows)} 个查询接口"}

    def new_version(self, entry_id: int, new_formula: str) -> dict[str, Any]:
        """基于当前口径升版本，保留历史版本供追溯。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or not new_formula.strip():
            return {"ok": False, "message": "口径不存在或新公式为空"}
        old_version = str(entry.get("口径版本", "v1"))
        version_num = int(old_version.lstrip("v")) if old_version.lstrip("v").isdigit() else 1
        rows = store.rows(self.MODULE)
        rows.append({
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "指标编码": entry.get("指标编码", ""),
            "指标名称": entry.get("指标名称", ""),
            "计算口径": new_formula,
            "聚合窗口": entry.get("聚合窗口", "日窗口"),
            "口径版本": f"v{version_num + 1}",
            "status": "草稿",
            "pending": True,
            "abnormal": False,
        })
        entry["status"] = "已废弃"
        entry["pending"] = False
        return {"ok": True, "message": f"新版本 v{version_num + 1} 已创建，旧版本 {old_version} 归档"}

    def consume_from_event_stream(self) -> list[dict[str, Any]]:
        """读取事件接入层已验证事件，生成口径候选建议。"""
        suggestions = []
        for evt in store.rows("event_stream"):
            if evt.get("status") != "已验证":
                continue
            code = str(evt.get("事件编码", ""))
            if not code:
                continue
            suggestions.append({
                "建议指标编码": f"M-{code}",
                "建议口径": f"count({code})/曝光量",
                "来源事件": evt.get("事件名称", ""),
            })
        return suggestions

    def produce_to_window_scheduler(self, entry_id: int) -> dict[str, Any]:
        """把生效口径注册到窗口调度器，建立滚动计算计划。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "已生效":
            return {"ok": False, "message": "仅生效口径可注册调度"}
        scheduler_rows = store.rows("window_scheduler")
        scheduler_rows.append({
            "id": max((int(r.get("id", 0)) for r in scheduler_rows), default=0) + 1,
            "计划名称": f"{entry.get('指标名称', '指标')}滚动计划",
            "指标编码": entry.get("指标编码", ""),
            "窗口粒度": entry.get("聚合窗口", "日窗口"),
            "下次窗口": datetime.now(timezone.utc).isoformat(),
            "上次状态": "待执行",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"调度计划已创建（{entry.get('聚合窗口', '日窗口')}）"}

    def list_effective_metrics(self) -> list[dict[str, Any]]:
        """列出全部生效口径，供指标查询服务上线接口引用。"""
        return [
            {
                "指标编码": r.get("指标编码", ""),
                "指标名称": r.get("指标名称", ""),
                "计算口径": r.get("计算口径", ""),
                "口径版本": r.get("口径版本", ""),
            }
            for r in store.rows(self.MODULE) if r.get("status") == "已生效"
        ]

    def check_formula_usage(self, formula_keyword: str) -> list[dict[str, Any]]:
        """检索包含关键字的口径，供指标异常检测定位异常口径。"""
        if not formula_keyword.strip():
            return []
        return [
            {"指标编码": r.get("指标编码", ""), "计算口径": r.get("计算口径", ""), "状态": r.get("status", "")}
            for r in store.rows(self.MODULE)
            if formula_keyword in str(r.get("计算口径", ""))
        ]
