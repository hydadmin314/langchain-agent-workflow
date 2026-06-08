from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from agent.sales_recommendation_agent.intent_parser.models import (
    CustomerDemand,
    DemandCategoryDecision,
)


UNKNOWN_TEXT_VALUES = {"", "未知", "不确定", "暂不确定", "客户不确定", "unknown", "none", "null"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ConversationTurn(BaseModel):
    """单轮对话记录。

    这里不保存完整大模型上下文，只保存后续排查和前端展示需要看的关键信息。
    真正用于推荐决策的是 ConversationState.customer_need 这张结构化需求表。
    """

    turn_index: int
    user_text: str = ""
    assistant_message: str = ""
    status: str = ""
    created_at: str = Field(default_factory=_now_iso)


class DemandConflict(BaseModel):
    """多轮需求合并时发现的字段冲突。

    例如第一轮用户说“不需要固定 IP”，第三轮又说“要 8 个固定 IP”。
    这类冲突不建议程序悄悄覆盖，应记录下来，交给后续追问或人工确认。
    """

    field: str
    old_value: Any = None
    new_value: Any = None
    user_text: str = ""


class ConversationState(BaseModel):
    """销售推荐 Agent 的结构化会话状态。

    这个状态是多轮对话的核心：每一轮用户补充的信息都会被合并到 customer_need。
    后续推荐、追问、候选召回都只读取这份最新需求表，而不是依赖聊天全文自由发挥。
    """

    session_id: str = Field(default_factory=lambda: uuid4().hex)
    customer_need: CustomerDemand = Field(default_factory=CustomerDemand)
    category_decision: DemandCategoryDecision = Field(default_factory=DemandCategoryDecision)
    readiness: dict[str, Any] = Field(default_factory=dict)
    asked_questions: list[str] = Field(default_factory=list)
    answered_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    conflicts: list[DemandConflict] = Field(default_factory=list)
    turns: list[ConversationTurn] = Field(default_factory=list)
    turn_count: int = 0
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    def append_turn(self, *, user_text: str, assistant_message: str, status: str) -> None:
        """追加一轮对话摘要，并更新时间戳。"""

        self.turn_count += 1
        self.updated_at = _now_iso()
        self.turns.append(
            ConversationTurn(
                turn_index=self.turn_count,
                user_text=user_text,
                assistant_message=assistant_message,
                status=status,
            )
        )


class DemandMergeResult(BaseModel):
    """需求合并结果。

    changed_fields 用于调试“本轮到底补充了哪些信息”；
    conflict_fields 用于后续追问“刚才的信息和前面不一致，是否以这次为准”。
    """

    customer_need: CustomerDemand
    changed_fields: list[str] = Field(default_factory=list)
    answered_fields: list[str] = Field(default_factory=list)
    conflicts: list[DemandConflict] = Field(default_factory=list)


class CustomerDemandMerger:
    """把本轮解析出的 CustomerDemand 合并进历史需求表。

    合并原则：
    1. 旧值为空、新值有值：填入新值。
    2. 旧值有值、新值为空：保留旧值。
    3. 列表字段：做去重合并。
    4. 新旧值冲突：默认保留旧值并记录冲突。
    5. 用户明确表达“改成/不是/不要/以这次为准”等修正意图时，允许覆盖旧值。
    """

    def merge(
        self,
        *,
        existing: CustomerDemand | None,
        incoming: CustomerDemand,
        user_text: str = "",
    ) -> DemandMergeResult:
        old_need = existing or CustomerDemand()
        merged_payload = old_need.model_dump(mode="json")
        incoming_payload = incoming.model_dump(mode="json")

        changed_fields: list[str] = []
        answered_fields: list[str] = []
        conflicts: list[DemandConflict] = []
        for field_name in CustomerDemand.model_fields:
            if field_name == "confidence":
                merged_payload[field_name] = max(
                    float(merged_payload.get(field_name) or 0),
                    float(incoming_payload.get(field_name) or 0),
                )
                continue

            if field_name == "missing_fields":
                continue

            old_value = merged_payload.get(field_name)
            new_value = incoming_payload.get(field_name)

            if isinstance(old_value, list) or isinstance(new_value, list):
                merged_list = merge_list_values(old_value, new_value)
                if merged_list != (old_value or []):
                    merged_payload[field_name] = merged_list
                    changed_fields.append(field_name)
                    answered_fields.append(field_name)
                continue

            old_empty = is_empty_need_value(old_value)
            new_empty = is_empty_need_value(new_value)

            if old_empty and not new_empty:
                merged_payload[field_name] = new_value
                changed_fields.append(field_name)
                answered_fields.append(field_name)
                continue

            if not old_empty and new_empty:
                continue

            if old_value == new_value:
                continue

            if should_overwrite_field(field_name=field_name, user_text=user_text):
                merged_payload[field_name] = new_value
                changed_fields.append(field_name)
                answered_fields.append(field_name)
                continue

            conflicts.append(
                DemandConflict(
                    field=field_name,
                    old_value=old_value,
                    new_value=new_value,
                    user_text=user_text,
                )
            )

        merged_payload["missing_fields"] = build_missing_fields(merged_payload, incoming_payload)
        merged_need = CustomerDemand.model_validate(merged_payload)
        return DemandMergeResult(
            customer_need=merged_need,
            changed_fields=dedupe_texts(changed_fields),
            answered_fields=dedupe_texts(answered_fields),
            conflicts=conflicts,
        )


class InMemoryConversationStore:
    """内存版会话存储。

    第一版先满足本地测试和单进程服务使用；后续如果要接前端多用户或服务重启保留状态，
    可以在不改 workflow 的前提下替换成 JSON 文件、SQLite 或 Redis 实现。
    """

    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}

    def get(self, session_id: str) -> ConversationState | None:
        return self._states.get(session_id)

    def get_or_create(self, session_id: str | None = None) -> ConversationState:
        resolved_session_id = session_id or uuid4().hex
        state = self._states.get(resolved_session_id)
        if state:
            return state
        state = ConversationState(session_id=resolved_session_id)
        self._states[resolved_session_id] = state
        return state

    def save(self, state: ConversationState) -> ConversationState:
        state.updated_at = _now_iso()
        self._states[state.session_id] = state
        return state

    def clear(self, session_id: str) -> None:
        self._states.pop(session_id, None)


