# 团队任务管理（看板）与会议纪要管理 —— 综合设计与分析

> DECP · Federated Digital Employee Collaboration Platform
> 基于现有「产品需求收集、整理与分析」闭环的扩展设计：新增 **task 看板**、**bug 缺陷域**、**meeting_minutes 会议纪要** 三个数据域，
> 打通「需求 → 开发任务 → 排期 → 跟踪」与「会议纪要 → 存档 → 待办任务化」两条新链路。
> 本文为权威设计输入（`docs/product-requirement-analysis-scenario_Version2.svg`）在团队执行层的延伸。

---

## 0. 综合分析与现状盘点

### 0.1 现有系统已具备、可直接复用的能力

| 层 | 现有实现 | 新能力如何复用 |
| --- | --- | --- |
| 存储层 | `StorageBackend` 抽象 + `ORMStorage`（SQLAlchemy 2.0，SQLite/PG 双后端，JsonType=SQLite TEXT/PG JSONB，`_ensure_columns` 幂等补列迁移） | 新增 `TaskOrm`/`BugOrm`/`SprintOrm`/`TaskLogOrm`/`MeetingMinutesOrm` 即挂即用，`create_all` 自动建表，旧库补列走 `_ensure_columns` |
| Service 层 | `FeedbackService`（启发式结构化抽取）/ `RequirementService`（相似度去重聚类）/ `WorkspaceService`（多租户成员） | 会议纪要提取复用 `_extract` 的启发式风格；bug 与需求相似度去重复用 `similarity` |
| MCP 工具层 | `DecpTools.TOOL_BINDINGS` 统一登记 23 个工具；`_authorize()` 统一身份解析 + 成员校验；`utils.tool_result` 统一响应 | 新工具一律登记 `TOOL_BINDINGS` + `_TOOL_DESCS`，走同一 `_authorize`，保证 direct / client 双模式一致 |
| 多租户 | `workspace_id` 全量隔离，`WorkspaceService.assert_member` 成员校验，身份来源 `ctx.meta > 显式参数 > 默认身份` | 新数据域全部携带 `workspace_id`，复用同一身份与隔离模型 |
| 来源追溯 | `SourceRef(ref_type, ref_id, detail)` | 会议待办任务/ bug 用 `source_refs` 反查来源；`ref_type` 扩展 `meeting/sprint/bug` |
| Agent/Skill | `SkillRegistry` + `skills/*/SKILL.md` + 意图关键词路由 | 新增 `task_management` / `meeting_minutes` 两个技能，同构注册 |
| 审核不可绕过 | 需求草稿必须人工 review 才能入库 | 看板 `done` 由人工流转触发，任务创建不自动改需求状态 |

### 0.2 现状盲区（本次新增解决的）

1. **需求止步于"已审核"**：`requirement.review` 通过（accepted/merged）后，没有继续往"研发排期/开发/上线"推进的载体——需求闭环缺执行段。
2. **bug / 项目需求无落点**：现有 `requirement` 面向客户反馈驱动的产品需求，研发 bug、项目级任务（里程碑/事务）、技术债、运营任务没有数据域。
3. **会议知识不沉淀**：会议纪要只是文本，无结构化存档，其中的待办/决议/行动项无法进入执行跟踪。
4. **无排期与进度视图**：没有迭代（sprint）、截止日期、负责人、看板列流转，无法"像 GitHub 看板一样"管理。

---

## 1. 总体架构：新增数据域在四层中的落位

```
┌─────────────────────────────────────────────────────────────┐
│ 数字员工 Agent / Skill 层                                     │
│  · 新增技能：task_management（看板/排期/待办）                   │
│              meeting_minutes（会议纪要→存档→待办任务化）          │
├─────────────────────────────────────────────────────────────┤
│ MCP 工具层（DecpTools，23 → 40 个工具）                         │
│  · 新增 task.* / bug.* / sprint.* / meeting.* 四组工具          │
│  · 全部登记 TOOL_BINDINGS + _TOOL_DESCS，走 _authorize         │
├─────────────────────────────────────────────────────────────┤
│ Service 层                                                    │
│  · 新增 TaskService（看板/排期/流转/审计/方案链接）                │
│  · 新增 BugService（缺陷全生命周期/关联各数据域）                  │
│  · 新增 MeetingMinutesService（提取/存档/待办任务化）             │
│  · RequirementService 扩展：accept/merge → 可转开发任务          │
├─────────────────────────────────────────────────────────────┤
│ 存储层（StorageBackend + ORMStorage，SQLite/PG 共用）           │
│  · 新增表：task / bug / bug_relation / sprint / task_log /      │
│            meeting_minutes / attachment                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 数据模型设计

### 2.0 新增枚举常量（models/__init__.py）

```python
# 任务类型：研发需求 / 缺陷 / 项目任务 / 技术债 / 运营 / 事务性任务
TaskType = Literal["requirement", "bug", "project", "tech_debt", "ops", "chore"]

# 任务看板状态（列）：类 GitHub 看板 + review/blocked
TaskStatus = Literal["backlog", "todo", "in_progress", "review", "blocked", "done", "cancelled"]

# 迭代状态
SprintStatus = Literal["planned", "active", "closed"]

# 会议待办分类：开发任务 / 事务性任务
MeetingItemKind = Literal["dev", "chore"]

# 缺陷严重级
BugSeverity = Literal["critical", "high", "medium", "low"]
# 缺陷状态（全生命周期，简化 6 态：去掉 triaged）
BugStatus = Literal["new", "confirmed", "in_progress", "fixed", "verified", "closed", "wonfix"]
# 缺陷优先级（直接复用 Priority：P0-P3）
# 缺陷来源渠道
BugChannel = Literal["feedback", "meeting", "manual", "qa", "monitor", "api"]
```

> `SourceRef.ref_type` 由 `Literal["feedback","ticket","excel","api","manual"]` 扩展为
> `Literal["feedback","ticket","excel","api","manual","meeting","sprint","bug","requirement"]`（向后兼容）。

### 2.1 task（任务看板核心实体）

```python
class Task(BaseModel):
    """团队任务：研发需求 / 项目 / 技术债 / 运营 / 事务任务，看板排期与跟踪。

    注意：bug 走独立数据域（Bug），不在 Task 内承载；任务通过 source_refs 关联 bug。
    """
    model_config = ConfigDict(extra="ignore")

    id: str                                # ts-xxx
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")

    type: TaskType = "requirement"         # 任务来源分类（不含 bug，见下）
    title: str = Field(min_length=1, description="任务标题")
    description: str = ""
    module: str | None = None              # 对齐 Feedback/Requirement.module

    status: TaskStatus = "backlog"         # 看板列（默认进待办池）
    priority: Priority = "P2"              # 复用现有 P0-P3

    assignee: str | None = None            # 责任人（须为 workspace 已批准成员）
    sprint_id: str | None = None           # 排期迭代（关联 sprint.id）
    planned_start: datetime | None = None  # 计划开始时间（排期）
    due_at: datetime | None = None         # 计划截止时间（排期）
    estimate: float | None = None          # 估时（小时，可选）

    order: int = 0                         # 看板列内排序（拖拽持久化）

    # ---- 方案链接（上传方案自动管理） ----
    plan_links: list[str] = Field(default_factory=list)   # 方案文档链接（上传即自动登记）

    # ---- 关联与来源 ----
    requirement_id: str | None = None      # type=requirement 时引用需求 id
    feedback_ids: list[str] = Field(default_factory=list)   # 继承需求的关联反馈
    bug_ids: list[str] = Field(default_factory=list)        # 关联缺陷（bug 独立域）
    source_refs: list[SourceRef] = Field(default_factory=list)  # 来源（meeting/requirement 等）
    labels: list[str] = Field(default_factory=list)         # 标签（继承需求 tags 或自定义）
    extra: dict = Field(default_factory=dict)               # 类型专属扩展字段

    # ---- 时间戳 / 审计 ----
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None     # 进入 in_progress 时间
    done_at: datetime | None = None        # 进入 done 时间
    archived: bool = False
    archived_at: datetime | None = None
    archived_by: str | None = None
