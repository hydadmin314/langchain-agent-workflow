from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any

from agent.graph_agent import GraphAgent
from tools.sales_tools.calc_tool import build_calculator_response, render_calculator_response
from tools.sales_tools.generator_tool import (
    apply_package_selection,
    build_quote_proposal_response,
    parse_package_selection,
    render_quote_proposal_response,
    render_quote_sheet_response,
    render_recommendation_response,
)
from tools.sales_tools.product_tool import display_text
from tools.sales_tools.requirement_parser import parse_requirement_payload
from tools.sales_tools.rule_engine import classify_requirement_payload
from workflow.nodes import (
    _active_requirement_query,
)


@dataclass
class BusinessCase:
    case_id: str
    agent: GraphAgent = field(default_factory=GraphAgent)
    status: str = "created"
    title: str = "未命名业务"
    product_module_key: str | None = None
    product_module_name: str | None = None
    seed_query: str | None = None
    last_answer: str | None = None
    selection_text: str | None = None
    selected_package: dict[str, Any] | None = None
    selection_response: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


@dataclass
class AgentSession:
    cases: dict[str, BusinessCase] = field(default_factory=dict)
    active_case_id: str | None = None
    case_counter: int = 0
    lock: Lock = field(default_factory=Lock)


class AgentSessionStore:
    """In-memory session store for browser conversations.

    A browser session can contain multiple customer businesses. Each business
    gets an isolated GraphAgent so active_requirement, candidate plans and
    generator actions do not bleed into another customer's context.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._store_lock = Lock()

    PRODUCT_CASES = {
        "sd_wan": {
            "name": "SD-WAN",
            "seed_query": "强制产品模块：SD-WAN；产品模块：SD-WAN；场景：组网互联",
        },
        "intl_route_optimization": {
            "name": "国际路由优化",
            "seed_query": "强制产品模块：国际路由优化；产品模块：国际路由优化；场景：国际访问优化",
        },
        "isp_private_line": {
            "name": "ISP 专线",
            "seed_query": "强制产品模块：ISP专线；产品模块：ISP 专线；场景：互联网接入",
        },
    }
    PRODUCT_CHOICE_LABELS = {
        "sd_wan": "SD-WAN",
        "intl_route_optimization": "国际路由优化",
        "isp_private_line": "ISP 专线",
    }
    INTENT_TO_PRODUCT_MODULE = {
        "SD-WAN": "sd_wan",
        "组网互联": "sd_wan",
        "国际路由优化": "intl_route_optimization",
        "国际访问优化": "intl_route_optimization",
        "ISP": "isp_private_line",
        "互联网接入": "isp_private_line",
    }

    def _get_session(self, session_id: str) -> AgentSession:
        with self._store_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = AgentSession()
            return self._sessions[session_id]

    def _new_case(self, session: AgentSession) -> BusinessCase:
        session.case_counter += 1
        case_id = f"case-{session.case_counter}"
        business_case = BusinessCase(case_id=case_id)
        session.cases[case_id] = business_case
        session.active_case_id = case_id
        return business_case

    def _active_case(self, session: AgentSession) -> BusinessCase | None:
        if not session.active_case_id:
            return None
        return session.cases.get(session.active_case_id)

    def _case_for_message(self, session: AgentSession, message: str) -> BusinessCase:
        active_case = self._active_case(session)
        if not active_case:
            return self._new_case(session)
        session.active_case_id = active_case.case_id
        return active_case

    def _case_title(self, business_case: BusinessCase) -> str:
        active_query = _active_requirement_query(business_case.agent.active_requirement)
        if not active_query:
            return business_case.title
        normalized = active_query.replace("\n", " ").strip()
        return normalized[:28] + ("..." if len(normalized) > 28 else "")

    def _case_summary(self, business_case: BusinessCase) -> dict[str, Any]:
        requirement = business_case.agent.active_requirement
        return {
            "case_id": business_case.case_id,
            "title": self._case_title(business_case),
            "status": business_case.status,
            "product_module_key": business_case.product_module_key,
            "product_module_name": business_case.product_module_name,
            "selected_package": business_case.selected_package,
            "active_requirement": requirement,
            "created_at": business_case.created_at,
            "updated_at": business_case.updated_at,
        }

    def _payload(
        self,
        session_id: str,
        session: AgentSession,
        business_case: BusinessCase,
        answer: str,
        response_type: str = "agent",
    ) -> dict[str, Any]:
        summaries = [self._case_summary(item) for item in session.cases.values()]
        return {
            "session_id": session_id,
            "case_id": business_case.case_id,
            "active_case_id": session.active_case_id,
            "response_type": response_type,
            "answer": answer,
            "current_case": self._case_summary(business_case),
            "active_requirement": business_case.agent.active_requirement,
            "selected_package": business_case.selected_package,
            "business_context": {
                "active_case_id": session.active_case_id,
                "cases": summaries,
            },
        }

    def _candidate_pricing_results(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        recommended = response.get("recommended_pricing")
        recommended_key = (
            display_text(recommended.get("product_id")) + "|" + display_text(recommended.get("billing_cycle"))
            if recommended
            else ""
        )
        candidates = [recommended] if recommended else []
        for item in response.get("calculator", {}).get("pricing_results", []):
            key = display_text(item.get("product_id")) + "|" + display_text(item.get("billing_cycle"))
            if key and key != recommended_key:
                candidates.append(item)
        return candidates

    def _compare_item_by_id(self, response: dict[str, Any], product_id: str | None) -> dict[str, Any] | None:
        if not product_id:
            return None
        return next(
            (
                item
                for item in response.get("compare", {}).get("compared_items", [])
                if item.get("product_id") == product_id
            ),
            None,
        )

    def _apply_selected_package(self, response: dict[str, Any], selected_package: dict[str, Any] | None) -> dict[str, Any]:
        if not selected_package:
            return response
        product_id = selected_package.get("product_id")
        billing_cycle = selected_package.get("billing_cycle")
        selected = next(
            (
                item
                for item in self._candidate_pricing_results(response)
                if item.get("product_id") == product_id
                and (not billing_cycle or item.get("billing_cycle") == billing_cycle)
            ),
            None,
        )
        if not selected:
            return response
        selected_response = dict(response)
        selected_response["recommended_pricing"] = selected
        selected_response["recommended_compare"] = self._compare_item_by_id(response, product_id) or {}
        selected_response["selected_package"] = selected_package
        selected_response["message"] = f"已按用户选择的 {product_id} 生成。"
        return selected_response

    def _proposal_response_for_case(self, business_case: BusinessCase, query: str) -> dict[str, Any]:
        response = build_quote_proposal_response(query)
        if business_case.selected_package:
            return self._apply_selected_package(response, business_case.selected_package)
        if business_case.selection_text:
            return apply_package_selection(response, business_case.selection_text)
        return response

    def _selected_calculator_response(self, proposal_response: dict[str, Any]) -> dict[str, Any]:
        calculator = dict(proposal_response.get("calculator") or {})
        selected = proposal_response.get("recommended_pricing")
        if selected:
            calculator["pricing_results"] = [selected]
            calculator["message"] = proposal_response.get("message") or "已按用户选择完成折扣与总价计算。"
        return calculator

    def _selection_from_message(
        self,
        response: dict[str, Any],
        message: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        normalized_message = message.strip()
        product_id_match = re.search(
            r"(intl_route_optimization-\d+|sd_wan-\d+|isp-\d+)",
            normalized_message,
            re.IGNORECASE,
        )
        candidates = self._candidate_pricing_results(response)
        if product_id_match:
            product_id = product_id_match.group(1)
            selected = next((item for item in candidates if item.get("product_id") == product_id), None)
            if selected:
                return {
                    "rank": None,
                    "scope": "product",
                    "raw_text": normalized_message,
                    "product_id": selected.get("product_id"),
                    "product_name": selected.get("product_name"),
                    "billing_cycle": selected.get("billing_cycle"),
                }, None
            return None, f"当前业务候选方案中没有找到 {product_id}，请从当前候选列表中选择。"

        if not any(term in normalized_message for term in ("选择", "选", "用", "按", "就用")):
            return None, None

        name_matches: list[dict[str, Any]] = []
        for item in candidates:
            product_name = display_text(item.get("product_name"), "")
            if product_name and product_name in normalized_message:
                name_matches.append(item)

        unique_matches: dict[str, dict[str, Any]] = {}
        for item in name_matches:
            key = display_text(item.get("product_id")) + "|" + display_text(item.get("billing_cycle"))
            unique_matches[key] = item
        matches = list(unique_matches.values())

        if len(matches) == 1:
            selected = matches[0]
            return {
                "rank": None,
                "scope": "product",
                "raw_text": normalized_message,
                "product_id": selected.get("product_id"),
                "product_name": selected.get("product_name"),
                "billing_cycle": selected.get("billing_cycle"),
            }, None
        if len(matches) > 1:
            options = "、".join(
                f"{display_text(item.get('product_name'))}（{item.get('product_id')}，{item.get('billing_cycle')}）"
                for item in matches
            )
            return None, f"“{normalized_message}”匹配到多个候选方案，请补充产品编号确认：{options}。"

        return None, None

    def _handle_package_selection(
        self,
        session_id: str,
        session: AgentSession,
        business_case: BusinessCase,
        message: str,
    ) -> dict[str, Any] | None:
        is_rank_selection = bool(parse_package_selection(message))
        query = _active_requirement_query(business_case.agent.active_requirement)
        if not query and is_rank_selection:
            answer = "请先输入客户需求并生成候选方案后，再选择具体套餐。"
            return self._payload(session_id, session, business_case, answer)
        if not query:
            return None

        base_response = build_quote_proposal_response(query)
        product_selection, selection_error = self._selection_from_message(base_response, message)
        if selection_error:
            answer = selection_error
            return self._payload(session_id, session, business_case, answer)
        if product_selection:
            response = self._apply_selected_package(base_response, product_selection)
            business_case.selection_text = None
            business_case.selected_package = product_selection
            business_case.selection_response = response
            business_case.status = "package_selected"
            answer = render_recommendation_response(response)
            business_case.last_answer = answer
            business_case.updated_at = datetime.now().isoformat(timespec="seconds")
            business_case.title = self._case_title(business_case)
            return self._payload(session_id, session, business_case, answer)

        if not is_rank_selection:
            return None

        response = apply_package_selection(base_response, message)
        if not response.get("selection_error"):
            business_case.selection_text = message
            business_case.selected_package = response.get("selected_package")
            business_case.selection_response = response
            business_case.status = "package_selected"

        answer = render_recommendation_response(response)
        business_case.last_answer = answer
        business_case.updated_at = datetime.now().isoformat(timespec="seconds")
        business_case.title = self._case_title(business_case)
        return self._payload(session_id, session, business_case, answer)

    async def chat(self, session_id: str, message: str) -> dict[str, Any]:
        session = self._get_session(session_id)

        def run_locked() -> dict[str, Any]:
            with session.lock:
                active_case = self._active_case(session)
                selection_result = None
                if active_case:
                    selection_result = self._handle_package_selection(session_id, session, active_case, message)
                    if selection_result:
                        return selection_result

                business_case = self._case_for_message(session, message)
                selection_result = self._handle_package_selection(session_id, session, business_case, message)
                if selection_result:
                    return selection_result

                product_choice_prompt = self._product_choice_prompt(business_case, message)
                if product_choice_prompt:
                    business_case.last_answer = product_choice_prompt
                    business_case.status = "needs_product_choice"
                    business_case.updated_at = datetime.now().isoformat(timespec="seconds")
                    return self._payload(
                        session_id,
                        session,
                        business_case,
                        product_choice_prompt,
                        response_type="system",
                    )

                agent_input = self._input_for_case(business_case, message)
                answer = business_case.agent.run(agent_input)
                business_case.selection_text = None
                business_case.selected_package = None
                business_case.selection_response = None
                business_case.last_answer = answer
                business_case.status = "recommended"
                business_case.updated_at = datetime.now().isoformat(timespec="seconds")
                business_case.title = self._case_title(business_case)
                return self._payload(session_id, session, business_case, answer)

        return await asyncio.to_thread(run_locked)

    def _case_has_requirement(self, business_case: BusinessCase | None) -> bool:
        if not business_case:
            return False
        return bool(_active_requirement_query(business_case.agent.active_requirement))

    def _apply_product_module(self, business_case: BusinessCase, product_module: str | None) -> None:
        if not product_module:
            return
        product_config = self.PRODUCT_CASES.get(product_module)
        if not product_config:
            return
        business_case.product_module_key = product_module
        business_case.product_module_name = product_config["name"]
        business_case.seed_query = product_config["seed_query"]
        business_case.title = f"{product_config['name']}业务"

    def _product_choice_prompt(self, business_case: BusinessCase, message: str) -> str | None:
        if business_case.product_module_key or self._case_has_requirement(business_case):
            return None

        parsed = parse_requirement_payload(message)
        rule_result = classify_requirement_payload(parsed)
        candidates = rule_result.get("routed_modules") or []
        if len(candidates) < 2:
            return None

        explicit_modules = {
            self.INTENT_TO_PRODUCT_MODULE[item]
            for item in (parsed.get("target_categories") or []) + (parsed.get("scenarios") or [])
            if item in self.INTENT_TO_PRODUCT_MODULE
        }
        top = candidates[0]
        second = candidates[1]
        if len(explicit_modules) < 2 and (top.get("score") or 0) - (second.get("score") or 0) > 4:
            return None

        options = "、".join(
            self.PRODUCT_CHOICE_LABELS.get(item.get("module_key"), display_text(item.get("category")))
            for item in candidates[:3]
        )
        return (
            "这个需求同时命中了多个产品方向，先不直接报价，避免产品线判断错误。\n\n"
            f"可能的产品方向：{options}。\n"
            "请先点击下方的 SD-WAN、国际路由优化 或 ISP专线 产品按钮，"
            "我会把当前空业务切到对应产品线，然后你再继续补充客户需求。"
        )

    def _input_for_case(self, business_case: BusinessCase, message: str) -> str:
        if business_case.seed_query and not self._case_has_requirement(business_case):
            return f"{business_case.seed_query}；{message}"
        return message

    async def new_case(self, session_id: str, product_module: str | None = None) -> dict[str, Any]:
        session = self._get_session(session_id)

        def run_locked() -> dict[str, Any]:
            with session.lock:
                active_case = self._active_case(session)
                if active_case and not self._case_has_requirement(active_case):
                    self._apply_product_module(active_case, product_module)
                    product_text = active_case.product_module_name or "当前"
                    answer = f"当前已经是空的{product_text}业务 {active_case.case_id}，请直接输入这个客户的需求。"
                    active_case.last_answer = answer
                    active_case.updated_at = datetime.now().isoformat(timespec="seconds")
                    return self._payload(session_id, session, active_case, answer, response_type="system")

                business_case = self._new_case(session)
                self._apply_product_module(business_case, product_module)
                business_case.status = "created"
                business_case.updated_at = datetime.now().isoformat(timespec="seconds")
                product_text = business_case.product_module_name or ""
                answer = f"已新建{product_text}业务 {business_case.case_id}，请继续输入这个客户的需求。"
                business_case.last_answer = answer
                return self._payload(session_id, session, business_case, answer, response_type="system")

        return await asyncio.to_thread(run_locked)

    async def action(self, session_id: str, action: str, case_id: str | None = None) -> dict[str, Any]:
        session = self._get_session(session_id)

        def run_locked() -> dict[str, Any]:
            with session.lock:
                if case_id and case_id in session.cases:
                    session.active_case_id = case_id
                business_case = self._active_case(session)
                if not business_case:
                    answer = "请先输入客户需求，再生成方案、报价单或计算报价。"
                    business_case = self._new_case(session)
                    return self._payload(session_id, session, business_case, answer, response_type="system")

                query = _active_requirement_query(business_case.agent.active_requirement)
                if not query:
                    answer = "请先输入客户需求，再生成方案、报价单或计算报价。"
                    return self._payload(session_id, session, business_case, answer, response_type="system")

                if action == "generate_proposal":
                    response = self._proposal_response_for_case(business_case, query)
                    answer = render_quote_proposal_response(response)
                    business_case.status = "proposal_generated"
                elif action == "generate_quote_sheet":
                    response = self._proposal_response_for_case(business_case, query)
                    answer = render_quote_sheet_response(response)
                    business_case.status = "quote_generated"
                elif action == "calculate_quote":
                    response = (
                        self._selected_calculator_response(self._proposal_response_for_case(business_case, query))
                        if business_case.selection_text or business_case.selected_package
                        else build_calculator_response(query)
                    )
                    answer = render_calculator_response(response)
                    business_case.status = "calculated"
                else:
                    answer = f"暂不支持的操作：{action}"

                business_case.last_answer = answer
                business_case.updated_at = datetime.now().isoformat(timespec="seconds")
                business_case.title = self._case_title(business_case)
                return self._payload(session_id, session, business_case, answer)

        return await asyncio.to_thread(run_locked)

    async def reset(self, session_id: str) -> dict[str, Any]:
        session = self._get_session(session_id)

        def reset_locked() -> dict[str, Any]:
            with session.lock:
                for business_case in session.cases.values():
                    business_case.agent.reset()
                session.cases.clear()
                session.active_case_id = None
                session.case_counter = 0
                return {
                    "session_id": session_id,
                    "message": "session reset",
                    "active_requirement": None,
                    "business_context": {"active_case_id": None, "cases": []},
                }

        return await asyncio.to_thread(reset_locked)


session_store = AgentSessionStore()
