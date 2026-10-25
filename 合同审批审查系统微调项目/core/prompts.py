"""Prompt 读取与渲染。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config as C

PROMPT_DIR = C.ROOT / "prompts"

SECTIONS = {
    "review": ("review_generation.md", "REVIEW_NARRATIVE_PROMPT_START", "REVIEW_NARRATIVE_PROMPT_END"),
    "semantic": ("semantic_generation.md", "SEMANTIC_NARRATIVE_PROMPT_START", "SEMANTIC_NARRATIVE_PROMPT_END"),
    "dpo_judge": ("dpo_judge.md", "DPO_JUDGE_PROMPT_START", "DPO_JUDGE_PROMPT_END"),
    "variant": ("variant_generation.md", "VARIANT_GENERATION_PROMPT_START", "VARIANT_GENERATION_PROMPT_END"),
}


def load_section(name: str, prompt_dir: Path = PROMPT_DIR) -> str:
    filename, start_marker, end_marker = SECTIONS[name]
    text = (prompt_dir / filename).read_text(encoding="utf-8")
    start = f"<!-- {start_marker} -->"
    end = f"<!-- {end_marker} -->"
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f"{filename} 的边界标记必须各出现一次")
    begin = text.index(start) + len(start)
    finish = text.index(end, begin)
    prompt = text[begin:finish].strip()
    if not prompt:
        raise ValueError(f"{filename} 的 Prompt 内容为空")
    return prompt


def render(template: str, replacements: dict[str, Any]) -> str:
    rendered = template
    for key, value in replacements.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, indent=2)
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


def review_prompt(snapshot: dict[str, Any], gold: dict[str, Any], seed: dict[str, Any]) -> str:
    return render(
        load_section("review"),
        {"SNAPSHOT_JSON": snapshot, "GOLD_JSON": gold, "SEED_JSON": seed},
    )


def semantic_prompt(snapshot: dict[str, Any], gold: dict[str, Any], seed: dict[str, Any]) -> str:
    return render(
        load_section("semantic"),
        {"SNAPSHOT_JSON": snapshot, "GOLD_JSON": gold, "SEED_JSON": seed},
    )


def variant_prompt(
    contract_name: str,
    dimension: str,
    instruction: str,
    contract_text: str,
) -> str:
    return render(
        load_section("variant"),
        {
            "CONTRACT_NAME": contract_name,
            "DIMENSION": dimension,
            "INSTRUCTION": instruction,
            "CONTRACT_TEXT": contract_text,
        },
    )


def dpo_judge_prompt(snapshot: dict[str, Any], rubric: dict[str, Any], candidates: list[str]) -> str:
    indexed = [{"index": index, "report": report} for index, report in enumerate(candidates)]
    return render(
        load_section("dpo_judge"),
        {"SNAPSHOT_JSON": snapshot, "RUBRIC_JSON": rubric, "CANDIDATES_JSON": indexed},
    )
