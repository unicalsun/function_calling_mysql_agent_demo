"""
智能体模块 - 实现 Agent Loop（核心循环）

学习重点：
1. Agent Loop 是所有 Function Calling 应用的核心模式
2. 循环流程：用户输入 → LLM 决策 → 工具执行 → 结果返回 → LLM 继续
3. 消息历史（messages）是 LLM 的"记忆"，每次交互都会累积
4. finish_reason 决定了循环是否继续
"""

from openai import OpenAI

from app.config import config
from app.prompts import SYSTEM_PROMPT
from app.tools import tools_schema, execute_tool


class Agent:
    def __init__(self):
        """
        初始化 Agent

        - client: OpenAI 客户端（兼容 DeepSeek 等 OpenAI-compatible API）
        - messages: 消息历史列表，存储整个对话过程
          - 第一条消息是 system prompt（角色定义）
          - 后续消息是 user/assistant/tool 的交互记录
        """
        self.client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_API_BASE,  # DeepSeek API 地址
        )
        # 初始化消息历史，system prompt 始终在最前面
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def chat(self, user_input: str) -> str:
        """
        核心方法：处理用户输入并返回最终回复

        Agent Loop 流程：
        1. 将用户输入添加到消息历史
        2. 调用 LLM，传入所有历史消息 + 工具定义
        3. 如果 LLM 返回文本（finish_reason != "tool_calls"）→ 返回结果
        4. 如果 LLM 返回工具调用 → 执行工具 → 将结果加入历史 → 回到步骤2
        5. 重复直到 LLM 返回文本或达到最大循环次数
        """
        self.messages.append({"role": "user", "content": user_input})

        # 限制最大循环次数，防止无限循环
        for _ in range(config.MAX_AGENT_LOOPS):
            response = self.client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=self.messages,      # 完整的对话历史
                tools=tools_schema,          # 可用工具定义
                temperature=config.LLM_TEMPERATURE,
            )

            choice = response.choices[0]

            # ---- 判断 LLM 的响应类型 ----

            # 无工具调用 → LLM 返回文本结果，循环结束
            if choice.finish_reason != "tool_calls":
                reply = choice.message.content or ""
                self.messages.append({"role": "assistant", "content": reply})
                return reply

            # 有工具调用 → 执行工具，继续循环
            # 将 assistant 的工具调用请求加入历史
            self.messages.append(choice.message)

            # 遍历所有工具调用（LLM 可能一次调用多个工具）
            for tool_call in choice.message.tool_calls:
                fn_name = tool_call.function.name
                import json
                fn_args = json.loads(tool_call.function.arguments)  # JSON 字符串 → 字典
                print(f"\n[Tool] {fn_name}({fn_args})")

                # 执行工具（内部处理高危确认）
                result = execute_tool(fn_name, fn_args)
                print(f"[Result] {result[:200]}{'...' if len(result) > 200 else ''}")

                # 将工具执行结果加入消息历史
                # role="tool" + tool_call_id 用于关联对应的工具调用
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

        # 循环次数用尽
        return "达到最大交互轮数，请重新提问。"

    def clear_history(self):
        """清空对话历史（保留 system prompt）"""
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
