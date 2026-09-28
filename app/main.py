"""
主程序入口 - 启动 MySQL 数据库分析 Agent (LangGraph 版本)

学习要点：
1. 程序启动流程：测试连接 → 初始化 Graph → 进入交互循环
2. 优雅退出：使用 try/finally 确保资源（数据库连接）被正确释放
3. 用户输入处理：支持特殊命令（quit/exit/clear）
4. LangGraph 应用：通过 app.invoke() 触发图执行
"""

from app.graph import compile_graph
from app.db import Database


def main():
    print("=" * 60)
    print("  MySQL Database Analysis Agent (LangGraph + Function Calling)")
    print("=" * 60)

    # 启动前测试数据库连接，失败则直接退出
    print("\n[初始化] 测试数据库连接...")
    if not Database.test_connection():
        print("[错误] 无法连接到数据库，请检查 .env 配置。")
        return
    print("[OK] 数据库连接成功！")

    # 编译 LangGraph 图
    print("[OK] LangGraph 图构建完成。")

    # 初始化消息历史（System Prompt 始终在最前面）
    from app.prompts import SYSTEM_PROMPT
    initial_state = {
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
        "tool_errors": [],
        "retry_count": 0,
        "next_node": "",
        "error_history": [],
    }

    print("[OK] Agent 初始化完成。\n")
    print("输入问题开始分析，输入 quit/exit 退出，输入 clear 清空历史。\n")

    # 编译并缓存图实例
    app = compile_graph()

    try:
        # 主交互循环（REPL：Read-Eval-Print Loop）
        while True:
            try:
                user_input = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("再见！")
                break
            if user_input.lower() == "clear":
                initial_state = {
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
                    "tool_errors": [],
                    "retry_count": 0,
                    "next_node": "",
                    "error_history": [],
                }
                print("[已清空对话历史]")
                continue

            # 将用户输入添加到状态（浅拷贝，避免修改原始状态）
            current_state = {
                **initial_state,
                "messages": list(initial_state["messages"]) + [
                    {"role": "user", "content": user_input}
                ],
            }

            # 执行 LangGraph 图
            try:
                result = app.invoke(current_state)
                # 提取最终回复（最后一条 assistant 消息）
                messages = result.get("messages", [])
                final_reply = ""
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("content"):
                        final_reply = msg["content"]
                        break
                if final_reply:
                    print(f"\nAgent > {final_reply}\n")
                else:
                    print("\n[系统] 处理完成，无最终回复。\n")
            except Exception as e:
                print(f"\n[错误] 执行过程中发生异常：{e}\n")
    finally:
        # 无论是否异常，都确保关闭数据库连接
        Database.close()


if __name__ == "__main__":
    main()