```

> **方案链接机制（plan_links）**：
> - 任务创建时可带 `plan_links`；`task.upload_plan(tid, url)` 工具接收方案文档链接（可配文件上传服务返回的 URL，或本地报告路径）。
> - 上传即自动登记进 `plan_links`（去重），并在 `task_log` 留痕（action=plan_added）。不做文件系统存取，只做"链接管理"——文件存储/对象存储由上层平台承载，DECP 存链接与元信息。
> - `task.get` 返回 `plan_links`，看板卡片可显示"有方案"标记。

**TaskStatus（看板列）**：

| 列 | 语义 | 流转来源 |
| --- | --- | --- |
| `backlog` | 待办池（未排期） | 创建默认 |
| `todo` | 已排期待开发 | 排期/拖拽 |
| `in_progress` | 进行中 | 开始开发 |
| `review` | 待评审/待验收 | 开发完成进入评审 |
| `blocked` | 阻塞（缺资源/依赖/决策） | 人工标记阻塞，可备注原因 |
| `done` | 已完成 | 人工关闭 |
| `cancelled` | 已取消 | 人工关闭 |

**TaskType（任务来源分类）**：
- `requirement` — 由已审核需求转化（`requirement_id` 引用）
- `project` — 项目级任务 / 里程碑
- `tech_debt` — 技术债（重构、优化、架构调整）
- `ops` — 运营任务（活动、配置、数据维护）
- `chore` — 事务性任务（跟进、协调、文档、安排）
- ~~`bug`~~ — **不在此列**：bug 走独立数据域，任务经 `bug_ids`/`source_refs` 关联

### 2.2 bug（缺陷独立数据域，含与各数据域关联）

```python
class Bug(BaseModel):
    """缺陷：独立全生命周期管理，与反馈/需求/任务/会议/方案多域关联。"""
    model_config = ConfigDict(extra="ignore")

    id: str                                # bg-xxx
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")

    title: str = Field(min_length=1, description="缺陷标题")
    description: str = ""                  # 详细描述
    module: str | None = None              # 影响模块
    severity: BugSeverity = "medium"       # 严重级
    priority: Priority = "P2"              # 处理优先级（独立于严重级）
    status: BugStatus = "new"              # 全生命周期
    channel: BugChannel = "manual"         # 来源渠道

    # ---- 复现信息 ----
    environment: str | None = None         # 环境（生产/测试/版本等）
    reproduce_steps: str | None = None     # 复现步骤
    expected: str | None = None            # 期望行为
    actual: str | None = None              # 实际行为

    # ---- 责任人/排期 ----
    assignee: str | None = None            # 处理人（须为 workspace 成员）
    reporter: str = "maintainer"           # 报告人
    sprint_id: str | None = None           # 排期迭代（可选）
    due_at: datetime | None = None         # 计划修复截止
    fix_version: str | None = None         # 修复版本号

    # ---- 方案链接 ----
    plan_links: list[str] = Field(default_factory=list)   # 修复方案链接

    # ---- 多数据域关联（核心） ----
    feedback_ids: list[str] = Field(default_factory=list)       # 关联客户反馈
    requirement_ids: list[str] = Field(default_factory=list)    # 关联需求（缺陷对应的需求）
    task_ids: list[str] = Field(default_factory=list)           # 关联开发任务（修复任务）
    meeting_ids: list[str] = Field(default_factory=list)        # 关联会议纪要（从纪要提交/提及）
    source_refs: list[SourceRef] = Field(default_factory=list)  # 通用来源引用
    labels: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)                   # 扩展（如附件/截图/日志等）

    # ---- 时间戳 / 审计 ----
    created_at: datetime
    updated_at: datetime
    fixed_at: datetime | None = None       # 标记 fixed 时间
    closed_at: datetime | None = None      # 关闭时间
    archived: bool = False
    archived_at: datetime | None = None
    archived_by: str | None = None
```

**BugStatus（全生命周期，简化 6 态）**：

| 状态 | 语义 | 流转来源 |
| --- | --- | --- |
| `new` | 新提交（含分诊：确认归属/优先级） | 创建默认 |
| `confirmed` | 已确认可复现 | 复现确认 |
| `in_progress` | 修复中 | 开始修复 |
| `fixed` | 已修复待验证 | 提交修复 |
| `verified` | 已验证通过 | 验证人确认 |
| `closed` | 已关闭 | 人工关闭 |
| `wonfix` | 不修复（设计如此/低优先） | 人工关闭并备注原因 |

> 简化决策：去掉 `triaged` 态，分诊（确认归属/优先级）并入 `new`，提交后直接人工确认复现（`confirmed`）即可进入修复，减少一次人工操作。

> **设计要点**：bug 独立成域，保留完整的严重级/复现信息/生命周期；同时与 **feedback / requirement / task / meeting** 四域双向外键关联（`feedback_ids`/`requirement_ids`/`task_ids`/`meeting_ids`），不破坏各域独立性，仅做引用。典型场景：
> - 客户反馈 → 提炼为缺陷（`bug.feedback_ids` 引用，`channel=feedback`）
> - 缺陷 → 关联需求（`bug.requirement_ids`，若需求修复更合适）
> - 缺陷 → 关联修复开发任务（`bug.task_ids`，任务 `bug_ids` 反向）
> - 会议提到缺陷（`bug.meeting_ids`，`channel=meeting`）

### 2.3 sprint（迭代/排期）

```python
class Sprint(BaseModel):
    """迭代排期：一组任务的排期容器，按时间轴跟踪。"""
    model_config = ConfigDict(extra="ignore")

    id: str                                # sp-xxx
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    name: str = Field(min_length=1, description="迭代名，如 Sprint 24-08")
    goal: str = ""                         # 迭代目标
    start_date: datetime
    end_date: datetime
    status: SprintStatus = "planned"
    created_at: datetime
```

> `task.sprint_id`/`bug.sprint_id` 关联迭代；按 `end_date` 升序即排期时间轴。
> 轻量方案：不做复杂 capacity 管理，先满足"排期 → 看板 → 跟踪"。

### 2.4 task_log（任务流转审计 / 活动流）

```python
class TaskLog(BaseModel):
    """任务/缺陷活动流：状态流转/指派/排期变更/方案上传留痕，类 GitHub issue 时间线。"""
    model_config = ConfigDict(extra="ignore")

    id: int                                # 自增
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    task_id: str                           # 关联任务（bug 流转也记在同一活动流，经 task_id 或 entity 字段）
    entity: str = "task"                   # task | bug（缺陷也复用审计流）
    action: str                            # created/moved/assigned/sprint_changed/due_changed/
                                           # plan_added/closed/status_changed
    from_status: str | None = None         # 流转前状态
    to_status: str | None = None           # 流转后状态
    field: str | None = None               # 变更字段名（可选）
    old_value: JsonType | None = None      # 旧值
    new_value: JsonType | None = None      # 新值
    actor: str                             # 操作者 user_id
    comment: str | None = None             # 备注（blocked 原因/wonfix 原因等）
    created_at: datetime
```

### 2.5 meeting_minutes（会议纪要）

```python
class ActionItem(BaseModel):
    """会议待办：强类型条目，供批量任务化。"""
    model_config = ConfigDict(extra="ignore")

    desc: str = Field(min_length=1, description="待办描述")
    owner: str | None = None               # 责任人
    due: date | None = None                # 截止日期
    kind: MeetingItemKind = "chore"        # dev | chore（启发式判定或显式指定）
    note: str | None = None                # 备注（可选）


class MeetingMinutes(BaseModel):
    """会议纪要：原文存档 + 启发式提取的摘要/决议/待办，结构化沉淀。"""
    model_config = ConfigDict(extra="ignore")

    id: str                                # mt-xxx
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")

    title: str = Field(min_length=1, description="会议主题")
    held_at: datetime = Field(default_factory=utcnow, description="会议时间（默认提交时间）")
    participants: list[str] = Field(default_factory=list)   # 参与人
    location: str | None = None            # 会议地点/线上链接
    recording_url: str | None = None       # 录屏/录音链接
    agenda: list[str] = Field(default_factory=list)         # 会议议程
    module: str | None = None              # 关联模块（对齐 feedback.module）

    raw_text: str = Field(min_length=1, description="原始纪要全文（存档原文，不可丢失）")
    summary: str = ""                      # 提取的会议内容摘要
    decisions: list[dict] = Field(default_factory=list)    # 决议：[{item, owner?}]
    action_items: list[ActionItem] = Field(default_factory=list)  # 待办（强类型）
    keywords: list[str] = Field(default_factory=list)      # 提取关键词

    submitted_by: str = "maintainer"
    source_ref: str | None = None          # 外部来源（会议平台链接等）

    created_at: datetime
    updated_at: datetime
    archived: bool = False
    archived_at: datetime | None = None
    archived_by: str | None = None
