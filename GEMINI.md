# Workspace Rules & Guidelines

## 1. 语言与沟通规范 (Communication Language)
- **始终使用中文回答**：无论用户使用英文、中文或中英混杂提问，所有解释、分析与总结均必须使用清晰、准确的**简体中文**输出（专业术语、代码标识符及标准缩写可保留英文对照）。

## 2. 特性开发与 Git 提交流程 (Git Workflow)
- **每个特性开发完成后自动 Commit & Push**：
  1. 每当完成一个新特性开发、代码重构或仿真阶段新增后，先运行项目（如 `uv run bank-card`）验证代码无报错；
  2. 验证通过后，**立即自动执行 `git add`、`git commit`（使用清晰的 Conventional Commits 消息，如 `feat: ...`）以及 `git push`**，将改动同步至远端仓库，无需等待用户额外提醒。
