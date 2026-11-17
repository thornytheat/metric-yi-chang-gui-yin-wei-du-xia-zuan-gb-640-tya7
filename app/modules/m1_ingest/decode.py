"""编码回退链：BOM → UTF-8 → GB18030。

声明式编码（HTTP 头或 BOM）只作为首个候选，严格解码失败立刻回退，
回退与剥离都留下原因码，绝不静默替换字符。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app import reasons
from app.errors import BadInput

BOM = b"\xef\xbb\xbf"
CHAIN = ("utf-8", "gb18030")


@dataclass
class Decoded:
    text: str
    encoding: str
    bom_stripped: bool = False
    fallback: bool = False
    notices: list[dict] = field(default_factory=list)


def _notice(code: str, detail: str) -> dict:
    return {"code": code, "label": reasons.label(code), "detail": detail}


def decode_bytes(data: bytes) -> Decoded:
    if not data:
        raise BadInput("文件为空", code="E_EMPTY_FILE")
    bom = data.startswith(BOM)
    body = data[len(BOM):] if bom else data
    notices: list[dict] = []
    if bom:
        notices.append(_notice("N_BOM_STRIPPED", "表头首列 BOM 已剥离"))
    for index, encoding in enumerate(CHAIN):
        try:
            text = body.decode(encoding)
        except UnicodeDecodeError:
            continue
        fallback = index > 0
        if fallback:
            notices.append(_notice("N_CHARSET_FALLBACK", "按 UTF-8 严格解码失败，回退 " + encoding))
        return Decoded(text=text, encoding=("utf-8-sig" if bom else encoding),
                       bom_stripped=bom, fallback=fallback, notices=notices)
    raise BadInput("无法按任何编码解码（utf-8 / gb18030 均失败）", code="E_DECODE_FAILED")