```

**提取（`raw_text` → structured，启发式，复用 `FeedbackService._extract` 风格）**：

| 片段 | 识别方式 |
| --- | --- |
| 主题/时间/参与人/地点/录屏 | 首部规则：标题行、`时间/地点/参会人/录屏/议程` 字段行 |
| 摘要 summary | 首段或"会议内容/进展"段 |
| 决议 decisions | 匹配 `决议/结论/决定/Decisions` 段头 → 逐条（编号行/`-`行） |
| 待办 action_items | 匹配 `待办/行动项/下一步/TODO/Action Items` 段头 → 逐条，构造 `ActionItem` |
| 待办责任人/截止 | 条目内正则 `（张三，明天）/ 张三：…/（本周五前）` |
| 待办分类 kind | 关键词判定（见 3.3），dev 或 chore |
| 关键词 keywords | 复用 n-gram/关键词提取或显式标签 |

> 与现有架构一致：**纯启发式、无 LLM 依赖**，确定性强、可测试。
> LLM 增强为可选升级路径：外部 LLM agent 提取后，以结构化字段调用 `meeting.submit`（同 `feedback.submit` 双通道），内核不改。

### 2.6 attachment（方案附件/链接登记，通用）

```python
class Attachment(BaseModel):
    """通用附件/链接登记：方案上传自动管理链接，跨 task/bug/meeting 复用。"""
    model_config = ConfigDict(extra="ignore")

    id: str                                # at-xxx
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    entity: str                            # task | bug | meeting | requirement
    entity_id: str                         # 关联实体 id
    url: str = Field(min_length=1, description="文件/链接地址")
    name: str = ""                         # 文件名/标题
    mime: str | None = None                # 类型（如 application/pdf、doc、plan）
    size: int = 0                          # 字节（可选）
    uploaded_by: str = "maintainer"
    created_at: datetime
```

> **方案链接"自动管理"的完整语义**：
> - `task.upload_plan(tid, url, name)` / `bug.upload_plan(bgid, url, name)`：登记附件 → 同步写入 `task.plan_links`/`bug.plan_links`（冗余，便于看板卡片直接读），并留 task_log。
> - `attachment.list(entity, entity_id)` 可查该实体全部方案/附件。
> - 冗余 `plan_links` 与附件表双写，保证看板查询零 join、又可审计追溯。

---

## 3. Service 层设计

### 3.1 TaskService（看板/排期/流转/审计/方案链接）

```
class TaskService:
    def __init__(self, storage) -> None

    async def create(self, data: TaskCreate, *, workspace_id) -> Task
        # type=requirement 时校验 requirement 存在且已审核完结（accepted/merged）；
        # assignee 若指定须为 workspace 已批准成员
        # 初始化 task_log(created)

    async def update(self, tid, fields, *, actor, workspace_id) -> Task
        # 白名单字段更新；记录 task_log（field/old/new）
        # assignee 变更 → action=assigned；sprint_id/due_at 变更 → sprint_changed/due_changed

    async def move(self, tid, status, *, actor, order=None, comment=None, workspace_id) -> Task
        # 看板拖拽：状态流转 + 列内 order 持久化
        # in_progress → 记 started_at；done → 记 done_at
        # blocked → 允许 comment 记录原因
        # 记录 task_log(action=move, from_status, to_status, comment)

    async def reorder(self, tid, order, *, workspace_id) -> Task   # 列内排序
    async def upload_plan(self, tid, url, *, name=None, actor, workspace_id) -> Task
        # 上传方案链接：登记 attachment(entity=task) + 同步 plan_links 冗余 + task_log(plan_added)

    async def board(self, *, status=None, sprint_id=None, assignee=None, type_=None,
                    include_bugs=True, workspace_id) -> dict
        # 返回 {columns: {backlog: [task...], todo: [...], in_progress, review, blocked, done, ...}}，
        # 列内按 order 排序（类 GitHub 看板）
        # include_bugs=True：任务卡下内嵌关联 bug 子卡片（bug.get 反向摘要，含状态/严重级）

    async def list(self, *, status, type_, sprint_id, assignee, limit, offset, include_archived, workspace_id) -> list[Task]
    async def get(self, tid, *, include_log=True, workspace_id) -> Task | None
    async def log(self, tid, *, workspace_id) -> list[TaskLog]      # 活动流
    async def link_requirement(self, rid, *, actor, workspace_id) -> Task
        # 需求 → 开发任务：继承 title/priority/module/tags/feedback_ids/source_refs，
        # type=requirement，requirement_id=rid，状态 backlog
    async def link_bug(self, tid, bug_id, *, workspace_id) -> Task
        # 任务关联缺陷（双向：task.bug_ids + bug.task_ids）

    async def archive / restore ...
    async def count(self, *, status=None, workspace_id) -> int       # 看板列计数
```

### 3.2 BugService（缺陷全生命周期/多域关联）

```
class BugService:
    def __init__(self, storage) -> None

    async def create(self, data: BugCreate, *, workspace_id) -> Bug
        # channel=feedback 时校验 feedback_ids 存在；assignee/reporter 成员校验
        # 初始化 task_log(entity=bug, action=created)

    async def update(self, bgid, fields, *, actor, workspace_id) -> Bug
        # 白名单更新 + 留痕

    async def transition(self, bgid, status, *, actor, comment=None, workspace_id) -> Bug
        # 状态机：new→confirmed→in_progress→fixed→verified→closed / wonfix（6 态，无 triaged）
        # fixed → 记 fixed_at；closed → 记 closed_at；wonfix 须 comment 原因

    async def link_feedback(self, bgid, feedback_ids, *, workspace_id) -> Bug     # 关联客户反馈
    async def link_requirement(self, bgid, requirement_ids, *, workspace_id) -> Bug # 关联需求
    async def link_task(self, bgid, task_ids, *, workspace_id) -> Bug             # 关联修复任务（双向）
    async def upload_plan(self, bgid, url, *, name=None, actor, workspace_id) -> Bug # 修复方案链接

    async def search(self, *, status, severity, priority, assignee, module, channel,
                     limit, offset, include_archived, workspace_id) -> list[Bug]
    async def get(self, bgid, *, include_relations=True, workspace_id) -> Bug | None
        # include_relations：返回关联 feedback/requirement/task/meeting 摘要
    async def board / list / count ...
    async def archive / restore ...

    @staticmethod
    def _from_feedback(fb, *, actor) -> Bug
        # 客户反馈 → 缺陷：title=content 截断，severity 从 fb.structured.impact_severity 映射
```

### 3.3 MeetingMinutesService（提取/存档/待办任务化）

```
class MeetingMinutesService:
    def __init__(self, storage) -> None

    async def submit(self, data: MeetingMinutesCreate, *, workspace_id) -> MeetingMinutes
        # 启发式提取 raw_text → summary/decisions/action_items(ActionItem)/keywords（结构化存档）

    async def get / list / search(*, module=None, participant=None...) / count
    async def to_tasks(self, mid, *, actor, workspace_id, dry_run=False) -> list[Task]
        # 将纪要的每条 action_item 批量创建为 task，source_refs 记录 meeting 来源；
        # dry_run 返回将生成的清单供人工确认

    async def to_bugs(self, mid, *, actor, workspace_id, dry_run=False) -> list[Bug]
        # 纪要中明确的缺陷描述（如 "发现 xxx bug"）→ 独立 Bug 域，channel=meeting，
        # meeting_ids=[mid] 关联

    @staticmethod
    def _classify_kind(desc: str) -> str:      # dev | chore
        # 开发类关键词：开发、实现、修复、接口、重构、优化、测试、部署、联调、排查、
        #               代码、SQL、前端、后端、上线、发布、升级…
        # 事务类关键词：跟进、协调、安排、确认、沟通、对齐、文档、评审、会议、催办、
        #               整理、通知、汇报、培训…
        # 命中 dev 词 → dev；否则 chore

    @staticmethod
    def _extract(raw_text) -> dict
        # 段落切分 → 段头识别 → 条目解析 → ActionItem（责任人/截止/分类）
