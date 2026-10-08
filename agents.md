# MySQL Database Analysis Agent (LangGraph + 分层错误处理)


---



## 重要约定
- 遵守 mnemo （C:\Users\veleven\AppData\Roaming\mnemo）的长期记忆中的内容， 使用mnemo 的search方法查看
- mnemo中的内容，修改必须向用户确认，不能自己增删改。
- 如果提到直接“只解读”， 那就是只读且不做任何的修改和新增。



## 禁止事项
- 每次优化和修改bug，如果发现新bug且与本次修复无关的bug，优先询问我，不要自己执行。
- 生产环境的 .env 文件不要碰

---


## 项目概述

> 学习重点：本项目是 OpenAI Function Calling + LangGraph 在实际生产场景中的完整应用，
> 展示了如何让 LLM 通过定义好的工具函数来操作外部系统（MySQL），
> 而不是直接生成 SQL 并执行（后者存在 SQL 注入等安全风险）。
> 相比纯 OpenAI SDK 版本，引入了 LangGraph 状态图和分层错误处理机制。

使用 OpenAI Function Calling 实现的 MySQL 数据库分析智能体。支持通过自然语言与 MySQL 数据库交互，进行数据分析、健康检查、性能诊断等操作。

## 架构

> 学习重点：采用分层架构（类 MVC 模式）的核心目的是**职责分离**。
> 每一层只关心自己的事情，修改某一层时不影响其他层。
> 例如：更换数据库只需要改 db.py，更换 LLM 只需要改 graph.py。

采用分层架构（类 MVC）+ LangGraph 状态图，职责分离：

```
app/
├── config.py      # 配置层 - 环境变量读取与验证
│                  #   - 使用 dotenv 管理敏感配置
│                  #   - 典型配置：数据库连接串、LLM API Key、模型名称等
├── db.py          # 数据库层 - 连接池管理与 SQL 执行
│                  #   - 连接池（Connection Pool）避免每次查询都建立新连接，提升性能
│                  #   - 统一的 SQL 执行入口，方便做日志、超时、错误处理
├── tools.py       # 工具层 - Function Calling 工具定义与实现
│                  #   - 这是整个项目的核心：定义 LLM 可以调用的"工具"
│                  #   - 每个工具包含：名称、描述、参数 schema（JSON Schema 格式）
│                  #   - LLM 根据描述判断何时调用哪个工具，参数由 LLM 自动填充
├── prompts.py     # 提示层 - LLM 系统提示词
│                  #   - System Prompt 告诉 LLM 它的角色、可用工具、行为规范
│                  #   - 良好的 prompt 能显著提升 LLM 的工具调用准确性
├── validators.py  # 校验层 - 数据校验模块
│                  #   - SQL 安全检测：防止 LLM 绕过专用工具直接执行写操作
│                  #   - 返回格式验证：确保结构化数据（如 JSON）可解析
│                  #   - 参数完整性检查：验证工具调用参数是否符合预期
├── tool_error.py  # 错误类型层 - ToolError 异常类与策略枚举
│                  #   - ToolError: 携带 strategy/repair_suggestion/original_arguments
│                  #   - ErrorStrategy: RETRY/FALLBACK/HITL/SAFE_END 四种策略
│                  #   - 工厂方法：connection_error/sql_syntax_error/permission_error 等
├── adapters.py    # 适配器层 - 工具包装 + 简单修复
│                  #   - ToolAdapter: 在工具执行前后插入横切关注点（校验、日志、错误处理）
│                  #   - simple_repair_result(): 自动修复简单的结果问题（格式化、JSON 修复）
│                  #   - 修复失败后封装为 ToolError，转发给 LangGraph
├── graph.py       # 图定义层 - LangGraph 状态图与节点函数
│                  #   - AgentState: 定义状态（messages, tool_errors, retry_count, next_node）
│                  #   - llm_node: 调用 LLM，返回工具调用或文本回复
│                  #   - tool_node: 通过 Adapter 执行工具，收集结果/错误
│                  #   - route_after_tool: 条件路由，根据错误策略决定下一步
│                  #   - handle_error_node: 错误处理，执行 retry/fallback/HITL/safe_end
│                  #   - route_after_error: 错误处理后的条件路由
└── main.py        # 入口层 - 程序启动与主循环
                   #   - 用户交互界面，接收自然语言输入，启动 LangGraph 图
```

## 核心设计

### Function Calling 工具

