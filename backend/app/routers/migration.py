"""公式迁移工作台 HTTP 接口。

同一组接口挂在多个语义前缀下（/api/formula-v2、/api/migration、
/api/formula-migration），方便不同调用方发现；领域逻辑全部在 migration_hub。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.migration_hub import DEFAULT_WINDOW, hub

router = APIRouter(tags=["公式结构化v2迁移"])


class CodePayload(BaseModel):
    metric_code: str | None = None
    catalog_id: int | None = None
    operator: str | None = None


class WindowPayload(BaseModel):
    metric_code: str = "M"
    window: str | None = DEFAULT_WINDOW
    plan_id: int | None = None
    reason: str | None = None
    result_value: str | None = None
    actor: str | None = None


class JobDefinitionQuery(BaseModel):
    format: str | None = None


@router.get("/workbench")
def workbench() -> dict[str, Any]:
    return hub.workbench()


@router.get("/scenario")
def scenario() -> dict[str, Any]:
    return hub.workbench()["scenario"]


@router.post("/testing/reset")
def reset_scenario() -> dict[str, Any]:
    return hub.reset_scenario()


# ---- 预查 / 检查单 ----

@router.get("/precheck")
def precheck_list(metric_code: str | None = None) -> dict[str, Any]:
    if metric_code:
        return hub.precheck(metric_code=metric_code)
    return hub.precheck_all()


@router.post("/precheck")
def precheck_post(payload: CodePayload) -> dict[str, Any]:
    if payload.metric_code or payload.catalog_id is not None:
        return hub.precheck(metric_code=payload.metric_code, catalog_id=payload.catalog_id)
    return hub.precheck_all()


@router.get("/checklist/{metric_code}")
def checklist(metric_code: str) -> dict[str, Any]:
    return hub.checklist(metric_code)


# ---- 确认切换 / 上线 ----

@router.post("/metrics/{metric_code}/confirm")
def confirm_metric(metric_code: str, payload: CodePayload | None = None) -> dict[str, Any]:
    operator = (payload.operator if payload else None) or "数据工程组"
    return hub.confirm_switch(metric_code, operator=operator)


@router.post("/metrics/{metric_code}/go-live")
def go_live_metric(metric_code: str, payload: CodePayload | None = None) -> dict[str, Any]:
    operator = (payload.operator if payload else None) or "数据工程组"
    return hub.confirm_switch(metric_code, operator=operator)


@router.post("/confirm")
def confirm_body(payload: CodePayload) -> dict[str, Any]:
    if not payload.metric_code:
        return {"ok": False, "message": "缺少 metric_code"}
    return hub.confirm_switch(payload.metric_code, operator=payload.operator or "数据工程组")


# ---- 映射 / 引用 / 历史查询 ----

@router.get("/mappings")
def mappings(metric_code: str | None = None) -> dict[str, Any]:
    return hub.mappings_view(metric_code)


@router.get("/references/{metric_code}")
def references(metric_code: str) -> dict[str, Any]:
    return hub.references(metric_code)


@router.get("/old-formula/{metric_code}")
def old_formula(metric_code: str) -> dict[str, Any]:
    return hub.references(metric_code)


@router.get("/history/query")
def history_query(metric_code: str, window: str = DEFAULT_WINDOW) -> dict[str, Any]:
    return hub.history_query(metric_code, window)


# ---- 作业口径读回（防混读） ----

@router.get("/jobs/{job_id}/definition")
def job_definition(job_id: int, format: str | None = None) -> dict[str, Any]:  # noqa: A002
    return hub.job_definition(job_id, want_format=format)


# ---- 窗口领取 / 完成 / 确认 / 回滚续做 ----

@router.get("/windows")
def windows(metric_code: str | None = None) -> dict[str, Any]:
    items = list(hub.state["windows"].values())
    if metric_code:
        items = [w for w in items if w["metric_code"] == metric_code]
    return {"ok": True, "items": items, "total": len(items)}


@router.post("/windows/claim")
def claim_window(payload: WindowPayload) -> dict[str, Any]:
    return hub.claim_window(
        payload.metric_code, payload.window, plan_id=payload.plan_id,
        actor=payload.actor or "窗口调度器")


@router.post("/windows/complete")
def complete_window(payload: WindowPayload) -> dict[str, Any]:
    return hub.complete_window(payload.metric_code,
                               payload.window or DEFAULT_WINDOW,
                               result_value=payload.result_value)


@router.post("/windows/confirm")
def confirm_window(payload: WindowPayload) -> dict[str, Any]:
    return hub.confirm_window_result(payload.metric_code,
                                     payload.window or DEFAULT_WINDOW)


@router.post("/windows/rollback")
def rollback_window(payload: WindowPayload) -> dict[str, Any]:
    return hub.rollback_window(payload.metric_code,
                               payload.window or DEFAULT_WINDOW,
                               reason=payload.reason or "模拟回滚")


@router.post("/windows/rerun")
def rerun_window(payload: WindowPayload) -> dict[str, Any]:
    return hub.rerun_window(payload.metric_code, payload.window or DEFAULT_WINDOW)


@router.post("/windows/rollback-rerun")
def rollback_rerun(payload: WindowPayload) -> dict[str, Any]:
    return hub.rollback_and_rerun(payload.metric_code,
                                  payload.window or DEFAULT_WINDOW)


# 以指标编码+窗口放在路径上的语义化版本，方便 GET 风格调用与页面拼接
@router.post("/metrics/{metric_code}/windows/{window}/claim")
def claim_window_path(metric_code: str, window: str) -> dict[str, Any]:
    return hub.claim_window(metric_code, window)


@router.post("/metrics/{metric_code}/windows/{window}/confirm")
def confirm_window_path(metric_code: str, window: str) -> dict[str, Any]:
    return hub.confirm_window_result(metric_code, window)


@router.post("/metrics/{metric_code}/windows/{window}/rollback-rerun")
def rollback_rerun_path(metric_code: str, window: str) -> dict[str, Any]:
    return hub.rollback_and_rerun(metric_code, window)


# ---- 调度批量办理 / 批次证据 ----

@router.post("/scheduler/dispatch")
def scheduler_dispatch() -> dict[str, Any]:
    return hub.scheduler_dispatch()


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: str) -> dict[str, Any]:
    return hub.get_batch(batch_id)


@router.get("/evidence/{pack_id}")
def evidence_pack(pack_id: str) -> dict[str, Any]:
    pack = hub.state["packs"].get(pack_id)
    if pack is None:
        return {"ok": False, "code": "EVIDENCE_NOT_FOUND", "message": f"证据包 {pack_id} 不存在"}
    return {"ok": True, "evidence_pack": pack}
