# MySQL Database Analysis Agent

## 项目概述


使用 OpenAI Function Calling 实现的 MySQL 数据库分析智能体。支持通过自然语言与 MySQL 数据库交互，进行数据分析、健康检查、性能诊断等操作。

## 架构


采用分层架构（类 MVC），职责分离：

```
app/
├── config.py      # 配置层 - 环境变量读取与验证
│                  #   - 使用 pydantic-settings 或 dotenv 管理敏感配置
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
├── agent.py       # 智能体层 - Agent 循环与 LLM 交互
│                  #   - 实现 Agent Loop：发送消息 → LLM 返回工具调用 → 执行工具 → 把结果发回 LLM → 循环
│                  #   - 这个循环是所有 Function Calling 应用的核心模式
└── main.py        # 入口层 - 程序启动与主循环
                   #   - 用户交互界面，接收自然语言输入，启动 Agent 循环
```

## 核心设计

### Function Calling 工具


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


对于高危操作，Agent 会展示以下信息供用户确认：
- 目标数据库
- 即将执行的 SQL 语句
- 操作类型分类（DDL/DML/TRUNCATE/BINLOG）

用户可选择 `y`（确认执行）或 `n`（拒绝操作）。

## 配置


所有配置通过 `.env` 文件注入，参考 `.env.example`。

## 使用


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
> 2. **Agent Loop（智能体循环）**：LLM 与工具之间的"请求-执行-返回"循环，
>    这是所有 AI Agent 应用的核心架构。
>
> 3. **安全控制**：通过工具分级（高危/普通）和 Human-in-the-Loop 确认机制，
>    确保高风险操作必须经过人类授权。
>
> 4. **分层架构**：将配置、数据库、工具、提示词、智能体逻辑分离，
>    使代码易于维护、测试和扩展。
>
> 5. **Prompt Engineering**：System Prompt 的质量直接影响 LLM 的工具调用准确性，
>    这是实际应用中需要反复调优的关键环节。