> 学习重点：Function Calling 的工作流程：
> 1. 开发者定义工具（名称 + 描述 + 参数 JSON Schema）
> 2. 用户发送自然语言请求（如"导出 users 表"）
> 3. LLM 分析请求，决定调用哪个工具，并生成参数
> 4. 应用层执行工具，把结果返回给 LLM
> 5. LLM 把结果格式化后返回给用户
>
> 关键点：LLM 不直接执行 SQL，它只是"决定调用什么工具、传什么参数"。
> 实际执行由应用层控制，可以加入权限检查、参数校验等安全措施。

**高危工具**（需用户二次确认）：
1. `mysqlbinlog` - 读取/分析 MySQL binlog
2. `mysqldump` - 导出数据库或表的结构和数据（备份）
3. `execute_ddl` - 执行 DDL 语句（CREATE/ALTER/DROP）
4. `truncate_table` - 清空表数据
5. `execute_dml` - 执行 DML 语句（INSERT/UPDATE/DELETE）
6. `cleanup_binlog` - 清理 binlog 文件

**普通工具**：
1. `execute_sql_query` - 执行 SQL 查询（自动检测高危语句）

### 确认机制

> 学习重点：这是 Human-in-the-Loop（人在回路）的典型实现。
> 对于高危操作，不完全信任 LLM 的判断，引入人类确认环节。
> 这是生产级 AI 应用的重要安全模式：
> - LLM 可能误解用户意图（如把"查看"理解为"删除"）
> - LLM 可能生成有副作用的操作（如误触 DROP TABLE）
> - 二次确认让最终执行权始终在人类手中

对于高危操作，Agent 会展示以下信息供用户确认：
- 目标数据库
- 即将执行的 SQL 语句
- 操作类型分类（DDL/DML/TRUNCATE/BINLOG）

用户可选择 `y`（确认执行）或 `n`（拒绝操作）。

### LangGraph 分层错误处理

> 学习重点：这是本项目的核心创新点。
> 传统 Agent 只有一条线性的"请求-执行-返回"路径，
> 而本项目引入了分层错误处理机制：
>
> 1. **校验层**（validators.py）：在工具执行前后进行多层校验
> 2. **适配器层**（adapters.py）：执行工具 + 简单修复
> 3. **错误类型层**（tool_error.py）：用 ToolError 携带策略信息
> 4. **图路由层**（graph.py）：根据策略决定 retry/fallback/HITL/safe_end
>
> 这种设计让 Agent 能够：
> - 自动重试临时性错误（如连接超时）
> - 优雅地降级到备用方案（如使用只读查询代替写操作）
> - 在必要时请求人工介入（如高危操作被拒绝）
> - 在严重错误时安全终止（如权限不足）

**分层处理流程：**

```
用户输入
  ↓
LLM 节点 → 决策是否调用工具
  ↓
工具节点 → Adapter 执行（校验 + 修复）
  ↓
路由节点 → 根据 ToolError.strategy 决定：
  ├── RETRY  →  增加 retry_count → 返回 LLM 重新决策
  ├── FALLBACK → 错误处理节点 → 尝试备用方案 → 返回 LLM
  ├── HITL   → 错误处理节点 → 提示用户人工介入 → 返回 LLM
  └── SAFE_END → 终止对话
```

**四种策略的含义：**

| 策略 | 适用场景 | 处理方式 |
|------|----------|----------|
| RETRY | 临时性错误（连接超时、网络抖动） | 增加重试计数，返回 LLM 用修复后的参数重试 |
| FALLBACK | 可降级场景（SQL 语法错误、参数缺失） | 提示 LLM 使用替代工具或方法 |
| HITL | 需要人工确认（高危操作被拒、权限不足） | 提示用户修改请求或联系管理员 |
| SAFE_END | 严重错误（执行终止、不可恢复） | 安全终止对话 |

### ToolAdapter 完整执行流程

```
ToolAdapter.execute(tool_name, arguments)
  │
  ├─[1] 参数校验 validate_tool_arguments()
  │       失败 → ToolError(FALLBACK)
  │
  ├─[2] 高危确认（HIGH_RISK_TOOLS 列表）
  │       用户拒绝 → ToolError(SAFE_END)
  │
  ├─[3] SQL 安全检测 validate_sql_safety()
  │       高危语句 → CONFIRM_HIGH_RISK=true → 用户确认
  │                → CONFIRM_HIGH_RISK=false → ToolError(HITL)
  │
  ├─[4] 执行工具 _execute_tool()
  │       成功 → 返回 str
  │       异常 → _handle_execution_error() → ToolError(按错误类型分类)
  │
  └─[5] 结果校验 validate_query_result_format()
          失败 → [6] 简单修复 _simple_repair_result()
                  成功 → 返回修复后的 str
                  失败 → ToolError(FALLBACK)
```