```

**待办 → 任务映射规则**：

| 会议 action_item.kind | 生成 task.type | 说明 |
| --- | --- | --- |
| `dev` | `project` 或 `requirement` | 开发任务（如对接到需求再补 requirement_id） |
| `chore` | `chore` | 事务性任务 |
| 命中技术债词（重构/优化/架构/技术债） | `tech_debt` | 技术债任务 |
| 命中运营词（活动/配置/数据维护/运营） | `ops` | 运营任务 |

**纪要中的缺陷 → Bug 域**（`meeting.to_bugs`）：识别 `发现/存在/报错/异常` 且明确缺陷描述的段落 → 生成 `Bug`（`channel=meeting`, `meeting_ids=[mid]`）。

---

## 4. MCP 工具层（新增 17 个，23 → 40）

全部登记 `DecpTools.TOOL_BINDINGS` + `_TOOL_DESCS`，方法签名带 `ctx` + `user_id`/`workspace_id` 显式参数，走 `_authorize()`。

| 数据域 | 工具 | 说明 |
| --- | --- | --- |
| task | `task.create` | 创建任务（type/priority/assignee/sprint/planned_start/due） |
| task | `task.update` | 更新字段（白名单，留痕） |
| task | `task.move` | 看板拖拽：状态流转（含 review/blocked）+ 列内排序 + 阻塞原因备注 |
| task | `task.board` | 看板视图：按列分组返回（类 GitHub），可按 sprint/assignee/type 过滤；`include_bugs` 内嵌关联 bug 子卡片 |
| task | `task.list` | 任务列表（status/type/sprint/assignee 过滤，include_archived） |
| task | `task.get` | 任务详情 + 活动流（task_log）+ 方案链接 |
| task | `task.upload_plan` | 上传方案链接（自动登记 plan_links + 附件 + 留痕） |
| task | `task.link_requirement` | 已审核需求 → 开发任务（一键转化） |
| task | `task.link_bug` | 任务关联缺陷（双向） |
| task | `task.archive` / `task.restore` | 软归档（对齐 requirement） |
| bug | `bug.create` | 创建缺陷（severity/priority/reproduce_steps/environment 等） |
| bug | `bug.update` | 更新缺陷字段 |
| bug | `bug.transition` | 缺陷状态机流转（new→…→closed/wonfix，留痕） |
| bug | `bug.search` | 缺陷查询（status/severity/priority/assignee/module/channel） |
| bug | `bug.get` | 缺陷详情 + 多域关联摘要（feedback/requirement/task/meeting） |
| bug | `bug.link_feedback` / `link_requirement` / `link_task` | 关联各数据域（可合并为一个 `bug.link`） |
| bug | `bug.upload_plan` | 修复方案链接（自动登记） |
| bug | `bug.from_feedback` | 客户反馈一键转缺陷 |
| bug | `bug.archive` / `bug.restore` | 软归档 |
| sprint | `sprint.create` | 创建迭代（name/goal/start/end） |
| sprint | `sprint.list` | 迭代列表（active 优先） |
| meeting | `meeting.submit` | 输入纪要原文 → 启发式提取 → 结构化存档（返回提取结果） |
| meeting | `meeting.get` / `list` / `search` | 纪要查询 |
| meeting | `meeting.to_tasks` | 纪要待办 → 批量创建任务（dry_run 预览） |
| meeting | `meeting.to_bugs` | 纪要缺陷 → 批量创建缺陷（dry_run 预览） |
| attachment | `attachment.upload` | 通用方案/附件上传登记（task/bug/meeting 复用） |
| attachment | `attachment.list` | 某实体的附件/方案链接列表 |

> 工具名与实现统一：新方法同样落在 `DecpTools.TOOL_BINDINGS`，`register_all_tools`（MCP 层）与 `DirectBackend`（Skill direct 模式）自动共用——**跨模式命名一致性由现有机制保证，无需额外改动**。

---

## 5. Agent / Skill 层

### 5.1 新增技能

```
task_management   # 触发：任务、看板、排期、待办、进度、迭代、sprint、技术债、运营任务
                  # 依赖：task.* / sprint.* / bug.search / requirement.search / requirement.get
bug_management    # 触发：缺陷、bug、报错、复现、修复
                  # 依赖：bug.* / task.* / feedback.search / meeting.search
meeting_minutes   # 触发：会议纪要、会议记录、纪要、行动项、会议待办
                  # 依赖：meeting.* / task.create / bug.create
```

- 新增 `src/decp_core/agent/skills/task_management.py`、`bug_management.py`、`meeting_minutes.py`（`BaseSkill` 子类，动作分支式，同 `RequirementAnalysisSkill` 结构）
- `SkillRegistry.register_defaults()` 注册；`agent/__init__.py` 的 `_INTENT_KEYWORDS` 增加意图关键词
- 新增 `skills/task-management/SKILL.md` + `manifest.json`、`skills/bug-management/SKILL.md` + `manifest.json`、`skills/meeting-minutes/SKILL.md` + `manifest.json`（对齐现有 skill 目录格式，`depends_on_tools` 列出新工具）

### 5.2 典型自然语言指令

- 「把已审核的需求 REQ-xxx 转成开发任务并排到 Sprint 24-08，责任人 Alice」
- 「创建一个看板，展示当前迭代进行中的任务和被阻塞的任务」
- 「这是今天的会议纪要：……，提取待办并列入任务计划，分开发任务和事务任务」
- 「会议纪要里有哪些是开发任务、哪些是事务任务、哪些是技术债？」
- 「客户反馈 FB-xxx 疑似缺陷，转成 bug 并关联原反馈」
- 「上传这个需求方案的链接，绑定到任务 ts-xxx」

---

## 6. 关键业务流程

### 6.1 需求 → 开发任务（打通需求闭环执行段）

```
requirement.review(accept/merge)        产品经理审核通过
        │
        ▼
task.link_requirement(requirement_id)   一键转开发任务
        │  继承 title/priority/module/tags/feedback_ids/source_refs
        │  type=requirement, requirement_id=req.id, status=backlog
        ▼
sprint 排期 (task.update sprint_id/planned_start/due_at)
        ▼
看板拖拽 (task.move: todo → in_progress → review → done) + task.upload_plan(方案)
```

> 约束：仅 `accepted` / `merged` 状态可转任务（人工审核不可绕过，与现有设计决策一致）；
> 转化不改变需求状态，需求审核记录保留，任务独立演进。

### 6.2 会议纪要 → 存档 + 待办任务化 + 缺陷识别

```
meeting.submit(raw_text)
   │  启发式提取：summary / decisions / action_items(ActionItem) / keywords
   ▼
存档 meeting_minutes（原文 raw_text 保留，含 location/recording_url/agenda）
   │
   ├─ meeting.to_tasks(dry_run=True)  预览将生成的任务清单
   │    每条 action_item → task：
   │      kind=dev  → type=project/requirement；技术债词 → tech_debt
   │      kind=chore → type=chore；运营词 → ops
   │      owner/due 继承；source_refs=[{ref_type:meeting, ref_id:mid}]
   │    确认后 dry_run=False 批量入库 → 看板 backlog
   │
   └─ meeting.to_bugs(dry_run=True)   预览将生成的缺陷清单
        识别"发现/存在/报错/异常"段落 → Bug(channel=meeting, meeting_ids=[mid])
        确认后批量入库 → bug 看板
```

**双向可追溯**：任务/缺陷 → `source_refs` 反查纪要；纪要 → 任务/缺陷（按 `ref_type=meeting` 查）。 `requirement.find_similar` 的相似度算法同样可用于纪要待办去重。

### 6.3 bug 全生命周期（独立域 + 多域关联）

```
客户反馈 FB-xxx 疑似缺陷
        │
        ▼
bug.from_feedback / bug.create          → 状态 new，channel=feedback
        │  feedback_ids=[FB-xxx] 关联
        ▼
bug.transition(new → confirmed)         确认可复现（补 reproduce_steps）
        ▼
bug.link_requirement / link_task        关联需求或直接挂修复任务（双向 task.bug_ids）
        ▼
bug.transition(in_progress → fixed → verified → closed)
        │  upload_plan 登记修复方案；wonfix 需 comment 原因
        ▼
闭环：客户反馈、需求、任务、会议全部可追溯
```

### 6.4 看板排期与跟踪

- `task.board` 按列返回卡片（标题/类型/优先级/责任人/截止/标签/有方案标记），列内按 `order` 排序
- 拖拽 = `task.move`（跨列流转 + 列内 `reorder`），自动记 `started_at` / `done_at`；`blocked` 可带原因
- 排期 = `sprint` 创建 + `task.update(sprint_id, planned_start, due_at)`；`task.list(sprint_id=…, status=…)` 即迭代视图
- 跟踪 = `task.log` 活动流（谁、何时、从哪列到哪列、改了什么字段、上传了什么方案）

**任务 ↔ bug 关联展示（看板子卡片）**：

```
看板列：  backlog          in_progress
        ┌────────────────┐  ┌────────────────┐
        │ 需求 REQ-x →任务 │  │ 修复任务 ts-03  │
        │  ├ 子卡 bg-01    │  │  ├ 子卡 bg-05   │ ← bug_ids 关联的缺陷
        │  │  [high] 支付超时│  │  │  [fixed] 待验证│    （含状态/严重级/标题）
        │  └─────────────┘  │  └──────────────┘
        └────────────────┘  └────────────────┘
