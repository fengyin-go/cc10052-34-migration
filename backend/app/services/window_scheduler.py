"""窗口调度器领域服务：管理指标窗口的滚动调度、粒度控制与执行状态回写。

数据链路位置：调度驱动层，按指标口径的聚合窗口驱动计算作业滚动执行，并把
每次窗口的执行结果同步给数据时效监控判定新鲜度。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class WindowSchedulerService(BaseModuleService):
    MODULE = "window_scheduler"
    ENTITY = "调度计划"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["计划名称", "指标编码", "窗口粒度", "下次窗口", "上次状态"]
    REQUIRED_FIELDS = ["计划名称", "指标编码", "窗口粒度"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "执行失败"]
    ACTION_RULES = {"立即执行": "执行中", "暂停计划": "待执行"}
    NEGATIVE_ACTIONS = []

    # ---- 结构化 v2 迁移：窗口领取/确认/回滚续做富动作 ----

    def list_entries(self, **kwargs: Any):  # type: ignore[override]
        """调度计划列表区分「当前切换状态」与「窗口实际使用版本」。"""
        from app.services.migration_hub import PROGRESS_LABEL, hub

        items, total = super().list_entries(**kwargs)
        hub.bootstrap()
        annotated = []
        for row in items:
            item = dict(row)
            code = str(row.get("指标编码", ""))
            window = hub._window_of_plan(row)  # noqa: SLF001
            win = hub._get_window(code, window)  # noqa: SLF001
            agg = hub.state["metrics"].get(code)
            if win is not None:
                item["当前切换状态"] = win.get("当前切换状态")
                item["窗口实际使用版本"] = win.get("窗口实际使用版本")
                item["窗口阶段"] = win.get("phase")
                item["确认次数"] = (win.get("confirmation") or {}).get("confirm_count", 0)
                item["有效结果"] = (win.get("result") or {}).get("result_id", "")
            elif agg is not None:
                item["当前切换状态"] = PROGRESS_LABEL.get(agg.get("progress"), agg.get("progress"))
                item["窗口实际使用版本"] = "尚未领取新版作业"
            annotated.append(item)
        return annotated, total

    CLAIM_ACTIONS = {
        "领取作业", "领取窗口作业", "领取窗口", "作业领取", "认领作业",
        "领取新版作业", "办理领取", "领取W", "领取 W", "领取窗口W", "领取窗口 W",
        "领取", "调度领取",
    }
    CONFIRM_ACTIONS = {"确认结果", "确认窗口结果", "确认W", "确认 W", "确认窗口", "确认"}
    ROLLBACK_ACTIONS = {"回滚窗口", "回滚"}
    RERUN_ACTIONS = {"重跑", "续做", "中断重跑", "回滚后的续做", "回滚后续做"}
    ROLLBACK_RERUN_ACTIONS = {
        "回滚重跑", "模拟回滚重跑", "回滚并重跑", "模拟回滚", "回滚续做",
        "模拟回滚并重跑", "回滚后重跑",
    }
    DISPATCH_ACTIONS = {"批量领取", "批量调度领取", "调度办理", "批量办理领取"}

    def handle_action(self, entry_id: int, action: str, remark: str | None = None) -> dict[str, Any]:
        from app.services.migration_hub import DEFAULT_WINDOW, hub

        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在或已归档"}
        hub.bootstrap()
        code = str(entry.get("指标编码", ""))
        window = hub._window_of_plan(entry)  # noqa: SLF001

        if action in self.CLAIM_ACTIONS:
            result = hub.claim_window(code, window, plan_id=entry_id)
            result["entry"] = entry
            return result
        if action in self.CONFIRM_ACTIONS:
            result = hub.confirm_window_result(code, window or DEFAULT_WINDOW)
            result["entry"] = entry
            return result
        if action in self.ROLLBACK_ACTIONS:
            result = hub.rollback_window(code, window or DEFAULT_WINDOW, reason=remark or "页面回滚")
            result["entry"] = entry
            return result
        if action in self.RERUN_ACTIONS:
            result = hub.rerun_window(code, window or DEFAULT_WINDOW)
            result["entry"] = entry
            return result
        if action in self.ROLLBACK_RERUN_ACTIONS:
            result = hub.rollback_and_rerun(code, window or DEFAULT_WINDOW)
            result["entry"] = entry
            return result
        if action in self.DISPATCH_ACTIONS:
            result = hub.scheduler_dispatch()
            result["entry"] = entry
            return result
        if action == "立即执行" and code and not code.startswith("WIND-"):
            # 真实指标计划：立即执行即按切换检查单办理新版作业领取。
            result = hub.claim_window(code, window, plan_id=entry_id)
            result["entry"] = entry
            return result

        # 关键词兜底：近义动作名也能命中同一用例。
        if "批量" in action and ("领" in action or "调度" in action or "办理" in action):
            result = hub.scheduler_dispatch()
            result["entry"] = entry
            return result
        if "回滚" in action and ("重跑" in action or "续做" in action or "重做" in action):
            result = hub.rollback_and_rerun(code, window or DEFAULT_WINDOW)
            result["entry"] = entry
            return result
        if "回滚" in action:
            result = hub.rollback_window(code, window or DEFAULT_WINDOW, reason=remark or "页面回滚")
            result["entry"] = entry
            return result
        if "续做" in action or "重跑" in action or "重做" in action:
            result = hub.rerun_window(code, window or DEFAULT_WINDOW)
            result["entry"] = entry
            return result
        if "确认" in action:
            result = hub.confirm_window_result(code, window or DEFAULT_WINDOW)
            result["entry"] = entry
            return result
        if "领" in action or "认领" in action:
            result = hub.claim_window(code, window, plan_id=entry_id)
            result["entry"] = entry
            return result

        row, message = self.run_action(entry_id, action)
        return {"ok": row is not None, "message": message, "entry": row}

    # ---- 业务扩展：窗口调度与状态回写 ----

    def trigger_window(self, entry_id: int) -> dict[str, Any]:
        """触发一次窗口调度，驱动指标计算作业并向时效监控回写状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待执行":
            return {"ok": False, "message": "仅待执行计划可触发"}
        metric_code = str(entry.get("指标编码", ""))
        metric = next((m for m in store.rows("metric_catalog") if m.get("指标编码") == metric_code), None)
        if metric is None or metric.get("status") != "已生效":
            entry["status"] = "执行失败"
            entry["上次状态"] = "执行失败"
            entry["abnormal"] = True
            return {"ok": False, "message": f"口径 {metric_code} 未生效，调度失败"}
        entry["status"] = "已完成"
        entry["上次状态"] = "已完成"
        entry["pending"] = False
        entry["下次窗口"] = datetime.now(timezone.utc).isoformat()
        compute_rows = store.rows("metric_compute")
        compute_rows.append({
            "id": max((int(r.get("id", 0)) for r in compute_rows), default=0) + 1,
            "作业名称": f"{entry.get('计划名称', '调度')}窗口作业",
            "指标编码": metric_code,
            "计算窗口": entry.get("窗口粒度", "日窗口"),
            "窗口水位": entry.get("下次窗口", datetime.now(timezone.utc).isoformat()),
            "作业状态": "待执行",
            "status": "待执行",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"窗口已调度，作业已创建（{entry.get('窗口粒度', '')}）"}

    def pause_plan(self, entry_id: int, reason: str) -> dict[str, Any]:
        """暂停调度计划并通知数据时效监控进入延迟观察。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "执行中":
            return {"ok": False, "message": "仅执行中计划可暂停"}
        if not reason.strip():
            return {"ok": False, "message": "暂停必须填写原因"}
        entry["status"] = "待执行"
        entry["上次状态"] = "已暂停"
        entry["pending"] = True
        entry["暂停原因"] = reason
        freshness_rows = store.rows("data_freshness")
        matched = [f for f in freshness_rows if f.get("监控对象") == f"window_scheduler:{entry_id}"]
        for row in matched:
            row["status"] = "延迟"
            row["时效等级"] = "延迟"
            row["pending"] = True
        return {"ok": True, "message": f"计划已暂停（{reason}），时效监控进入延迟观察"}

    def batch_trigger_due(self) -> dict[str, Any]:
        """批量触发全部到期窗口。"""
        due_rows = [r for r in store.rows(self.MODULE) if r.get("status") == "待执行"]
        done, failed = 0, 0
        for row in due_rows:
            if self.trigger_window(int(row.get("id", 0))).get("ok"):
                done += 1
            else:
                failed += 1
        return {"ok": True, "message": f"批量调度完成：成功 {done}，失败 {failed}"}

    def consume_from_metric_catalog(self, metric_code: str) -> list[dict[str, Any]]:
        """读取指标口径中心该口径的版本信息，校验调度口径一致性。"""
        return [
            {"口径版本": m.get("口径版本", ""), "聚合窗口": m.get("聚合窗口", ""), "状态": m.get("status", "")}
            for m in store.rows("metric_catalog")
            if m.get("指标编码") == metric_code
        ]

    def rollback_window(self, entry_id: int, to_window: str) -> dict[str, Any]:
        """回退窗口重算：把下次窗口拨回并重建计算作业。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or not to_window.strip():
            return {"ok": False, "message": "计划不存在或回退窗口为空"}
        if to_window >= str(entry.get("下次窗口", "")):
            return {"ok": False, "message": "回退窗口必须早于当前下次窗口"}
        entry["下次窗口"] = to_window
        entry["status"] = "待执行"
        entry["pending"] = True
        entry["回退标记"] = to_window
        return {"ok": True, "message": f"窗口已回退到 {to_window}，等待重算"}

    def produce_to_anomaly_metric(self, entry_id: int) -> dict[str, Any]:
        """把连续执行失败的计划登记为指标异常事件。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or str(entry.get("上次状态", "")) != "执行失败":
            return {"ok": False, "message": "计划无连续失败记录"}
        anomaly_rows = store.rows("anomaly_metric")
        anomaly_rows.append({
            "id": max((int(r.get("id", 0)) for r in anomaly_rows), default=0) + 1,
            "异常编号": f"AN-WS-{entry_id}",
            "指标编码": entry.get("指标编码", ""),
            "基线区间": entry.get("窗口粒度", ""),
            "观测值": "调度连续失败",
            "异常评分": "0.8",
            "status": "待确认",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": "调度失败已登记为异常事件"}

    def export_plan_timeline(self, metric_code: str) -> list[dict[str, Any]]:
        """导出指标的调度时间线，供指标查询服务展示刷新策略。"""
        return [
            {
                "计划名称": r.get("计划名称", ""),
                "窗口粒度": r.get("窗口粒度", ""),
                "下次窗口": r.get("下次窗口", ""),
                "上次状态": r.get("上次状态", ""),
            }
            for r in store.rows(self.MODULE) if r.get("指标编码") == metric_code
        ]
