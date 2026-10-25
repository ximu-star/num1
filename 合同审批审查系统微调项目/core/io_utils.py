"""JSONL / JSON 读写与断点续跑工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"找不到数据文件: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} 不是合法 JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} 必须是 JSON 对象")
        rows.append(row)
    return rows


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """原子写入 JSONL，避免中断后留下半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
    temporary.replace(path)


def load_existing_ids(path: Path, id_field: str = "sample_id") -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"断点文件 {path}:{line_number} 不是合法 JSON") from exc
        value = row.get(id_field) if isinstance(row, dict) else None
        if not isinstance(value, str) or not value or value in ids:
            raise ValueError(f"断点文件 {path}:{line_number} 的 {id_field} 无效或重复")
        ids.add(value)
    return ids


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        raise ValueError("chunk size 必须大于 0")
    return [items[index : index + size] for index in range(0, len(items), size)]


def compact_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