```

- 数据模型不变：`task.bug_ids` + `bug.task_ids` 双向引用
- **展示层**：`task.board(include_bugs=True)` 在任务卡下内嵌关联 bug 子卡片（标题 + 状态色标 + 严重级徽标），不改变看板主结构
- **导航**：点击子卡片经 `bug.get` 进入缺陷详情（含完整复现信息/多域关联/活动流）
- `task.get`/`bug.get` 双向返回关联摘要，供详情页与反查

---

## 7. 隔离、权限与审计

| 维度 | 方案 |
| --- | --- |
| 多租户隔离 | `task` / `bug` / `sprint` / `task_log` / `meeting_minutes` / `attachment` 全部含 `workspace_id`；后端查询强制 `where workspace_id`（同 feedback/requirement） |
| 身份解析 | 复用 `_authorize`：`ctx.meta > 显式参数 > 默认身份`，`assert_member` 校验成员资格 |
| 责任人约束 | `task.assignee`/`bug.assignee` 须为 workspace 已批准成员（`member_list(status=approved)` 校验） |
| 人工决策权 | 需求转任务仅限已审核状态；任务 `done`/`cancelled`、bug `closed`/`wonfix` 由人工流转触发 |
| 审计 | `task_log` 全量留痕（创建/流转/指派/排期变更/方案上传/关闭），操作者与时间入账 |
| 方案链接 | 只登记链接与元信息，不做文件存取；文件存储由上层平台承载（同现有 `data/reports/` 模式） |
| 出站控制 | 无新增出站调用；报告导出沿用 `data/reports/`（可选 `report.generate_excel` 扩展任务/bug 清单 sheet） |

---

## 8. 与现有需求数据域的衔接明细

| 衔接点 | 实现 |
| --- | --- |
| 需求 → 任务 | `task.link_requirement`；`requirement_id` 外键引用 + `source_refs` |
| 需求 → bug | `bug.requirement_ids` 引用（需求引发的缺陷） |
| bug → 任务 | `task.bug_ids` 双向引用（`bug.link_task`） |
| 反馈 → bug | `bug.feedback_ids`（`bug.from_feedback` / `channel=feedback`） |
| 会议 → 任务/bug | `source_refs=[{ref_type:meeting}]` + `bug.meeting_ids` |
| 任务/缺陷 → 会议反查 | `bug.meeting_ids`、`task.source_refs` 反查 `meeting_minutes` |
| 优先级/标签复用 | 直接复用 `Priority` 枚举与 `tags`/`labels` |
| 相似度复用 | 纪要待办去重、bug 重复上报检测复用 `decp_core.services.similarity`（字符 n-gram） |
| 审核不可绕过 | 需求状态机不因转任务而改变；新任务在 `backlog` 起步，状态机独立 |

---

## 9. 测试计划（新增 test 文件）

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_task.py` | task CRUD / 看板 move+order / review/blocked 流转 / 状态时间戳 / sprint 排期 / 归档 / 隔离 / 非成员拒绝 |
| `tests/test_bug.py` | bug 全生命周期状态机 / severity/priority / 复现信息 / 多域关联（feedback/requirement/task/meeting 双向）/ 重复上报检测 / 归档 |
| `tests/test_task_log.py` | 流转留痕、字段变更记录、方案上传留痕、actor 审计 |
| `tests/test_meeting.py` | 提取（决议/待办 ActionItem/责任人/分类）、to_tasks 批量生成、to_bugs、dry_run、来源追溯 |
| `tests/test_attachment.py` | 方案链接登记、plan_links 冗余同步、attachment.list |
| `tests/test_link_requirement.py` | 需求转任务继承字段、未审核需求拒绝转化 |
| `tests/test_mcp_tools.py`（扩展） | 新工具经 MCP 层注册、direct/client 双模式命名一致 |

---

## 10. 实施顺序

1. **存储层**：`orm.py` 新增 `TaskOrm`/`BugOrm`/`SprintOrm`/`TaskLogOrm`/`MeetingMinutesOrm`/`AttachmentOrm`；`base.py` 新增抽象方法；`orm_backend.py` 实现 CRUD + 过滤；`_ensure_columns` 兜底迁移。
2. **models**：新增枚举 + `Task`/`TaskCreate`/`Bug`/`BugCreate`/`Sprint`/`TaskLog`/`ActionItem`/`MeetingMinutes`/`MeetingMinutesCreate`/`Attachment`；扩展 `SourceRef.ref_type`。
3. **services**：`TaskService` + `BugService` + `MeetingMinutesService`（含 `_classify_kind`/`_extract` 启发式）。
4. **MCP tools**：`DecpTools.TOOL_BINDINGS` 登记新工具 + 方法实现 + `_TOOL_DESCS`。
5. **Agent**：三个新技能类 + `SkillRegistry` 注册 + `_INTENT_KEYWORDS` + `skills/*/SKILL.md` + manifest。
6. **CLI/报告（可选）**：`task.board`/`bug.search` 导出 Excel；demo 指令样例。
7. **测试 + 文档**：新增测试文件；更新 `CLAUDE.md` 工具清单与目录结构。

---

## 11. 关键设计决策与可调整项

| 决策 | 选择 | 理由 / 可调整 |
| --- | --- | --- |
| bug 独立数据域 | ✅ 独立 `Bug` + 多域关联（feedback/requirement/task/meeting 双向外键引用） | 完整缺陷生命周期与信息管理；关联不破坏各域独立性 |
| TaskType 扩展 | 增加 `tech_debt` / `ops` | 技术债/运营任务有落点 |
| TaskStatus 扩展 | 增加 `review` / `blocked` | 覆盖评审与阻塞态，更贴近研发真实流转 |
| 会议提取是否接 LLM | 内核纯启发式，LLM 在外层双通道 | 与现有架构一致（确定性、可测试、零额外依赖）；`meeting.submit` 支持外部 LLM 提取后结构化入参 |
| 迭代排期深度 | 轻量 sprint（不含容量/燃尽） | 先满足排期+跟踪；容量管理可后扩 |
| 开发/事务分类 | 关键词启发式 `_classify_kind` | 词典可配置（后续支持注入） |
| 方案链接管理 | `attachment` 表 + `plan_links` 冗余双写 | 看板零 join 读取 + 审计可追溯；只存链接不存文件 |
| action_items 类型 | 强类型 `ActionItem(BaseModel)` | 待办字段约束（desc/owner/due/kind/note）清晰 |
| 会议字段扩展 | 增加 `location`/`recording_url`/`agenda` | 满足完整存档诉求 |
| bug 状态机 | `new→confirmed→in_progress→fixed→verified→closed/wonfix`（6 态，去掉 triaged） | 已简化；如需再减（如去掉 verified）按团队习惯调整 |

---

## 附：新增工具清单汇总（17 个）

**task（10）**：`task.create` `task.update` `task.move` `task.board` `task.list` `task.get` `task.upload_plan` `task.link_requirement` `task.link_bug` `task.archive`/`task.restore`
**bug（8）**：`bug.create` `bug.update` `bug.transition` `bug.search` `bug.get` `bug.link`（feedback/requirement/task）`bug.from_feedback` `bug.upload_plan` `bug.archive`/`bug.restore`
**sprint（2）**：`sprint.create` `sprint.list`
**meeting（5）**：`meeting.submit` `meeting.get`/`list`/`search` `meeting.to_tasks` `meeting.to_bugs`
**attachment（2）**：`attachment.upload` `attachment.list`

---

## 12. 存储层细化设计

### 12.1 StorageBackend 抽象接口扩展（base.py）

新增 4 组抽象方法，与现有 `feedback_*/requirement_*` 命名风格对齐（全带 `workspace_id` 强制隔离）：

