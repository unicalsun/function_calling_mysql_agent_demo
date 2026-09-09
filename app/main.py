"""
主程序入口 - 启动 MySQL 数据库分析 Agent

学习要点：
1. 程序启动流程：测试连接 → 初始化 Agent → 进入交互循环
2. 优雅退出：使用 try/finally 确保资源（数据库连接）被正确释放
3. 用户输入处理：支持特殊命令（quit/exit/clear）
"""

from app.agent import Agent
from app.db import Database


def main():
    print("=" * 60)
    print("  MySQL Database Analysis Agent (OpenAI Function Calling)")
    print("=" * 60)

    # 启动前测试数据库连接，失败则直接退出
    # 这是"快速失败"（Fail Fast）原则：有问题尽早暴露
    print("\n[初始化] 测试数据库连接...")
    if not Database.test_connection():
        print("[错误] 无法连接到数据库，请检查 .env 配置。")
        return
    print("[OK] 数据库连接成功！")

    # 初始化 Agent（会创建 OpenAI 客户端）
    agent = Agent()
    print("[OK] Agent 初始化完成。\n")
    print("输入问题开始分析，输入 quit/exit 退出，输入 clear 清空历史。\n")

    try:
        # 主交互循环（REPL：Read-Eval-Print Loop）
        while True:
            try:
                user_input = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                # Ctrl+C 或 Ctrl+D 优雅退出
                print("\n再见！")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("再见！")
                break
            if user_input.lower() == "clear":
                agent.clear_history()
                print("[已清空对话历史]")
                continue

            # 将用户输入发送给 Agent，获取回复
            reply = agent.chat(user_input)
            print(f"\nAgent > {reply}\n")
    finally:
        # 无论是否异常，都确保关闭数据库连接
        # finally 块保证即使 try 中发生异常也会执行
        Database.close()


if __name__ == "__main__":
    # 当直接运行此文件时执行 main()
    # 通过 python -m app.main 运行时也会执行
    # 但作为模块被 import 时不会执行
    main()
