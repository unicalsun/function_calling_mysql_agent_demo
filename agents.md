# MySQL Database Analysis Agent

## 项目概述

> 学习重点：本项目是 OpenAI Function Calling 在实际生产场景中的完整应用，
> 展示了如何让 LLM 通过定义好的工具函数来操作外部系统（MySQL），
> 而不是直接生成 SQL 并执行（后者存在 SQL 注入等安全风险）。

使用 OpenAI Function Calling 实现的 MySQL 数据库分析智能体。支持通过自然语言与 MySQL 数据库交互，进行数据分析、健康检查、性能诊断等操作。

## 架构

> 学习重点：采用分层架构（类 MVC 模式）的核心目的是**职责分离**。
> 每一层只关心自己的事情，修改某一层时不影响其他层。
> 例如：更换数据库只需要改 db.py，更换 LLM 只需要改 agent.py。

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

## 配置

> 学习重点：敏感配置（API Key、数据库密码）永远不要硬编码在代码里。
> 使用 .env 文件 + .env.example 模板是 Python 项目的标准做法：
> - .env.example 提交到 Git，供团队参考需要哪些配置
> - .env 加入 .gitignore，包含真实的密钥，不提交

所有配置通过 `.env` 文件注入，参考 `.env.example`。

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