```python
# ---- task ----
@abstractmethod
async def task_insert(self, rec: dict[str, Any]) -> str: ...
@abstractmethod
async def task_get(self, tid: str, *, workspace_id: str = "default") -> dict[str, Any] | None: ...
@abstractmethod
async def task_list(
    self, *, status: str | None = None, type_: str | None = None,
    sprint_id: str | None = None, assignee: str | None = None,
    limit: int = 100, offset: int = 0, include_archived: bool = False,
    workspace_id: str = "default",
) -> list[dict[str, Any]]: ...
@abstractmethod
async def task_update(self, tid: str, fields: dict[str, Any], *, workspace_id: str = "default") -> dict[str, Any] | None: ...
@abstractmethod
async def task_count(self, *, status: str | None = None, include_archived: bool = False,
                     workspace_id: str = "default") -> int: ...
@abstractmethod
async def task_reorder(self, tid: str, order: int, *, workspace_id: str = "default") -> dict[str, Any] | None: ...

# ---- bug ----
@abstractmethod
async def bug_insert(self, rec: dict[str, Any]) -> str: ...
@abstractmethod
async def bug_get(self, bgid: str, *, workspace_id: str = "default") -> dict[str, Any] | None: ...
@abstractmethod
async def bug_list(
    self, *, status: str | None = None, severity: str | None = None,
    priority: str | None = None, assignee: str | None = None,
    module: str | None = None, channel: str | None = None,
    limit: int = 100, offset: int = 0, include_archived: bool = False,
    workspace_id: str = "default",
) -> list[dict[str, Any]]: ...
@abstractmethod
async def bug_update(self, bgid: str, fields: dict[str, Any], *, workspace_id: str = "default") -> dict[str, Any] | None: ...
@abstractmethod
async def bug_count(self, *, status: str | None = None, include_archived: bool = False,
                    workspace_id: str = "default") -> int: ...

# ---- sprint ----
@abstractmethod
async def sprint_insert(self, rec: dict[str, Any]) -> str: ...
@abstractmethod
async def sprint_get(self, spid: str, *, workspace_id: str = "default") -> dict[str, Any] | None: ...
@abstractmethod
async def sprint_list(self, *, status: str | None = None, workspace_id: str = "default") -> list[dict[str, Any]]: ...
@abstractmethod
async def sprint_update(self, spid: str, fields: dict[str, Any], *, workspace_id: str = "default") -> dict[str, Any] | None: ...

# ---- task_log（审计流） ----
@abstractmethod
async def log_insert(self, rec: dict[str, Any]) -> int: ...
@abstractmethod
async def log_list(self, task_id: str, *, entity: str = "task", workspace_id: str = "default") -> list[dict[str, Any]]: ...

# ---- meeting_minutes ----
@abstractmethod
async def meeting_insert(self, rec: dict[str, Any]) -> str: ...
@abstractmethod
async def meeting_get(self, mid: str, *, workspace_id: str = "default") -> dict[str, Any] | None: ...
@abstractmethod
async def meeting_list(
    self, *, module: str | None = None, participant: str | None = None,
    limit: int = 100, offset: int = 0, include_archived: bool = False,
    workspace_id: str = "default",
) -> list[dict[str, Any]]: ...
@abstractmethod
async def meeting_update(self, mid: str, fields: dict[str, Any], *, workspace_id: str = "default") -> dict[str, Any] | None: ...
@abstractmethod
async def meeting_count(self, *, include_archived: bool = False, workspace_id: str = "default") -> int: ...

# ---- attachment（方案/附件链接登记） ----
@abstractmethod
async def attachment_insert(self, rec: dict[str, Any]) -> str: ...
@abstractmethod
async def attachment_list(self, entity: str, entity_id: str, *, workspace_id: str = "default") -> list[dict[str, Any]]: ...
```

### 12.2 ORM 表模型（orm.py 新增）

```python
class TaskOrm(Base):
    __tablename__ = "task"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), default=DEFAULT_WORKSPACE_ID, index=True)
    type: Mapped[str] = mapped_column(String(16), default="requirement")
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    module: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="backlog", index=True)
    priority: Mapped[str] = mapped_column(String(4), default="P2", index=True)
    assignee: Mapped[str | None] = mapped_column(String(64), index=True)
    sprint_id: Mapped[str | None] = mapped_column(String(32), index=True)
    planned_start: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    estimate: Mapped[float | None] = mapped_column(Float)
    order: Mapped[int] = mapped_column(Integer, default=0)
    plan_links: Mapped[list] = mapped_column(JsonType, default=list)
    requirement_id: Mapped[str | None] = mapped_column(String(32))
    feedback_ids: Mapped[list] = mapped_column(JsonType, default=list)
    bug_ids: Mapped[list] = mapped_column(JsonType, default=list)
    source_refs: Mapped[list] = mapped_column(JsonType, default=list)
    labels: Mapped[list] = mapped_column(JsonType, default=list)
    extra: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at / updated_at / started_at / done_at: Mapped[DateTime]
    archived / archived_at / archived_by
    __table_args__ = (Index("idx_task_workspace_status", "workspace_id", "status"),)
```

```python
class BugOrm(Base):
    __tablename__ = "bug"
    # 标量列：id / workspace_id / title / description / module / severity / priority /
    #         status / channel / environment / reproduce_steps / expected / actual /
    #         assignee / reporter / sprint_id / due_at / fix_version
    # JSON 列：plan_links / feedback_ids / requirement_ids / task_ids / meeting_ids /
    #          source_refs / labels / extra
    # 时间戳：created_at / updated_at / fixed_at / closed_at + archived 三件套
    __table_args__ = (Index("idx_bug_workspace_status", "workspace_id", "status"),)
```

```python
class SprintOrm(Base):
    __tablename__ = "sprint"
    id / workspace_id / name / goal / start_date / end_date / status / created_at
    __table_args__ = (Index("idx_sprint_workspace", "workspace_id"),)

class TaskLogOrm(Base):
    __tablename__ = "task_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id / task_id / entity / action / from_status / to_status /
    field / old_value(JsonType) / new_value(JsonType) / actor / comment / created_at
    __table_args__ = (Index("idx_task_log_task", "entity", "task_id"),)

class MeetingMinutesOrm(Base):
    __tablename__ = "meeting_minutes"
    # 标量：id / workspace_id / title / held_at / location / recording_url / module /
    #       raw_text(Text) / summary(Text) / submitted_by / source_ref
    # JSON：participants / agenda / decisions / action_items(ActionItem 序列化) / keywords
    # 时间戳：created_at / updated_at + archived 三件套
    __table_args__ = (Index("idx_meeting_workspace", "workspace_id"),)

class AttachmentOrm(Base):
    __tablename__ = "attachment"
    id / workspace_id / entity / entity_id / url / name / mime / size / uploaded_by / created_at
    __table_args__ = (Index("idx_attachment_entity", "entity", "entity_id"),)
```

> 迁移策略：新表由 `Base.metadata.create_all` 自动建；未来迭代只在已有表加列时走 `_ensure_columns`。
> `action_items` 存 JSON：`ActionItem.model_dump()` 序列化、`ActionItem.model_validate()` 反序列化（复用 `_serialize_refs` 模式）。

### 12.3 看板 board 查询实现要点（orm_backend.py）

```python
async def task_board(self, *, status=None, sprint_id=None, assignee=None, type_=None,
                     include_bugs=True, workspace_id="default") -> dict[str, list]:
    # 1) 一次查出该 workspace 全部非归档 task（或按过滤条件）
    # 2) 按 status 分组到 {backlog: [], todo: [], in_progress: [], review: [], blocked: [], done: [], cancelled: []}
    # 3) 组内按 (order, created_at) 排序
    # 4) include_bugs=True：收集本批 task 的 bug_ids → bug_get 批量取 → 子卡片摘要挂到 task 下
    #    批量：`bug_get_many(ids, workspace_id)`（新增可选抽象，或 Service 层循环 + 限制数量）
```

> 设计取舍：看板批量取关联 bug 时，避免 N+1。实现上新增一个可选批量方法
> `bug_get_many(ids: list[str], *, workspace_id) -> list[dict]`（Service 层 fallback 到逐条循环，保证后端实现可选）。

---

## 13. Service 层细化设计

### 13.1 TaskService 完整签名与核心逻辑

