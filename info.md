
---

所有模块导入验证通过。项目创建完成。

---

项目结构：
```
openai_function_calling_mysql_langgraph/
├── .env               ← 配置 MySQL / LLM（请填入 API Key）
├── .env.example       ← 配置模板
├── requirements.txt
├── agents.md
├── changelog.md
└── app/
    ├── config.py      ← .env 读取
    ├── db.py          ← 连接池 + SQL/Shell 执行
    ├── tools.py       ← 6 个 Function Calling 工具 + 确认逻辑
    ├── prompts.py     ← 系统提示词
    ├── agent.py       ← Agent 循环（LLM ↔ Tools）
    └── main.py        ← 入口
```
5 个高危工具（需二次确认）：
1. mysqlbinlog - binlog 读取
2. execute_ddl - DDL 语句
3. truncate_table - 清空表
4. execute_dml - INSERT/UPDATE/DELETE
5. cleanup_binlog - binlog 清理

另外 `execute_sql_query` 会自动检测写操作并触

运行前：编辑 `.env` 填入 `OPENAI_API_KEY，然后`

---


完成。现在 6 个高危工具：

```
┌─────┬────────────────┬───────────────────────────────┐
│  #  │     工具名     │             用途              │
├─────┼────────────────┼───────────────────────────────┤
│ 1   │ mysqlbinlog    │ binlog 读取与分析             │
├─────┼────────────────┼───────────────────────────────┤
│ 2   │ mysqldump      │ 数据库/表备份导出             │
├─────┼────────────────┼───────────────────────────────┤
│ 3   │ execute_ddl    │ DDL 语句（CREATE/ALTER/DROP） │
├─────┼────────────────┼───────────────────────────────┤
│ 4   │ truncate_table │ 清空表数据                    │
├─────┼────────────────┼───────────────────────────────┤
│ 5   │ execute_dml    │ INSERT/UPDATE/DELETE          │
├─────┼────────────────┼───────────────────────────────┤
│ 6   │ cleanup_binlog │ binlog 文件清理               │
└─────┴────────────────┴───────────────────────────────┘
```
全部高危工具执行前都会展示操作内容，需输入 y 确认后才执行。


---


已为 agents.md 添加了必要的注释，主要标注了以下学习重点：

1. 项目概述 - Function Calling 的核心价值（让 LLM 通过工具操作外部系统，而非直接执行 SQL）
2. 架构注释 - 每个模块的具体职责和设计意图（连接池、工具定义、Agent Loop 等）
3. Function Calling 工作流程 - 5 步流程说明，强调 LLM 只负责"决定调用什么工具"
4. 确认机制 - Human-in-the-Loop 的重要性（防止 LLM 误解意图导致危险操作）
5. 配置管理 - .env 文件的标准用法
6. 运行命令 - python -m 的规范用法
7. 学习要点总结 - 5 个核心概念（Function Calling、Agent Loop、安全控制、分层架构、Prompt Engineering）

注释使用 > 引用块格式，便于区分原文和注释内容。


---

已完成所有 Python 文件的关键注释添加，注释覆盖了以下核心知识点：

config.py

- load_dotenv() 的作用和调用时机
- 配置类设计模式（环境变量覆盖、默认值）
- @property 派生属性

db.py

- SQLAlchemy 连接池参数含义（pool_size, pool_recycle）
- 懒初始化（Lazy Init）模式
- with 上下文管理器确保连接归还
- shell=True 的安全风险

prompts.py

- System Prompt 的结构设计（角色、能力、原则、格式）

tools.py

- Function Calling Schema 结构（OpenAI 标准格式）
- 双层安全机制：显式高危工具 + 隐式 SQL 正则检测
- Human-in-the-Loop 确认模式
- 正则匹配防御性编程

agent.py

- Agent Loop 核心循环（最重要的概念）
- finish_reason 判断逻辑
- 消息历史（messages）的作用
- tool_call_id 关联机制

main.py

- 程序启动流程
- 快速失败（Fail Fast）原则
- try/finally 资源清理
- __name__ == "__main__" 的作用


---

# 改造完成：Tool → LangGraph → LLM 分层错误处理链路

## 新增文件

┌───────────────────┬─────────────────────────────────────────────────────────────────────────────────────┐
│       文件        │                                        职责                                         │
├───────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
│ app/validators.py │ 校验层：参数完整性 + SQL 安全检测 + 返回格式验证                                    │
├───────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
│ app/tool_error.py │ 错误类型层：ToolError 异常类 + ErrorStrategy 枚举（RETRY/FALLBACK/HITL/SAFE_END）   │
├───────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
│ app/adapters.py   │ 适配器层：ToolAdapter 包装原始工具，执行前校验 + 执行后简单修复，失败封装 ToolError │
├───────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
│ app/graph.py      │ 图定义层：AgentState + 4 个节点 + 2 条条件路由边                                    │
└───────────────────┴─────────────────────────────────────────────────────────────────────────────────────┘

## 核心设计

```
用户输入
  ↓
llm_node ──────────────────────────────────────────────┐
  ↓                                                    │
tool_node (ToolAdapter.execute)                        │
  ├─ 参数校验 → 失败 → ToolError(FALLBACK) ────────────┤
  ├─ SQL 安全检测 → 高危 → 确认 → 执行 ────────────────┤
  ├─ 执行工具 → 成功 → ToolMessage ────────────────────┤
  └─ 执行工具 → 失败 → ToolError(RETRY/HITL/SAFE_END) ┤
  ↓                                                    │
route_after_tool (条件路由)                             │
  ├─ 无错误 → llm ─────────────────────────────────────┘
  ├─ RETRY → llm (retry_count++)
  ├─ FALLBACK/HITL → handle_error_node
  └─ SAFE_END → END
  ↓
handle_error_node
  ├─ RETRY → HumanMessage(错误信息) → llm
  ├─ FALLBACK → HumanMessage(备用方案提示) → llm
  ├─ HITL → HumanMessage(人工介入提示) → llm
  └─ SAFE_END → HumanMessage(终止提示) → END

  ```

## 测试结果

✓ graph.py 语法正确，图编译成功：['__start__', 'llm', 'tool', 'handle_error']
✓ validators.py: DROP TABLE 正确被拦截，SELECT 通过
✓ tool_error.py: ToolError 工厂方法正常工作
✓ adapters.py: 缺少参数 → FALLBACK，空 SQL → ToolError

## 改动文件

- app/prompts.py — 新增「错误处理原则」章节
- app/main.py — 改用 app.graph.compile_graph() + app.invoke()
- app/requirements.txt — 新增 langgraph>=0.2.0 和 langchain-core>=0.3.0
- app/tools.py — 移除 emoji 避免 Windows GBK 编码错误
- agents.md — 更新架构说明和分层设计文档

---




