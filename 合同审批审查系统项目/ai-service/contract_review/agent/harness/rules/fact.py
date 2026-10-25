"""
【来自电商小二，改了业务逻辑】事实校验器

电商小二原版：提取订单金额/商品数量/物流单号等事实
本项目改造：提取合同金额/付款比例/账期天数/条款号/规则编码

原理：
1. FactExtractor：从 AI 回复里用正则提取事实
2. FactChecker：把这些事实和工具返回的结果对比
3. 如果 AI 说的事实在工具结果里找不到 → 判定为编造 → 重试
"""
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from contract_review.agent.harness.tools.snapshot import ToolCallSnapshot


class FactCategory(StrEnum):
    """定义需要工具数据支持的合同事实类别。"""

    AMOUNT = "AMOUNT"
    PERCENT = "PERCENT"
    DAYS = "DAYS"
    CLAUSE = "CLAUSE"
    RULE_CODE = "RULE_CODE"


@dataclass(frozen=True)
class ContractFact:
    """保存从 Agent 回复中提取的一项合同事实。"""

    category: FactCategory
    value: str
    normalized: str


class FactExtractor:
    """从 Agent 回复中提取需要工具证据支持的合同事实。"""

    rule_code_pattern = re.compile(r"\bR-[A-Z]+-\d+\b")
    clause_pattern = re.compile(r"第[一二三四五六七八九十百千零〇\d]+条")
    amount_pattern = re.compile(
        r"(?:合同金额|合同总价|合同额|签约额|总价|金额)"
        r"[^0-9]{0,8}([\d,]+(?:\.\d+)?)\s*(万元|元)?"
    )
    percent_pattern = re.compile(
        r"(?:付款比例|预付款|履约保证金|质保金|比例)"
        r"[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%"
    )
    days_pattern = re.compile(
        r"(?:账期|付款期限|结算后|验收后|完工后|工期)"
        r"[^0-9]{0,8}(\d+)\s*天"
    )

    @classmethod
    def extract(cls, reply_content: str) -> tuple[ContractFact, ...]:
        facts: dict[ContractFact, None] = {}

        for code in cls.rule_code_pattern.findall(reply_content):
            facts[ContractFact(FactCategory.RULE_CODE, code, code.upper())] = None

        for clause in cls.clause_pattern.findall(reply_content):
            facts[ContractFact(FactCategory.CLAUSE, clause, clause)] = None

        for raw, unit in cls.amount_pattern.findall(reply_content):
            amount = Decimal(raw.replace(",", ""))
            if unit == "万元":
                amount *= Decimal("10000")
            facts[
                ContractFact(
                    FactCategory.AMOUNT,
                    f"{raw}{unit or ''}",
                    _decimal_text(amount),
                )
            ] = None

        for percent in cls.percent_pattern.findall(reply_content):
            facts[
                ContractFact(
                    FactCategory.PERCENT,
                    f"{percent}%",
                    _decimal_text(Decimal(percent) / Decimal("100")),
                )
            ] = None

        for days in cls.days_pattern.findall(reply_content):
            facts[
                ContractFact(
                    FactCategory.DAYS,
                    f"{days}天",
                    _decimal_text(Decimal(days)),
                )
            ] = None

        return tuple(facts)


class FactChecker:
    """校验回复中的合同事实是否有成功工具结果支撑。"""

    number_pattern = re.compile(r"^[+-]?\d+(?:\.\d+)?$")

    def get_unsupported_facts(
        self,
        reply_content: str,
        successful_tool_calls: tuple[ToolCallSnapshot, ...],
    ) -> tuple[ContractFact, ...]:
        facts = FactExtractor.extract(reply_content)
        if not facts:
            return ()

        source_decimals: set[Decimal] = set()
        normalized_sources: set[str] = set()
        for tool_call in successful_tool_calls:
            for source in (tool_call.arguments, tool_call.result or {}):
                for scalar in self._iter_scalars(source):
                    normalized_sources.add(self._normalize_text(scalar))
                    decimal_value = self._to_decimal(scalar)
                    if decimal_value is not None:
                        source_decimals.add(decimal_value)

        return tuple(
            fact
            for fact in facts
            if not self._is_supported(fact, source_decimals, normalized_sources)
        )

    @classmethod
    def _is_supported(
        cls,
        fact: ContractFact,
        source_decimals: set[Decimal],
        normalized_sources: set[str],
    ) -> bool:
        if fact.category in {FactCategory.AMOUNT, FactCategory.PERCENT, FactCategory.DAYS}:
            value = Decimal(fact.normalized)
            # 百分比既可能是 0.4，也可能是 40，两种表示都接受。
            candidates = {value, value * 100, value / 100}
            return bool(candidates & source_decimals)

        normalized_value = cls._normalize_text(fact.value)
        return normalized_value in normalized_sources

    @classmethod
    def _to_decimal(cls, value: object) -> Decimal | None:
        text = str(value).strip()
        if cls.number_pattern.fullmatch(text) is None:
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None

    @staticmethod
    def _normalize_text(value: object) -> str:
        return (
            unicodedata.normalize("NFKC", str(value))
            .strip()
            .casefold()
            .replace("-", "_")
        )

    @classmethod
    def _iter_scalars(cls, value: object) -> Iterable[object]:
        if isinstance(value, dict):
            for item in value.values():
                yield from cls._iter_scalars(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from cls._iter_scalars(item)
        elif value is not None:
            yield value


def _decimal_text(value: Decimal) -> str:
    """去掉无意义的尾随 0，便于跨表示比较。"""
    normalized = value.normalize()
    return format(normalized, "f")