```python
class TaskService:
    ALLOWED_UPDATE = {"title", "description", "module", "priority", "assignee",
                      "sprint_id", "planned_start", "due_at", "estimate", "labels", "extra"}
    VALID_ASSIGNEES = None  # 惰性：workspace 已批准成员集合

    def __init__(self, storage: StorageBackend) -> None: ...

    async def create(self, data: TaskCreate, *, workspace_id) -> Task:
        """创建任务。校验：
        - assignee（若指定）须为 workspace approved 成员
        - type=requirement 时 requirement_id 必须指向 accepted/merged 需求
        - 初始化 task_log(entity=task, action=created)
        """
        tid = gen_id("ts")
        now = utcnow()
        rec = {**data.model_dump(), "id": tid, "status": "backlog",
               "created_at": now, "updated_at": now, "archived": False}
        await self._storage.task_insert(rec)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": tid, "entity": "task",
            "action": "created", "actor": data.submitted_by, "created_at": now,
        })
        return Task.model_validate(rec)

    async def move(self, tid, status, *, actor, order=None, comment=None, workspace_id) -> Task:
        """看板拖拽。status 白名单 {backlog,todo,in_progress,review,blocked,done,cancelled}。
        - in_progress 且 started_at 为空 → 置 started_at
        - done 且 done_at 为空 → 置 done_at
        - blocked → 强制要求 comment（阻塞原因），写入 task_log
        - 记录 task_log(action=move, from_status, to_status, comment)
        """
        cur = await self._storage.task_get(tid, workspace_id=workspace_id)
        if cur is None: raise KeyError(f"任务不存在: {tid}")
        if cur.get("status") == status and order is None:
            return Task.model_validate(cur)   # 无实际变更
        fields = {"status": status, "updated_at": utcnow()}
        if status == "in_progress" and not cur.get("started_at"):
            fields["started_at"] = utcnow()
        if status == "done" and not cur.get("done_at"):
            fields["done_at"] = utcnow()
        if order is not None: fields["order"] = order
        await self._storage.task_update(tid, fields, workspace_id=workspace_id)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": tid, "entity": "task",
            "action": "move", "from_status": cur.get("status"), "to_status": status,
            "comment": comment, "actor": actor, "created_at": utcnow(),
        })
        return await self.get(tid, workspace_id=workspace_id)

    async def upload_plan(self, tid, url, *, name=None, actor, workspace_id) -> Task:
        """方案链接上传：attachment 登记 + plan_links 冗余 + task_log(plan_added)。"""
        now = utcnow()
        at_id = gen_id("at")
        await self._storage.attachment_insert({
            "id": at_id, "workspace_id": workspace_id, "entity": "task", "entity_id": tid,
            "url": url, "name": name or url, "uploaded_by": actor, "created_at": now,
        })
        cur = await self._storage.task_get(tid, workspace_id=workspace_id)
        links = list(cur.get("plan_links") or [])
        if url not in links: links.append(url)
        await self._storage.task_update(tid, {"plan_links": links, "updated_at": now},
                                        workspace_id=workspace_id)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": tid, "entity": "task",
            "action": "plan_added", "field": "plan_links",
            "old_value": None, "new_value": url, "actor": actor, "created_at": now,
        })
        return await self.get(tid, workspace_id=workspace_id)

    async def link_requirement(self, rid, *, actor, workspace_id) -> Task:
        """需求 → 开发任务。仅 accepted/merged 需求可转；继承 title/priority/module/tags/feedback_ids。"""
        req = await self._storage.requirement_get(rid, workspace_id=workspace_id)
        if req is None: raise KeyError(f"需求不存在: {rid}")
        if req.get("status") not in ("accepted", "merged"):
            raise ValueError(f"仅已审核（accepted/merged）需求可转任务，当前: {req.get('status')}")
        return await self.create(TaskCreate(
            type="requirement", title=req["title"], description=req.get("description", ""),
            module=req.get("module"), priority=req.get("priority", "P2"),
            requirement_id=rid,
            feedback_ids=[s["ref_id"] for s in req.get("feedback_ids", []) if s.get("ref_type") == "feedback"],
            labels=req.get("tags", []),
            source_refs=[{"ref_type": "requirement", "ref_id": rid,
                          "detail": f"需求 {rid} 转开发任务"}],
        ), workspace_id=workspace_id)

    async def board(self, *, status=None, sprint_id=None, assignee=None, type_=None,
                    include_bugs=True, workspace_id) -> dict:
        # 见 12.3；返回 {columns: {...}, counts: {per_column}}
    ...
```

### 13.2 BugService 完整签名与状态机

```python
class BugService:
    ALLOWED_UPDATE = {"title", "description", "module", "severity", "priority",
                      "environment", "reproduce_steps", "expected", "actual",
                      "assignee", "sprint_id", "due_at", "fix_version", "labels", "extra"}
    TRANSITIONS = {
        "new":          {"confirmed", "wonfix", "closed"},
        "confirmed":    {"in_progress", "wonfix", "closed"},
        "in_progress":  {"fixed", "wonfix", "closed"},
        "fixed":        {"verified", "in_progress", "closed"},   # 允许回归
        "verified":     {"closed", "in_progress"},               # 允许 reopen
        "closed":       {"in_progress"},                          # 允许 reopen
        "wonfix":       {"in_progress", "closed"},               # 允许 reopen
    }

    def __init__(self, storage: StorageBackend) -> None: ...

    async def create(self, data: BugCreate, *, workspace_id) -> Bug:
        """创建缺陷。channel=feedback 时校验 feedback_ids 存在；初始化 task_log(entity=bug, action=created)。"""

    async def transition(self, bgid, status, *, actor, comment=None, workspace_id) -> Bug:
        """状态机校验：from ∈ TRANSITIONS[to]（允许回退）；fixed→fixed_at；closed→closed_at；
        wonfix 强制 comment（原因）；每次留痕 task_log(entity=bug, action=move, comment)。"""

    async def link(self, bgid, *, feedback_ids=None, requirement_ids=None,
                   task_ids=None, meeting_ids=None, workspace_id) -> Bug:
        """多域关联：四域引用逐一追加去重；task_ids 变更时同步更新对应 task.bug_ids（双向）。"""

    async def from_feedback(self, fb, *, actor, workspace_id) -> Bug:
        """客户反馈 → 缺陷：title=content[:60]，severity 由 fb.structured.impact_severity 映射
        {critical:critical, high:high, medium:medium, low:low}，channel=feedback，
        feedback_ids=[fb.id]，source_refs 记录。"""

    async def search(self, *, status, severity, priority, assignee, module, channel,
                     limit, offset, include_archived, workspace_id) -> list[Bug]:
    async def get(self, bgid, *, include_relations=True, workspace_id) -> Bug | None:
        # include_relations：返回 {bug, feedbacks:[...], requirements:[...], tasks:[...], meetings:[...]}
    ...
```

### 13.3 MeetingMinutesService 完整签名与启发式提取

```python
class MeetingMinutesService:
    DEV_KEYWORDS = ("开发", "实现", "修复", "接口", "重构", "优化", "测试", "部署", "联调",
                    "排查", "代码", "SQL", "前端", "后端", "上线", "发布", "升级", "BUG", "缺陷")
    CHORE_KEYWORDS = ("跟进", "协调", "安排", "确认", "沟通", "对齐", "文档", "评审", "会议",
                      "催办", "整理", "通知", "汇报", "培训")
    TECH_DEBT_KEYWORDS = ("技术债", "重构", "架构", "优化")
    OPS_KEYWORDS = ("活动", "运营", "配置", "数据维护", "上线支持")

    def __init__(self, storage: StorageBackend) -> None: ...

    @staticmethod
    def _classify_kind(desc: str) -> str:
        """dev / chore：命中 dev 词 → dev；否则 chore。"""
        d = desc.lower()
        if any(k.lower() in d for k in DEV_KEYWORDS): return "dev"
        return "chore"

    @staticmethod
    def _classify_type(desc: str) -> str:
        """task.type 判定：tech_debt 词 → tech_debt；ops 词 → ops；dev → project；否则 chore。"""

    @classmethod
    def _extract(cls, raw_text: str) -> dict:
        """段落切分 → 段头识别（决议/待办）→ 条目解析 → ActionItem。
        返回 {"summary", "decisions": [...], "action_items": [ActionItem], "keywords": [...]}"""
        # 规则：
        # 1. 首部：匹配 时间/地点/参会人/议程/录屏 字段行
        # 2. summary：首段非字段行文本
        # 3. 决议段（决议/结论/决定/Decisions）→ decisions
        # 4. 待办段（待办/行动项/下一步/TODO/Action Items）→ 逐条 ActionItem
        #    - 责任人：条目内 `（张三）` / `张三：` / `负责人:张三`
        #    - 截止：`（周五前）` / `本周五` / `截止: 2026-08-20`
        #    - kind：_classify_kind(desc)

    async def submit(self, data: MeetingMinutesCreate, *, workspace_id) -> MeetingMinutes:
        """保存原文 raw_text + 启发式提取结构化字段。"""

    async def to_tasks(self, mid, *, actor, workspace_id, dry_run=False) -> list[Task] | list[dict]:
        """每条 action_item → Task：
        - kind=dev 且命中 tech_debt 词 → type=tech_debt
        - kind=dev → type=project（requirement_id 留空，后续可对接）
        - kind=chore 且命中 ops 词 → type=ops
        - kind=chore → type=chore
        - owner → assignee；due → due_at；desc → title；source_refs=[{ref_type:meeting, ref_id:mid}]
        - dry_run=True 返回 [{desc, type, assignee, due}] 预览，不入库"""

    async def to_bugs(self, mid, *, actor, workspace_id, dry_run=False) -> list[Bug] | list[dict]:
        """识别纪要中 '发现/存在/报错/异常' 且含缺陷语义的段落 → Bug(channel=meeting, meeting_ids=[mid])。
        dry_run 同理预览。"""
```

### 13.4 复用与常量