**简单修复规则：**

| 场景 | 修复动作 |
|------|---------|
| SQL 结果多余空行 | `\n{3,}` → `\n\n` |
| SQL 结果 >2000 字符 | 截断 + 提示 "输出已截断" |
| SQL 结果缺分隔线 | 在列名后插入 `---` 分隔线 |
| JSON 尾部多余逗号 | 去除 `,` |
| JSON 缺少闭合括号 | 补齐 `}` 或 `]` |
| 空结果 | 补充 "执行成功，但无输出内容" |

### ToolError → LangGraph 数据流

```
Adapter 返回 ToolError
  │
  ▼
tool_node 写入 state
  {
    "messages": [],                        # 无 ToolMessage
    "tool_errors": [{                       # 累积错误列表
      "type": "tool_error",
      "tool_name": "execute_sql_query",
      "message": "SQL 语法错误：...",
      "strategy": "fallback",               # ← 路由决策依据
      "repair_suggestion": "...",
      "retry_count": 0,
      "original_arguments": {...},
      "original_output": null
    }]
  }
  │
  ▼
route_after_tool 读取 strategy
  strategy == "retry"   → "llm"
  strategy == "fallback" → "handle_error"
  strategy == "hitl"     → "handle_error"
  strategy == "safe_end" → END
  │
  ▼
handle_error_node 根据策略生成 HumanMessage
  FALLBACK → "[工具执行失败] ...\n请使用其他工具或方法完成相同目标。"
  HITL     → "[需要人工确认] ...\n请修改您的请求或联系管理员。"
  │
  ▼
route_after_error 读取 next_node 信号
  next_node == "llm" → 继续循环
  next_node == "end" → 终止
  │
  ▼
LLM 看到上下文：
  messages = [System, User, Assistant(tool_calls), Human("错误信息")]
  → LLM 基于错误信息重新决策
```

## 配置

> 学习重点：敏感配置（API Key、数据库密码）永远不要硬编码在代码里。
> 使用 .env 文件 + .env.example 模板是 Python 项目的标准做法：
> - .env.example 提交到 Git，供团队参考需要哪些配置
> - .env 加入 .gitignore，包含真实的密钥，不提交

所有配置通过 `.env` 文件注入，参考 `.env.example`。

新增配置项：
- `CONFIRM_HIGH_RISK`: 高危操作是否需要用户确认（true/false，默认 true）
- `MAX_AGENT_LOOPS`: 最大循环次数，防止无限循环（默认 20，重试上限为 10）

## 使用

> 学习重点：`python -m app.main` 表示以模块方式运行 main.py，
> 这比 `python app/main.py` 更规范，能正确处理包内相对导入。

```bash
pip install -r requirements.txt
python -m app.main
```

## 学习要点总结

> 通过本项目可以学到的核心概念：
>
> 1. **Function Calling 模式**：LLM 通过结构化的工具定义与外部系统交互，
>    而非直接执行代码或生成原始 SQL。
>
> 2. **LangGraph StateGraph**：使用 TypedDict 定义状态，用节点函数处理转换，
>    用条件边（ConditionalEdge）实现动态路由。
>
> 3. **分层错误处理**（本项目核心创新）：
>    - 校验层（validators.py）：三层纵深防御（参数 → SQL 安全 → 返回格式）
>    - 适配器层（adapters.py）：横切关注点 + 简单修复
>    - 错误类型层（tool_error.py）：携带策略信息的异常类
>    - 图路由层（graph.py）：根据策略决定 retry/fallback/HITL/safe_end
>
> 4. **安全控制**：通过工具分级（高危/普通）和 Human-in-the-Loop 确认机制，
>    确保高风险操作必须经过人类授权。
>
> 5. **Adapter 模式**：在不修改原始工具代码的前提下，
>    通过包装层增加校验、日志、错误处理等横切关注点。
>
> 6. **策略化错误处理**：ToolError 不仅携带错误信息，还携带"如何处理"的策略，
>    让上层路由做智能决策，而非简单地抛出异常。
>
> 7. **多级条件路由**：先根据工具结果路由，再根据错误处理结果路由，
>    通过 next_node 信号模式实现节点间解耦的决策传递。
>
> 8. **Prompt Engineering**：System Prompt 的质量直接影响 LLM 的工具调用准确性，
>    新的提示词增加了错误处理原则，指导 LLM 在遇到错误时如何调整请求。
