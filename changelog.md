# Changelog

## 2026-09-09 - v1.1.0 (LangGraph + 分层错误处理)

### 架构升级：从线性 Agent Loop 到 LangGraph 状态图

将原有的线性 `Agent.chat()` 循环重构为 LangGraph `StateGraph`，引入分层错误处理机制。

### 新增文件

- **`app/validators.py`** — 校验层
  - `ValidationResult`：校验结果数据类（valid / errors / hints）
  - `validate_sql_safety()`：SQL 安全检测，拦截 DROP/DELETE/UPDATE/INSERT/TRUNCATE/CREATE/PURGE
  - `validate_query_result_format()`：查询返回格式验证（空结果提示、SQL 执行出错检测）
  - `validate_tool_arguments()`：参数完整性检查
  - `validate_tool_output()`：综合校验入口（参数 → SQL 安全 → 返回格式，三层纵深防御）

- **`app/tool_error.py`** — 错误类型层
  - `ErrorStrategy` 枚举：RETRY / FALLBACK / HITL / SAFE_END 四种处理策略
  - `ToolError` 数据类：携带 tool_name / message / strategy / repair_suggestion / original_arguments / original_output
  - `to_message()`：序列化为 dict，供 LangGraph State 存储
  - `from_validation_error()`：从 ValidationResult 创建 ToolError（自动映射策略）
  - 工厂方法：`connection_error` / `sql_syntax_error` / `permission_error` / `user_rejected_error` / `shell_command_error`

- **`app/adapters.py`** — 适配器层（横切关注点包装）
  - `ToolAdapter.execute()`：统一执行入口，顺序执行参数校验 → 高危确认 → SQL 安全检测 → 工具执行 → 结果校验 → 简单修复
  - `_simple_repair_result()`：自动修复层
    - `_repair_sql_result()`：去除多余空行、截断 >2000 字符输出、补充分隔线
    - `_repair_json_result()`：去除尾部逗号、补齐缺失括号
    - 空结果补充：添加"执行成功但无输出"提示
  - `_handle_execution_error()`：异常分类 → 对应策略的 ToolError（连接超时→RETRY、语法错误→FALLBACK、权限不足→HITL）

- **`app/graph.py`** — 图定义层（替代原 agent.py）
  - `AgentState` TypedDict：messages(追加) / tool_errors / retry_count / next_node / error_history
  - `llm_node()`：调用 LLM，tool_calls 时写 AIMessage，文本回复时设置 next_node="end"
  - `tool_node()`：通过 ToolAdapter 执行工具，收集 ToolMessage 或 ToolError
  - `route_after_tool()`：条件路由，根据 tool_errors[-1].strategy 选择 llm / handle_error / end
  - `handle_error_node()`：错误处理节点，根据策略生成 HumanMessage 并设置 next_node
  - `route_after_error()`：二次条件路由，读取 next_node 信号决定继续或终止

### 修改文件

- **`app/prompts.py`** — 新增「错误处理原则」章节，指导 LLM 在遇到工具错误时如何调整请求
- **`app/main.py`** — 改用 `graph.compile_graph()` + `app.invoke(state)` 驱动图执行；每次请求浅拷贝 state，clear 命令重置完整 state
- **`app/tools.py`** — 移除 emoji（⚠️）避免 Windows GBK 编码崩溃
- **`requirements.txt`** — 新增 `langgraph>=0.2.0` 和 `langchain-core>=0.3.0`

### 新架构

```
app/
├── config.py      # 配置层
├── db.py          # 数据库层
├── tools.py       # 工具 Schema + 高危列表 + 确认逻辑
├── prompts.py     # 提示词（新增错误处理原则）
├── validators.py  # 校验层（参数/SQL安全/返回格式）
├── tool_error.py  # 错误类型层（ToolError + ErrorStrategy）
├── adapters.py    # 适配器层（校验+修复+错误封装）
├── graph.py       # 图定义层（StateGraph + 节点 + 条件路由）
└── main.py        # 入口层（LangGraph 驱动）
```

### LangGraph 图结构