| 复用点 | 实现 |
| --- | --- |
| id 生成 | `gen_id(prefix)`（现有 `f"{prefix}-{uuid4().hex[:12]}"`） |
| 时间戳 | `utcnow()`（现有 `datetime.now(UTC)`） |
| 相似度去重 | `decp_core.services.similarity`（bug 重复上报、纪要待办去重） |
| 成员校验 | `WorkspaceService.assert_member`（MCP 层）+ `member_list(status="approved")`（assignee 校验） |
| 序列化 | `_serialize_refs`（SourceRef→dict）；`ActionItem.model_dump()/model_validate()` |

---

## 14. MCP 工具层细化设计

### 14.1 DecpTools 扩展结构

```python
class DecpTools:
    def __init__(self, storage, reports_dir):
        ...
        self.task = TaskService(storage)
        self.bug = BugService(storage)
        self.sprint = SprintService(storage)          # 新增轻量 SprintService（create/list）
        self.meeting = MeetingMinutesService(storage)
        self.attachment = AttachmentService(storage)  # 新增轻量 AttachmentService（upload/list）
```

### 14.2 新增工具方法实现模式（与现有完全一致）

每个工具方法遵循统一骨架：

```python
async def task_move(self, task_id: str, status: str, order: int | None = None,
                    comment: str | None = None, ctx: Context | None = None,
                    user_id: str | None = None, workspace_id: str | None = None) -> dict:
    """看板拖拽：状态流转 + 列内排序，in_progress/done 自动记时间戳。"""
    try:
        uid, wid = await self._authorize(ctx, user_id, workspace_id)
        task = await self.task.move(task_id, status, actor=uid, order=order,
                                    comment=comment, workspace_id=wid)
        return utils.tool_result({"ok": True, "workspace_id": wid, "user_id": uid,
                                  "task": task.model_dump()})
    except WorkspaceError as e:
        return utils.error_result(f"任务流转失败: {e}")
    except Exception as e:  # noqa: BLE001
        return utils.error_result(f"任务流转失败: {e}")
```

### 14.3 TOOL_BINDINGS 增量（23 → 40）

```python
TOOL_BINDINGS = {
    ...  # 现有 23 个不变
    # task
    "task.create": "task_create", "task.update": "task_update", "task.move": "task_move",
    "task.board": "task_board", "task.list": "task_list", "task.get": "task_get",
    "task.upload_plan": "task_upload_plan", "task.link_requirement": "task_link_requirement",
    "task.link_bug": "task_link_bug", "task.archive": "task_archive", "task.restore": "task_restore",
    # bug
    "bug.create": "bug_create", "bug.update": "bug_update", "bug.transition": "bug_transition",
    "bug.search": "bug_search", "bug.get": "bug_get", "bug.link": "bug_link",
    "bug.from_feedback": "bug_from_feedback", "bug.upload_plan": "bug_upload_plan",
    "bug.archive": "bug_archive", "bug.restore": "bug_restore",
    # sprint
    "sprint.create": "sprint_create", "sprint.list": "sprint_list",
    # meeting
    "meeting.submit": "meeting_submit", "meeting.get": "meeting_get",
    "meeting.list": "meeting_list", "meeting.search": "meeting_search",
    "meeting.to_tasks": "meeting_to_tasks", "meeting.to_bugs": "meeting_to_bugs",
    # attachment
    "attachment.upload": "attachment_upload", "attachment.list": "attachment_list",
}
```

> 注意：`task.archive/restore` 与 `bug.archive/restore` 计入 40，上表共 17 个新增（原 23 + 17 = 40）。

### 14.4 看板返回结构（task.board 契约）

```json
{
  "ok": true,
  "workspace_id": "ws-xxx",
  "columns": {
    "backlog":     [{"id": "ts-1", "title": "...", "type": "requirement", "priority": "P0",
                     "assignee": "alice", "due_at": "2026-08-20", "labels": ["结算"],
                     "has_plan": true, "bugs": [{"id": "bg-1", "title": "...", "status": "confirmed", "severity": "high"}]}],
    "todo":        [],
    "in_progress": [...],
    "review":      [...],
    "blocked":     [...],
    "done":        [...],
    "cancelled":   []
  },
  "counts": {"backlog": 1, "todo": 0, "in_progress": 2, "review": 1, "blocked": 1, "done": 5, "cancelled": 0}
}
```

---

## 15. Agent / Skill 层细化

### 15.1 新增 Skill 类（对齐 RequirementAnalysisSkill 结构）

```python
class TaskManagementSkill(BaseSkill):
    name = "task_management"
    description = "团队任务管理：看板/排期/待办/流转/技术债/运营。当用户要求查看或管理任务看板、排期迭代、流转任务状态、上传方案、将需求转为开发任务时使用。"
    tools_required = ["task.create", "task.move", "task.board", "task.list", "task.get",
                      "task.upload_plan", "task.link_requirement", "task.link_bug",
                      "sprint.create", "sprint.list", "bug.search", "requirement.search"]
    async def run(self, **params):
        # 动作分支：board / list / move / create / upload_plan / link_requirement / sprint ...
```

```python
class BugManagementSkill(BaseSkill):
    name = "bug_management"
    description = "缺陷管理：创建/分诊/修复/验证/关闭缺陷，关联反馈/需求/任务。当用户报告 bug、查看缺陷、流转缺陷状态、上传修复方案时使用。"
    tools_required = ["bug.create", "bug.transition", "bug.search", "bug.get",
                      "bug.link", "bug.from_feedback", "bug.upload_plan", "task.create"]
```

```python
class MeetingMinutesSkill(BaseSkill):
    name = "meeting_minutes"
    description = "会议纪要管理：提交纪要、提取决议与待办、将待办批量转为任务、识别纪要中的缺陷。当用户输入会议纪要、要求提取行动项、把会议待办列入任务计划时使用。"
    tools_required = ["meeting.submit", "meeting.get", "meeting.list",
                      "meeting.to_tasks", "meeting.to_bugs", "task.create"]
```

### 15.2 SkillRegistry.register_defaults 扩展

```python
def register_defaults(self) -> None:
    self.register(RequirementAnalysisSkill(self._backend))
    self.register(QuerySkill(self._backend))
    self.register(FeedbackCollectSkill(self._backend))
    self.register(TaskManagementSkill(self._backend))
    self.register(BugManagementSkill(self._backend))
    self.register(MeetingMinutesSkill(self._backend))
```

### 15.3 _INTENT_KEYWORDS 扩展（agent/__init__.py）

```python
_INTENT_KEYWORDS = {
    "requirement_analysis": ("需求", "反馈", "整理", "聚类", "去重", "优先级", ...),
    "query": ("查询", "状态", ...),
    "feedback_collect": ("收集", "录入", ...),
    "task_management": ("任务", "看板", "排期", "待办", "sprint", "迭代", "拖拽",
                        "技术债", "运营任务", "方案", "负责人", "截止"),
    "bug_management": ("缺陷", "bug", "报错", "复现", "修复", "分诊", "验证"),
    "meeting_minutes": ("会议纪要", "会议记录", "纪要", "行动项", "会议待办", "决议"),
}
```

### 15.4 SKILL.md + manifest（skills/ 目录）

```
skills/task-management/SKILL.md + manifest.json
skills/bug-management/SKILL.md + manifest.json
skills/meeting-minutes/SKILL.md + manifest.json
```

- `manifest.json` 的 `depends_on_tools` 列出各自依赖的新工具（供 `SkillCatalog`/`validate_all` 校验）
- `compatibility: deerflow>=0.0.1`、`risk_level: low`（对齐现有）
- SKILL.md 内嵌工具表与"多工作区隔离先确认 workspace"的既有约束段落

---

## 16. 测试细化

| 文件 | 关键用例 |
| --- | --- |
| `tests/test_task.py` | create 校验（非成员 assignee 拒绝 / 未审核需求转任务拒绝）；move 状态机（含 review/blocked，blocked 无 comment 拒绝）；order 持久化；started_at/done_at 自动记；sprint 过滤；归档/恢复；workspace 隔离 |
| `tests/test_bug.py` | 状态机全路径（含非法跳转拒绝、fixed→verified→closed、verified→in_progress reopen）；wonfix 无 comment 拒绝；from_feedback 映射；link 四域双向；重复上报相似度去重 |
| `tests/test_task_log.py` | 流转/指派/排期/方案上传留痕完整；actor 正确 |
| `tests/test_meeting.py` | `_extract` 解析样例（决议/待办/责任人/截止/分类）；to_tasks dry_run 与入库；to_bugs；来源追溯 |
| `tests/test_attachment.py` | upload_plan 双写一致性（attachment + plan_links）；重复 URL 去重；attachment.list |
| `tests/test_mcp_tools.py` | 新工具注册计数（40）；direct/client 双模式命名一致；看板返回结构契约 |
| `tests/test_workspace.py` | 非成员调用 task/bug/meeting 工具 → WorkspaceError |