def is_empty_need_value(value: Any) -> bool:
    """判断需求字段是否为空。

    注意：False 是用户明确回答“不需要”，不能当成空值；None 才表示暂不确定。
    """

    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value.strip().lower() in UNKNOWN_TEXT_VALUES
    if isinstance(value, list):
        return len(value) == 0
    return False


def merge_list_values(old_value: Any, new_value: Any) -> list[Any]:
    """合并列表字段并保留原始顺序。"""

    values: list[Any] = []
    for source in (old_value, new_value):
        if isinstance(source, list):
            values.extend(source)
        elif not is_empty_need_value(source):
            values.append(source)
    return dedupe_values(values)


def build_missing_fields(merged_payload: dict[str, Any], incoming_payload: dict[str, Any]) -> list[str]:
    """重算 missing_fields，避免已经回答过的字段继续显示缺失。"""

    missing = list(incoming_payload.get("missing_fields") or [])
    return [
        field_name
        for field_name in dedupe_texts(missing)
        if is_empty_need_value(merged_payload.get(field_name))
    ]


def should_overwrite_field(*, field_name: str, user_text: str) -> bool:
    """判断某个字段是否允许被本轮输入覆盖。

    不能因为用户说了“不需要固定 IP”，就把 primary_goal、primary_category 等字段也一起覆盖。
    所以这里做字段级判断：只有本轮文本明确涉及该字段时，才允许覆盖旧值。
    """

    if not has_correction_cue(user_text):
        return False

    field_keywords = {
        "fixed_ip_required": ("固定IP", "固定 IP", "公网IP", "公网 IP"),
        "fixed_ip_count": ("固定IP", "固定 IP", "公网IP", "公网 IP"),
        "voice_required": ("语音", "固话", "电话", "座机"),
        "concurrent_calls": ("并发", "通道", "路", "坐席", "号码"),
        "budget": ("预算", "价格", "费用", "钱", "以内", "左右"),
        "bandwidth_need": ("带宽", "速率", "M", "G", "兆"),
        "user_count": ("人", "员工", "坐席", "终端", "账号"),
        "site_count": ("地点", "门店", "分支", "总部", "多点", "单点"),
        "business_action": ("新装", "变更", "移机", "过户", "改套餐", "拆机", "撤单", "续约"),
        "overseas_access": ("海外", "国外", "美国", "日本", "新加坡", "SaaS", "跨境"),
        "overseas_target": ("海外", "国外", "美国", "日本", "新加坡", "SaaS", "跨境"),
    }
    keywords = field_keywords.get(field_name, ())
    return any(keyword in user_text for keyword in keywords)


def has_correction_cue(text: str) -> bool:
    """判断用户本轮是否在修正前文信息。"""

    correction_keywords = (
        "改成",
        "改为",
        "不是",
        "不对",
        "刚才说错",
        "以这次为准",
        "其实是",
        "不要",
        "不需要",
        "需要",
    )
    return any(keyword in text for keyword in correction_keywords)


def dedupe_values(values: list[Any]) -> list[Any]:
    """对任意值列表去重，保留首次出现的值。"""

    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = repr(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def dedupe_texts(values: list[str]) -> list[str]:
    """字符串列表去重。"""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
