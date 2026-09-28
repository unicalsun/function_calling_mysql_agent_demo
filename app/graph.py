"""
LangGraph 状态图 - 实现分层错误处理的 Agent 循环

架构设计：
- State: 定义整个图的状态，包含消息历史、错误跟踪、重试计数、路由信号
- 节点：LLM 决策 → 工具执行（带 Adapter） → 条件路由 → 错误处理 → 条件路由
- 条件路由：根据 ToolError 的 strategy 决定 retry/fallback/HITL/safe_end

学习重点：
1. StateGraph 是 LangGraph 的核心抽象，定义状态和节点
2. 条件路由（ConditionalEdge）让图可以根据状态动态选择路径
3. 循环通过 END 节点返回到 LLM 节点实现
4. 路由信号（next_node）让错误处理节点也能决定下一步
"""

from typing import Annotated, Literal, TypedDict
import operator
import json

from langgraph.graph import StateGraph, END
from langgraph.graph import MessagesState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from openai import OpenAI

from app.config import config
from app.prompts import SYSTEM_PROMPT
from app.tools import tools_schema
from app.adapters import ToolAdapter
from app.tool_error import ToolError, ErrorStrategy


# ========== 状态定义 ==========

class AgentState(TypedDict):
    """
    Agent 状态定义

    设计要点：
    - messages: 使用 LangChain 的标准消息历史
    - tool_errors: 累积的工具错误列表（用于调试和 LLM 参考）
    - retry_count: 当前工具调用重试次数
    - next_node: 路由信号，由错误处理节点设置，供条件路由读取
    - error_history: 错误历史记录（用于 LLM 学习避免重复错误）
    """
    # LangGraph 标准消息历史（operator.add 表示追加模式）
    messages: Annotated[list, operator.add]
    # 工具错误跟踪
    tool_errors: list
    # 重试计数
    retry_count: int
    # 路由信号：由 handle_error_node 设置，route_after_tool 读取
    next_node: str
    # 错误历史（供 LLM 参考）
    error_history: list


# ========== 节点定义 ==========

def llm_node(state: AgentState) -> dict:
    """
    LLM 决策节点

    职责：
    1. 调用 LLM，传入当前消息历史 + 工具定义
    2. 如果 LLM 选择工具调用，将 assistant 消息加入状态
    3. 如果 LLM 返回文本，直接返回（由条件路由判断是否继续）

    学习重点：
    - LLM 节点只负责"决策"，不执行工具
    - 返回格式遵循 LangGraph 的约定：返回要合并到状态中的键值对
    """
    client = OpenAI(
        api_key=config.OPENAI_API_KEY,
        base_url=config.OPENAI_API_BASE,
    )

    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=state["messages"],
        tools=tools_schema,
        temperature=config.LLM_TEMPERATURE,
    )

    choice = response.choices[0]
    result = {}

    # LLM 返回文本（finish_reason != "tool_calls"）→ 最终回复
    if choice.finish_reason != "tool_calls":
        reply = choice.message.content or ""
        result["messages"] = [AIMessage(content=reply)]
        # 标记为最终节点，不再循环
        result["next_node"] = "end"
    else:
        # LLM 选择工具调用，将 assistant 消息加入状态
        result["messages"] = [AIMessage(
            content=choice.message.content,
            tool_calls=[
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments),
                }
                for tc in choice.message.tool_calls
            ],
        )]
        # 清除之前的路由信号
        result["next_node"] = ""

    return result


def tool_node(state: AgentState) -> dict:
    """
    工具执行节点

    职责：
    1. 接收 LLM 的工具调用请求
    2. 通过 Adapter 执行工具（包含校验和修复）
    3. 如果执行成功，返回 ToolMessage
    4. 如果执行失败，返回 ToolError（由条件路由处理）

    学习重点：
    - Adapter 模式：在工具执行前后插入横切关注点（校验、日志、错误处理）
    - ToolError 携带策略信息，让上层可以做出智能决策
    """
    adapter = ToolAdapter()
    tool_messages = []
    tool_errors = []

    # 获取最近的 assistant 消息（包含工具调用）
    last_message = state["messages"][-1]
    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {"messages": [], "tool_errors": []}

    for tool_call in last_message.tool_calls:
        fn_name = tool_call.name
        fn_args = tool_call.args

        print(f"\n[Tool] {fn_name}({fn_args})")

        # 通过 Adapter 执行工具（包含校验 + 简单修复）
        result = adapter.execute(fn_name, fn_args)

        if isinstance(result, ToolError):
            print(f"[Error] {result}")
            tool_errors.append(result.to_message())
        else:
            print(f"[Result] {result[:200]}{'...' if len(result) > 200 else ''}")
            tool_messages.append(ToolMessage(
                content=result,
                tool_call_id=tool_call.id,
            ))

    return {
        "messages": tool_messages,
        "tool_errors": tool_errors,
    }


