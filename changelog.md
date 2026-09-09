# Changelog

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
