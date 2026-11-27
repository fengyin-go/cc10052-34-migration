"""指标计算作业领域服务：管理实验指标的计算窗口、窗口水位推进与作业重试。

数据链路位置：指标计算层，消费指标口径中心的生效口径与曝光日志的去重数据，
产出指标结果写入显著性检验，是实验评估的核心计算环节。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class MetricComputeService(BaseModuleService):
    MODULE = "metric_compute"
    ENTITY = "计算作业"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["作业名称", "指标编码", "计算窗口", "窗口水位", "作业状态"]
    REQUIRED_FIELDS = ["作业名称", "指标编码", "计算窗口"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "计算失败"]
    ACTION_RULES = {"启动作业": "执行中", "重试计算": "计算失败"}
    NEGATIVE_ACTIONS = []

    # ---- 口径快照读回 / 领取 / 确认 富动作 ----

    DEFINITION_ACTIONS = {"读回口径", "查看口径", "口径快照", "查看具体口径",
                          "读回开始口径", "查看作业口径"}
    CLAIM_ACTIONS = {"领取作业", "认领作业", "领取"}
    CONFIRM_ACTIONS = {"确认结果", "确认"}

    def handle_action(self, entry_id: int, action: str, remark: str | None = None) -> dict[str, Any]:
        from app.services.migration_hub import hub

        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在或已归档"}
        hub.bootstrap()
        code = str(entry.get("指标编码", ""))
        window = str(entry.get("计算窗口", ""))

        if action in self.DEFINITION_ACTIONS:
            result = hub.job_definition(entry_id)
            result["entry"] = entry
            return result
        if action in self.CLAIM_ACTIONS:
            result = hub.claim_window(code, window)
            result["entry"] = entry
            return result
        if action in self.CONFIRM_ACTIONS:
            result = hub.confirm_window_result(code, window)
            result["entry"] = entry
            return result
        if action == "启动作业":
            # v2 作业走窗口完成用例，保证结果唯一与格式不混读；
            # 其它作业保留原有 start_job 行为。
            snapshot = entry.get("口径快照")
            if isinstance(snapshot, dict) and snapshot.get("format") == "structured-v2":
                result = hub.complete_window(code, window)
                result["entry"] = entry
                return result
            legacy = self.start_job(entry_id)
            return {"ok": bool(legacy.get("ok")), "message": legacy.get("message", ""),
                    "entry": store.find(self.MODULE, entry_id)}

        # 关键词兜底：近义动作名命中同一用例。
        if any(k in action for k in ("口径", "快照", "定义", "公式")):
            result = hub.job_definition(entry_id)
            result["entry"] = entry
            return result
        if "确认" in action:
            result = hub.confirm_window_result(code, window)
            result["entry"] = entry
            return result
        if "领" in action or "认领" in action:
            result = hub.claim_window(code, window)
            result["entry"] = entry
            return result
        if any(k in action for k in ("启动", "开始", "执行作业", "运行")):
            snapshot = entry.get("口径快照")
            if isinstance(snapshot, dict) and snapshot.get("format") == "structured-v2":
                result = hub.complete_window(code, window)
                result["entry"] = entry
                return result
            legacy = self.start_job(entry_id)
            return {"ok": bool(legacy.get("ok")), "message": legacy.get("message", ""),
                    "entry": store.find(self.MODULE, entry_id)}

        row, message = self.run_action(entry_id, action)
        return {"ok": row is not None, "message": message, "entry": row}

    # ---- 业务扩展：窗口计算与结果下发 ----

    def start_job(self, entry_id: int) -> dict[str, Any]:
        """启动计算作业，推进窗口水位并在完成后写入显著性检验批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待执行":
            return {"ok": False, "message": "仅待执行作业可启动"}
        metric = next(
            (m for m in store.rows("metric_catalog") if m.get("指标编码") == entry.get("指标编码")),
            None,
        )
        if metric is None or metric.get("status") != "已生效":
            return {"ok": False, "message": f"口径 {entry.get('指标编码')} 未生效，禁止计算"}
        entry["status"] = "已完成"
        entry["pending"] = False
        entry["作业状态"] = "已完成"
        entry["窗口水位"] = datetime.now(timezone.utc).isoformat()
        test_rows = store.rows("significance_test")
        test_rows.append({
            "id": max((int(r.get("id", 0)) for r in test_rows), default=0) + 1,
            "检验批次": f"ST-{entry_id}-{datetime.now(timezone.utc).timestamp():.0f}",
            "实验编码": f"EXP_-000{entry_id}",
            "指标编码": entry.get("指标编码", ""),
            "P值": "0.031",
            "置信区间": "[-0.021, 0.045]",
            "status": "待检验",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"作业 {entry_id} 计算完成并登记检验批次"}

    def retry_failed_job(self, entry_id: int) -> dict[str, Any]:
        """重试计算失败作业，重算窗口并登记异常事件。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "计算失败":
            return {"ok": False, "message": "仅计算失败作业可重试"}
        entry["status"] = "执行中"
        entry["pending"] = True
        entry["作业状态"] = "执行中"
        entry["重试次数"] = str(int(entry.get("重试次数", "0") or 0) + 1)
        anomaly_rows = store.rows("anomaly_metric")
        anomaly_rows.append({
            "id": max((int(r.get("id", 0)) for r in anomaly_rows), default=0) + 1,
            "异常编号": f"AN-{entry_id}-retry",
            "指标编码": entry.get("指标编码", ""),
            "基线区间": entry.get("计算窗口", ""),
            "观测值": "作业重试",
            "异常评分": "0.5",
            "status": "待确认",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"作业 {entry_id} 已进入重试（第 {entry['重试次数']} 次）"}

    def advance_watermark(self, entry_id: int, new_watermark: str) -> dict[str, Any]:
        """推进窗口水位并同步数据时效监控的最新水位。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or not new_watermark.strip():
            return {"ok": False, "message": "作业不存在或水位为空"}
        old = str(entry.get("窗口水位", ""))
        if new_watermark <= old:
            return {"ok": False, "message": "新水位必须晚于当前水位"}
        entry["窗口水位"] = new_watermark
        freshness_rows = store.rows("data_freshness")
        matched = [f for f in freshness_rows if f.get("监控对象") == f"metric_compute:{entry_id}"]
        if matched:
            matched[-1]["最新水位"] = new_watermark
            matched[-1]["status"] = "新鲜"
            matched[-1]["时效等级"] = "新鲜"
        else:
            freshness_rows.append({
                "id": max((int(r.get("id", 0)) for r in freshness_rows), default=0) + 1,
                "监控对象": f"metric_compute:{entry_id}",
                "数据域": "指标计算",
                "最新水位": new_watermark,
                "延迟分钟": "0",
                "时效等级": "新鲜",
                "status": "新鲜",
                "pending": True,
                "abnormal": False,
            })
        return {"ok": True, "message": f"水位已推进到 {new_watermark}"}

    def batch_execute_pending(self) -> dict[str, Any]:
        """批量执行全部待执行作业并汇总结果。"""
        pending_rows = [r for r in store.rows(self.MODULE) if r.get("status") == "待执行"]
        done, failed = 0, 0
        for row in pending_rows:
            result = self.start_job(int(row.get("id", 0)))
            if result.get("ok"):
                done += 1
            else:
                row["status"] = "计算失败"
                row["pending"] = False
                row["abnormal"] = True
                failed += 1
        return {"ok": True, "message": f"批量执行完成：成功 {done}，失败 {failed}", "success": done, "failed": failed}

    def consume_from_window_scheduler(self, metric_code: str) -> list[dict[str, Any]]:
        """读取窗口调度器中该指标的活跃计划，返回调度约束。"""
        return [
            {
                "计划名称": s.get("计划名称", ""),
                "窗口粒度": s.get("窗口粒度", ""),
                "下次窗口": s.get("下次窗口", ""),
            }
            for s in store.rows("window_scheduler")
            if s.get("指标编码") == metric_code and s.get("status") in ("待执行", "执行中")
        ]

    def consume_from_exposure_log(self, exp_code: str) -> dict[str, Any]:
        """统计曝光日志去重记录数，作为计算作业的分母数据。"""
        rows = [
            r for r in store.rows("exposure_log")
            if r.get("实验编码") == exp_code and r.get("status") == "已去重"
        ]
        return {
            "实验编码": exp_code,
            "去重曝光数": len(rows),
            "分组分布": {
                group: sum(1 for r in rows if r.get("分组名称") == group)
                for group in ("对照组", "实验组")
            },
        }

    def export_job_results(self, metric_code: str) -> list[dict[str, Any]]:
        """导出指定指标的完成作业清单，供报告生成器编排数据窗口。"""
        return [
            {
                "作业名称": r.get("作业名称", ""),
                "计算窗口": r.get("计算窗口", ""),
                "窗口水位": r.get("窗口水位", ""),
            }
            for r in store.rows(self.MODULE)
            if r.get("指标编码") == metric_code and r.get("status") == "已完成"
        ]