def route_after_tool(state: AgentState) -> Literal["llm", "handle_error", "end"]:
    """
    条件路由：根据工具执行结果决定下一步

    路由逻辑：
    - 如果没有错误（tool_errors 为空）→ 返回 LLM 继续对话
    - 如果有错误 → 根据错误的 strategy 决定：
      - RETRY → 增加重试计数，返回 LLM（让 LLM 用修复后的参数重试）
      - FALLBACK → 返回错误处理节点，尝试备用方案
      - HITL → 返回错误处理节点，需要用户确认
      - SAFE_END → 终止对话

    学习重点：
    - 条件路由是 LangGraph 的核心特性，让图具有动态性
    - 返回值必须是图中定义的节点名称或 END
    """
    tool_errors = state.get("tool_errors", [])

    # 没有错误 → 继续 LLM 循环
    if not tool_errors:
        return "llm"

    # 检查最后一个错误的策略
    last_error = tool_errors[-1]
    strategy = ErrorStrategy(last_error.get("strategy", "safe_end"))

    if strategy == ErrorStrategy.RETRY:
        # 重试：增加计数，返回 LLM
        if state.get("retry_count", 0) >= config.MAX_AGENT_LOOPS // 2:
            return "end"
        return "llm"

    elif strategy in (ErrorStrategy.FALLBACK, ErrorStrategy.HITL):
        return "handle_error"

    else:  # SAFE_END
        return "end"


def handle_error_node(state: AgentState) -> dict:
    """
    错误处理节点

    职责：
    1. 接收 ToolError
    2. 根据错误策略执行不同处理：
       - RETRY: 增加重试计数，返回 LLM 让其自行修复
       - FALLBACK: 提示 LLM 使用替代方法
       - HITL: 提示用户人工介入
       - SAFE_END: 终止对话

    学习重点：
    - 错误处理不应该简单地抛出异常，而应该提供恢复路径
    - HITL 是生产级 AI 应用的重要安全模式
    - 通过 next_node 信号让条件路由知道下一步去哪
    """
    tool_errors = state.get("tool_errors", [])
    if not tool_errors:
        return {"messages": [], "next_node": "end"}

    last_error = tool_errors[-1]
    strategy = ErrorStrategy(last_error.get("strategy", "safe_end"))
    tool_name = last_error.get("tool_name", "unknown")
    message = last_error.get("message", "")
    repair_suggestion = last_error.get("repair_suggestion", "")

    # 根据策略执行不同处理
    if strategy == ErrorStrategy.RETRY:
        # 重试：增加计数，返回 LLM
        error_message = f"[工具执行失败] {tool_name}: {message}\n建议：{repair_suggestion}"
        return {
            "messages": [HumanMessage(content=error_message)],
            "retry_count": state.get("retry_count", 0) + 1,
            "next_node": "llm",
        }

    elif strategy == ErrorStrategy.FALLBACK:
        # 备用方案：提示 LLM 使用替代方法
        fallback_message = f"[工具执行失败] {tool_name}: {message}\n建议：{repair_suggestion}\n请使用其他工具或方法完成相同目标。"
        return {
            "messages": [HumanMessage(content=fallback_message)],
            "tool_errors": tool_errors,
            "next_node": "llm",
        }

    elif strategy == ErrorStrategy.HITL:
        # 人机交互：需要用户确认
        hitl_message = f"[需要人工确认] {tool_name}: {message}\n{repair_suggestion}\n请修改您的请求或联系管理员。"
        return {
            "messages": [HumanMessage(content=hitl_message)],
            "tool_errors": tool_errors,
            "next_node": "llm",
        }

    else:  # SAFE_END
        # 安全终止
        end_message = f"[操作已终止] {tool_name}: {message}"
        return {
            "messages": [HumanMessage(content=end_message)],
            "tool_errors": tool_errors,
            "next_node": "end",
        }


def route_after_error(state: AgentState) -> Literal["llm", "end"]:
    """
    错误处理后的条件路由

    根据 handle_error_node 设置的 next_node 信号决定下一步。

    学习重点：
    - 多级条件路由：先根据工具结果路由，再根据错误处理结果路由
    - next_node 信号模式：节点间通过状态字段传递决策
    """
    next_node = state.get("next_node", "end")
    if next_node == "llm":
        return "llm"
    return "end"


# ========== 图构建 ==========

def build_graph() -> StateGraph:
    """
    构建 LangGraph 状态图

    图结构：
    start → llm → tool → route_after_tool → {llm, handle_error, end}
                                  ↘ handle_error → route_after_error → {llm, end}

    学习重点：
    - StateGraph 的节点和边定义
    - 条件边的使用
    - 图的编译和执行
    """
    # 创建状态图
    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("llm", llm_node)
    graph.add_node("tool", tool_node)
    graph.add_node("handle_error", handle_error_node)

    # 设置入口点
    graph.set_entry_point("llm")

    # 添加边：llm → tool
    graph.add_edge("llm", "tool")

    # 添加条件边：tool → route_after_tool
    graph.add_conditional_edges(
        "tool",
        route_after_tool,
        {
            "llm": "llm",
            "handle_error": "handle_error",
            "end": END,
        },
    )

    # 添加条件边：handle_error → route_after_error
    graph.add_conditional_edges(
        "handle_error",
        route_after_error,
        {
            "llm": "llm",
            "end": END,
        },
    )

    # 编译图
    return graph.compile()


def compile_graph():
    """编译图并返回（供外部调用）"""
    return build_graph()


if __name__ == "__main__":
    # 测试图构建
    app = build_graph()
    print("Graph built successfully!")
    print(f"Nodes: {list(app.nodes.keys())}")
    print(f"Edges: {app.edges}")
