# DECP 工作进度（PROGRESS）

> **工作契约**：任务开始前读取本文件确认当前状态与未竟事项；任务结束时追加更新"本次完成"与"遗留项"，保持本文件为最新权威状态。条目含日期，便于追溯。

---

## 2026-08-19 · 遗留项收敛（participant 下沉 SQL / soul 校验 / 文档清理 / 打 tag）

### 本次完成 ✅

**承接 2026-08-18 遗留项 1/2/6/9/10，全部闭环。测试 92 → 95 passed（含 PG 实测）。**

| 遗留项 | 内容 | 验证 |
| --- | --- | --- |
| 1 | `meeting_list` participant 过滤从 Python 内存过滤**下沉到 SQL 层**（`_meeting_participant_cond`）：SQLite 用 JSON 转义 LIKE（`json.dumps` 匹配存储的 `\uXXXX` 形式），PG 用 JSONB `@>`（`cast(JSONB).contains([participant])` 传 list 而非字符串） | SQLite + PG 双实测通过 |
| 2 | `bug_get_many` 确认已为 `@abstractmethod`（非可选）且 `ORMStorage` 完整实现，`__abstractmethods__` 为空；补 `test_task_board_embeds_bug_subcards` 覆盖 board include_bugs 路径 | 15 passed |
| 6 | `SkillCatalog` 增加 `SkillDef.is_injection` 属性 + `triggers()` 方法；`missing_tools` 显式跳过注入型技能；补 `test_skill_catalog_soul_excluded_from_triggers` | 6 passed |
| 9 | CLAUDE.md 存储层旧表述清理：目录结构更新为 `orm_backend.py`/`orm.py` 单一 ORM 实现，第 7 节重写（JsonType 切换 / build_dsn 编码 / 抽象强制实现） | — |
| 10 | 打 tag v0.2.0 触发 `release-skills.yml`（测试 + npm/skills 同步 + 打包不含 soul + 上传 Release 资产） | 本地 `package-skills.sh --check` 预演通过 |

**关键决策**
- participant 过滤的 SQLite LIKE **不能用 `escape="\\"`**：存储文本的 `\uXXXX` 含反斜杠，设转义字符会把 `\u` 解释为转义序列导致命中失败。
- PG JSONB `contains()` 传 **Python list**（`[participant]`）而非 JSON 字符串——传字符串会绑定为 VARCHAR，`@>` 右侧类型不匹配报错。
- soul 注入型语义基于 manifest（`depends_on_tools`/`depends_on_mcp_servers` 均空），与 skills/README.md 约定一致。

### 遗留项 🔲（更新后）

1. **看板无容量/燃尽视图**——sprint 只有轻量排期，容量管理与燃尽图未实现（设计文档第 11 章已列为可调整项，属新功能，单独排期）。
2. **会议提取为纯启发式**——未接 LLM 增强通道（设计保留：外部 LLM 提取后结构化入参 `meeting.submit`）。
3. **`_parse_owner`/`_parse_due`/`_classify_kind` 词典硬编码**——后续可配置化（注入词典）。
4. **bug 完整生命周期增强**：附件（截图/日志）存储、重复上报自动检测（复用 `requirement.find_similar` 相似度）未实现。
5. **CLI/报告导出**：`task.board`/`bug.search` 导出 Excel、demo 指令样例未补充。

---

## 2026-08-18 · 团队任务看板 + 缺陷域 + 会议纪要管理

### 本次完成 ✅

**新功能（五数据域全链路，已提交 3 个 commit 并推送 origin/main）**

| commit | 内容 |
| --- | --- |
| `dedc44d` feat | 团队任务看板与缺陷域 + 会议纪要管理（task/bug/sprint/meeting/attachment 五数据域） |
| `64dc5a5` docs | 设计文档 `docs/task-kanban-and-meeting-minutes-design.md` + CLAUDE.md 工具清单同步 |
| `5d0ae7a` chore | 技能索引与打包脚本补齐新技能（skills/README、soul、package-skills、sync-npm-skills） |

**数据模型**（`models/__init__.py`）
- 新枚举：`TaskType`(requirement/project/tech_debt/ops/chore)、`TaskStatus`(7 列含 review/blocked)、`BugSeverity`/`BugStatus`(6 态)/`BugChannel`、`SprintStatus`、`MeetingItemKind`
- 新模型：`Task`/`TaskCreate`、`Bug`/`BugCreate`、`Sprint`/`SprintCreate`、`TaskLog`、`ActionItem`(强类型)、`MeetingMinutes`/`MeetingMinutesCreate`、`Attachment`
- `SourceRef.ref_type` 扩展 `meeting/sprint/bug/requirement`

**存储层**（`storage/base.py` + `orm.py` + `orm_backend.py`）
- 6 张新表：`task`/`bug`/`sprint`/`task_log`/`meeting_minutes`/`attachment`（SQLite/PG 双后端）
- 44 个新抽象方法 + CRUD，含 `bug_get_many`、`task_reorder`、workspace 隔离强制

**Service 层**（`services/__init__.py`）
- `TaskService`：看板 board（列分组+排序+缺陷子卡片）、move 状态机（blocked 强制原因、reopen 清 done_at）、方案链接双写、需求转任务（仅已审核）、双向关联缺陷
- `BugService`：`TRANSITIONS` 状态机（非法跳转/wonfix 无原因拒绝/reopen）、四域双向关联、`from_feedback` 严重级映射、create 带 task_ids 反向同步
- `MeetingMinutesService`：`_extract` 启发式提取（摘要/决议/待办/责任人/截止/开发事务分类）、`to_tasks`/`to_bugs`（dry_run 预览 + **幂等守卫**、宽容非成员 owner）、`_classify_kind`/`_classify_type`
- `SprintService`、`AttachmentService`

**MCP 工具层**：23 → **54** 个工具，全部走 `_authorize` 身份校验，direct/client 双模式一致

**Agent/Skill 层**：3 个新 Skill 类 + `register_defaults` + `_INTENT_KEYWORDS` 路由 + 3 组 `skills/*/SKILL.md` + `manifest.json`（含 Prompt Injection 防护约束）+ 分发链路（zip 打包 + npm 镜像 + README/soul 索引同步）

**Review 修复**：专项 review agent 发现 9 个真实缺陷全部闭环
1. `_parse_due` 正则 `[星期天]` 字符类 bug（下周三解析失败）
2. `task.update`/`bug.update` 字符串 due_at 崩溃 + task_log datetime 无法 JSON 序列化
3. `task.link_requirement` feedback_ids 永远为空（误读 dict 实际存字符串）
4. SQLite 中文参与人 LIKE 搜索失效（`\uXXXX` 转义）→ Python 层过滤
5. `_drop_identity` 误删 `MeetingMinutes.submitted_by`
6. `meeting.to_tasks`/`to_bugs` 非幂等（重试产生重复 backlog）
7. `_parse_owner` 时间括号/复合前缀误判
8. `bug.create(task_ids)` 不反向同步 task.bug_ids
9. `_JSON_COLUMNS` 缺 `extra`/`structured` + `_serialize_refs` dict 破坏

**测试**：92 passed（原 56 + 新 27 + review 回归 9）

> 遗留项已迁移至 2026-08-19 条目：1/2/6/9/10 已闭环，3/4/5/7/8 继续保留。
