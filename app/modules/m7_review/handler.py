"""复盘入口：复盘卡、单卡证据、敏感性扫描、卡片导出。

参数先校验再算：窗口与阈值越界给 400 E_BAD_AXIS，不静默取默认值。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app import errors
from app.modules.m7_review import cards_csv, service
from app.web import state

router = APIRouter()


@router.get("/api/review")
def get_review(request: Request, run_id: str = Query(default="")):
    app_state = state(request)
    result = service.review(app_state.settings, app_state.store, run_id or None)
    if not result:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    if result.get("empty"):
        raise errors.BadInput("当前批次没有有效指标点", code="E_NO_POINTS")
    return result


@router.get("/api/review/cards.csv")
def get_cards_csv(request: Request, run_id: str = Query(default="")):
    app_state = state(request)
    result = service.review(app_state.settings, app_state.store, run_id or None)
    if not result:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    body = cards_csv.cards_csv(result.get("cards", []))
    return PlainTextResponse(
        body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="review-{result["run_id"]}.csv"'})


@router.get("/api/review/sensitivity")
def get_sensitivity(request: Request,
                    window: str = Query(default="28"),
                    threshold: str = Query(default="3.5"),
                    run_id: str = Query(default="")):
    window_value, threshold_value = service.parse_axis(window, threshold)
    app_state = state(request)
    result = service.sensitivity_point(app_state.settings, app_state.store,
                                       run_id or None, window_value, threshold_value)
    if not result:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    if result.get("empty"):
        raise errors.BadInput("当前批次没有有效指标点", code="E_NO_POINTS")
    return result


@router.get("/api/review/{event_id}")
def get_card(request: Request, event_id: str, run_id: str = Query(default="")):
    app_state = state(request)
    result = service.review(app_state.settings, app_state.store, run_id or None)
    if not result:
        raise errors.NotFound("还没有运行记录", code="E_NO_RUN")
    for card in result.get("cards", []):
        if card["event_id"] == event_id:
            return card
    raise errors.NotFound("复盘卡不存在：" + event_id, code="E_CARD_NOT_FOUND")
