"""语义路径兼容层：把迁移能力也挂到各业务模块命名空间下。

统一资源路由只支持 /api/{module}/{id}/actions；这里补充少量按业务语义
命名的直连接口，注册顺序在资源路由之前，避免被整型 entry_id 匹配拦截。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.migration_hub import DEFAULT_WINDOW, hub

router = APIRouter(prefix="/api", tags=["迁移语义路径兼容"])


class _Code(BaseModel):
    metric_code: str | None = None
    code: str | None = None
    window: str | None = DEFAULT_WINDOW
    reason: str | None = None


def _code_of(payload: _Code) -> str:
    return str(payload.metric_code or payload.code or "M")


# ---- 口径中心命名空间 ----

@router.get("/metric_catalog/precheck")
def catalog_precheck_get(metric_code: str | None = None) -> dict[str, Any]:
    hub.bootstrap()
    if metric_code:
        return hub.precheck(metric_code=metric_code)
    return hub.precheck_all()


@router.post("/metric_catalog/precheck")
def catalog_precheck_post(payload: _Code) -> dict[str, Any]:
    hub.bootstrap()
    code = payload.metric_code or payload.code
    if code:
        return hub.precheck(metric_code=code)
    return hub.precheck_all()


@router.get("/metric_catalog/migration-workbench")
def catalog_workbench() -> dict[str, Any]:
    return hub.workbench()


@router.post("/metric_catalog/{metric_code}/confirm-switch")
def catalog_confirm(metric_code: str) -> dict[str, Any]:
    return hub.confirm_switch(metric_code, operator="语义接口")


# ---- 窗口调度命名空间 ----

@router.post("/window_scheduler/claim")
def scheduler_claim(payload: _Code) -> dict[str, Any]:
    return hub.claim_window(_code_of(payload), payload.window or DEFAULT_WINDOW)


@router.post("/window_scheduler/rollback-rerun")
def scheduler_rollback_rerun(payload: _Code) -> dict[str, Any]:
    return hub.rollback_and_rerun(_code_of(payload), payload.window or DEFAULT_WINDOW)


@router.post("/window_scheduler/dispatch")
def scheduler_dispatch() -> dict[str, Any]:
    return hub.scheduler_dispatch()


# ---- 计算作业命名空间：口径读回（含格式防混读） ----

@router.get("/metric_compute/{job_id}/definition")
def compute_definition(job_id: int, format: str | None = None) -> dict[str, Any]:  # noqa: A002
    return hub.job_definition(job_id, want_format=format)
