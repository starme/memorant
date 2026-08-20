# memorant

## 项目概述
- 技术栈：Python 3.10+ / FastMCP MCP server + Claude Code plugin hooks and commands
- 后端路径：`mcp-server`
- 前端路径：无

## 常用命令
- 后端测试：`cd mcp-server && uv sync --dev && uv run python -m pytest`
- Shell hook 测试：`bash tests/test-memorant-hook.sh && bash tests/test-on-git-commit.sh`
- 构建：`cd mcp-server && uv build`
- Lint：`cd mcp-server && uv run ruff check .`（若环境未安装 ruff，先安装开发工具）

## DevFlow
本项目使用 DevFlow 管理开发流程。配置见 `.devflow/manifest.yaml`。
- 新功能：`/devflow start "需求描述"`
- 修 bug：`/devflow fix "bug 描述"`
- 查看状态：`/devflow status`
- 继续中断流程：`/devflow next`