```
__start__ → llm → tool → route_after_tool ──无错误──→ llm (循环)
                          │
                          ├─ RETRY → llm (retry_count++)
                          ├─ FALLBACK/HITL → handle_error → route_after_error → llm
                          └─ SAFE_END → END

route_after_error:
  next_node=="llm" → llm (继续循环)
  next_node=="end" → END (终止)
```

### 分层错误处理流程

```
LLM 调用工具
  │
  ▼
ToolAdapter.execute()
  │
  ├─[校验1] validate_tool_arguments() → 失败 → ToolError(FALLBACK)
  ├─[确认] HIGH_RISK_TOOLS → 用户拒绝 → ToolError(SAFE_END)
  ├─[校验2] validate_sql_safety() → 高危 → HITL确认 / ToolError(HITL)
  ├─[执行] Database.execute_query() → 异常 → ToolError(RETRY/FALLBACK/HITL/SAFE_END)
  ├─[校验3] validate_query_result_format() → 异常 → 触发修复
  └─[修复] _simple_repair_result()
       ├─ 成功 → str (直接返回 LLM)
       └─ 失败 → ToolError(FALLBACK)
  │
  ▼
tool_node 写入 state.tool_errors
  │
  ▼
route_after_tool 读取 strategy 决定路由
  │
  ▼
handle_error_node 生成 HumanMessage 供 LLM 参考
  │
  ▼
LLM 基于错误信息重新决策
```

### 四种策略处理路径

| 策略 | 触发场景 | LangGraph 路径 | LLM 看到的上下文 |
|------|---------|---------------|----------------|
| RETRY | 连接超时、网络抖动 | tool → route → llm (retry_count++) | 错误信息 + 建议，自行重试 |
| FALLBACK | SQL 语法错误、参数缺失 | tool → handle_error → llm | 错误 + 替代方案提示 |
| HITL | 高危操作被拒、权限不足 | tool → handle_error → llm | 人工介入提示 |
| SAFE_END | 不可恢复错误 | tool → END | 终止消息 |

### 学习要点（新增）

1. **LangGraph StateGraph**：用 TypedDict 定义状态，用节点函数处理转换，用条件边实现动态路由
2. **Adapter 模式**：在不修改原始工具代码的前提下，通过包装层注入校验、日志、错误处理等横切关注点
3. **策略化错误处理**：ToolError 不仅携带错误信息，还携带"如何处理"的策略，让上层路由做智能决策
4. **多级条件路由**：先根据工具结果路由，再根据错误处理结果路由，形成完整的错误恢复闭环
5. **next_node 信号模式**：节点间通过状态字段传递决策，避免节点间的直接耦合

---

## 2026-09-09 - v1.0.0

### 新增

- **MySQL Database Analysis Agent**：基于 OpenAI Function Calling 的 MySQL 数据库分析智能体
- **分层架构**：config / db / tools / prompts / agent / main 六层分离
- **7 个 Function Calling 工具**：
  - `execute_sql_query` - SQL 查询执行（自动检测高危语句）
  - `mysqlbinlog` - Binlog 读取与分析
  - `mysqldump` - 数据库/表结构和数据导出（备份）
  - `execute_ddl` - DDL 语句执行
  - `truncate_table` - 表数据清空
  - `execute_dml` - DML 语句执行（INSERT/UPDATE/DELETE）
  - `cleanup_binlog` - Binlog 文件清理
- **高危操作二次确认机制**：6 个高危工具执行前需用户确认
- **自动风险检测**：`execute_sql_query` 工具自动检测 DDL/DML/TRUNCATE 等语句并触发确认
- **环境变量配置**：MySQL、LLM、Agent 配置全部通过 `.env` 注入
- **多 LLM 支持**：默认 DeepSeek V4 Flash，兼容所有 OpenAI 接口的模型
- **对话历史管理**：支持清空历史（clear 命令）

### 架构

```
app/
├── config.py      # 配置层 - .env 环境变量读取
├── db.py          # 数据库层 - 连接池 + SQL 执行 + Shell 命令
├── tools.py       # 工具层 - Function Calling Schema + 实现 + 确认逻辑
├── prompts.py     # 提示层 - 系统提示词
├── agent.py       # 智能体层 - Agent 循环（LLM 交互 + 工具调用）
└── main.py        # 入口层 - 程序启动
```
