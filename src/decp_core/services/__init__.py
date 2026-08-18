# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""企业数据层服务（product workspace 数据域）。

Service 封装数据域的业务逻辑，供 MCP 路由层调用并封装为 tools：
- FeedbackService   反馈收集、结构化、查询
- RequirementService 需求整理、去重、聚类、影响分析、优先级建议、入库与审核
"""
from __future__ import annotations

import re
import secrets
from collections import Counter, defaultdict
from typing import Any

from decp_core.models import (
    ActionItem,
    AnalysisResult,
    Attachment,
    Bug,
    BugCreate,
    Feedback,
    FeedbackCreate,
    MeetingMinutes,
    MeetingMinutesCreate,
    Requirement,
    RequirementCreate,
    SourceRef,
    Sprint,
    SprintCreate,
    Task,
    TaskCreate,
    TaskLog,
    new_id,
    utcnow,
)
from decp_core.storage.base import StorageBackend

from decp_core.logging_setup import get_decp_logger

# 业务日志（service 层），统一走 get_decp_logger → 控制台/滚动文件/日志中心上报
_logger = get_decp_logger("service")

# 中文标点归一化（聚类/去重的轻量文本预处理）
_PUNCT_RE = re.compile(r"[，。；：、！？\s,.!?;:()（）\[\]【】\"'“”‘’\-—]+")


def _norm(text: str) -> str:
    return _PUNCT_RE.sub("", text or "").lower()


def _extract_customer_count(text: str) -> int | None:
    """从影响描述中提取显式声明的受影响客户数。

    规则：形如「影响 N 家/个/位客户」才认为是客户数；
    其他数字（如订单量 5000）一律不当作客户数。
    """
    if not text:
        return None
    m = re.search(r"(\d+)\s*(?:家|个|位|名)\s*客户", text)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# 轻量文本相似度（确定性实现，不依赖外部嵌入服务）
# 1) 词集 Jaccard + 2) 连续 4-gram 重合度，加权融合
# ---------------------------------------------------------------------------

def _char_ngrams(text: str, n: int) -> set[str]:
    s = _norm(text)
    if not s:
        return set()
    if len(s) < n:
        return {s}
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _dice(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _containment(a: str, b: str) -> float:
    """公共子串包含度：短文本被长文本覆盖的比例（容忍同义改写与插入）。"""
    sa, sb = _norm(a), _norm(b)
    if not sa or not sb:
        return 0.0
    short, long = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    best = 0
    n, m = len(short), len(long)
    dp = [0] * (m + 1)
    for i in range(1, n + 1):
        prev = 0
        for j in range(1, m + 1):
            temp = dp[j]
            if short[i - 1] == long[j - 1]:
                dp[j] = prev + 1
                best = max(best, dp[j])
            else:
                dp[j] = 0
            prev = temp
    return best / len(short)


def similarity(a: str, b: str) -> float:
    """文本相似度 ∈ [0,1]：字符 2-gram Dice(0.5) + 3-gram Dice(0.2) + 公共子串包含(0.3)。

    针对中文反馈场景：词级方法对中文分词敏感，字符 n-gram 对同义改写、
    标点差异、插入词更鲁棒，且确定性强（不依赖外部分词/嵌入）。
    """
    g2a, g2b = _char_ngrams(a, 2), _char_ngrams(b, 2)
    g3a, g3b = _char_ngrams(a, 3), _char_ngrams(b, 3)
    return 0.5 * _dice(g2a, g2b) + 0.2 * _dice(g3a, g3b) + 0.3 * _containment(a, b)


# ---------------------------------------------------------------------------
# FeedbackService
# ---------------------------------------------------------------------------

class FeedbackService:
    """反馈数据域：收集（收集：自然语言/Excel/工单）、结构化抽取、查询。"""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    @property
    def storage(self) -> StorageBackend:
        return self._storage

    async def create(self, data: FeedbackCreate, *, workspace_id: str = "default") -> Feedback:
        """收集一条反馈并做基础结构化抽取。"""
        rec = Feedback(
            id=new_id("fb"),
            content=data.content,
            channel=data.channel,
            customer=data.customer,
            module=data.module,
            feedback_type=data.feedback_type,
            impact=data.impact,
            source_ref=data.source_ref,
            submitted_by=data.submitted_by,
            workspace_id=workspace_id,
            created_at=utcnow(),
            structured=self._extract(data),
        )
        await self._storage.feedback_insert(rec.model_dump())
        _logger.info(
            "feedback.created id=%s channel=%s customer=%s module=%s type=%s severity=%s content=%.60s",
            rec.id, rec.channel, rec.customer, rec.module,
            rec.structured.get("feedback_type"), rec.structured.get("impact_severity"),
            rec.content,
        )
        return rec

    async def create_many(self, items: list[FeedbackCreate], *, workspace_id: str = "default") -> list[Feedback]:
        """批量收集（Excel/CSV 导入入口）。"""
        out = [await self.create(item, workspace_id=workspace_id) for item in items]
        _logger.info("feedback.bulk_created count=%d ids=%s", len(out), [f.id for f in out])
        return out

    @staticmethod
    def _extract(data: FeedbackCreate) -> dict[str, Any]:
        """基础结构化：从自由文本中抽取模块/类型/影响关键词。

        设计文档 Step2「反馈理解与结构化」的确定性实现；真实场景可替换为 LLM 抽取。
        """
        text = data.content
        # 影响程度：数字量级 → 影响面
        impact_severity = "medium"
        nums = re.findall(r"\d+", text)
        max_num = max((int(n) for n in nums), default=0)
        if max_num >= 1000:
            impact_severity = "high"
        elif max_num == 0:
            impact_severity = "low"
        # 问题类型：按关键词粗分类（顺序即优先级——先精确命中再宽泛）
        # 每条先判断"独立类型"，再用"通用类型"兜底
        type_kw: dict[str, tuple[tuple[str, ...], ...]] = {
            "兼容": (("版本不匹配", "兼容"),),
            "容量": (("超过", "上限", "批量导入"),),
            "功能": (("无法登录", "不能", "缺少", "需要新增", "不支持"),),
            "登录认证": (("登录", "认证"),),
            "同步": (("同步",),),
            "性能": (("性能", "超时", "慢", "无响应", "卡", "崩溃", "宕机"),),
            "安全": (("越权", "权限", "安全"),),
        }
        ftype = data.feedback_type
        if not ftype:
            for t, kw_groups in type_kw.items():
                if any(any(k in text for k in group) for group in kw_groups):
                    ftype = t
                    break
        keywords = [k for k in _norm(text).split() if len(k) >= 2][:8]
        return {
            "impact_severity": impact_severity,
            "max_numeric_magnitude": max_num,
            "keywords": keywords,
            "feedback_type": ftype or "general",
            "extractor": "heuristic",
        }

    async def get(self, fid: str, *, workspace_id: str = "default") -> Feedback | None:
        row = await self._storage.feedback_get(fid, workspace_id=workspace_id)
        return Feedback.model_validate(row) if row else None

    async def list(self, *, customer: str | None = None, module: str | None = None,
                   limit: int = 100, offset: int = 0, workspace_id: str = "default") -> list[Feedback]:
        rows = await self._storage.feedback_list(
            customer=customer, module=module, limit=limit, offset=offset,
            workspace_id=workspace_id,
        )
        return [Feedback.model_validate(r) for r in rows]

    async def count(self, *, workspace_id: str = "default") -> int:
        return await self._storage.feedback_count(workspace_id=workspace_id)


# ---------------------------------------------------------------------------
# RequirementService
# ---------------------------------------------------------------------------

class RequirementService:
    """需求数据域：整理、去重、聚类、影响分析、优先级建议、来源校验、入库、审核。"""

    DEDUP_THRESHOLD = 0.30   # 文本相似度超过即视为重复候选（同义改写典型 0.31-0.35，同类相近 0.16 以下）
    CLUSTER_THRESHOLD = 0.20 # 聚类聚合阈值（明显低于去重阈值，聚同类但非重复）

    def __init__(self, storage: StorageBackend, feedback: FeedbackService | None = None) -> None:
        self._storage = storage
        self._feedback = feedback or FeedbackService(storage)

    # ---- 去重 ----
    def find_duplicates(self, items: list[Feedback], threshold: float | None = None) -> list[list[str]]:
        """对反馈两两比较相似度，返回相似分组（去重候选）。"""
        thr = threshold or self.DEDUP_THRESHOLD
        groups: list[list[Feedback]] = []
        for item in items:
            placed = False
            for g in groups:
                if similarity(item.content, g[0].content) >= thr:
                    g.append(item)
                    placed = True
                    break
            if not placed:
                groups.append([item])
        return [[f.id for f in g] for g in groups if len(g) > 1]

    # ---- 聚类 ----
    def cluster(self, items: list[Feedback], threshold: float | None = None) -> list[dict[str, Any]]:
        """按相似度聚合反馈为聚类主题，输出标题/关键词/覆盖反馈。"""
        thr = threshold or self.CLUSTER_THRESHOLD
        clusters: list[dict[str, Any]] = []
        for item in items:
            best = None
            best_score = thr
            for cl in clusters:
                rep = item  # 用该 item 与聚类代表比较
                s = similarity(item.content, cl["representative"])
                if s > best_score:
                    best, best_score = cl, s
            if best is not None:
                best["feedback_ids"].append(item.id)
                best["representative"] = max(
                    [best["representative"], item.content], key=len
                ) if len(item.content) > len(best["representative"]) else best["representative"]
                best["score"] = max(best["score"], best_score)
            else:
                clusters.append({
                    "id": new_id("cl"),
                    "title": self._title_for(item),
                    "keywords": item.structured.get("keywords", []),
                    "feedback_ids": [item.id],
                    "representative": item.content,
                    "score": 1.0,
                })
        # 清理内部字段，产出摘要
        out = []
        for cl in clusters:
            out.append({
                "id": cl["id"],
                "title": cl["title"],
                "keywords": cl["keywords"],
                "feedback_ids": cl["feedback_ids"],
                "count": len(cl["feedback_ids"]),
                "score": round(cl["score"], 3),
            })
        return out

    @staticmethod
    def _title_for(item: Feedback) -> str:
        module = item.structured.get("feedback_type") or item.module or "需求"
        text = _norm(item.content)
        if len(text) <= 12:
            return text
        return text[:12] + "…"

    # ---- 分类 ----
    def categorize(self, items: list[Feedback]) -> dict[str, list[str]]:
        cats: dict[str, list[str]] = defaultdict(list)
        for item in items:
            cat = item.structured.get("feedback_type") or "general"
            cats[cat].append(item.id)
        return dict(cats)

    # ---- 影响分析 ----
    def impact_analysis(self, items: list[Feedback]) -> dict[str, dict[str, Any]]:
        """逐条影响分析。

        - severity：文本量级推断的严重度
        - magnitude：问题量级（如 5000 条订单），与「客户数」严格区分
        - affected_customers：显式声明的受影响客户数（feedback.impact 含数字时才统计），
          否则按客户去重计数（该条反馈涉及的唯一客户数），不把问题量级当作客户数。
        """
        customer_set = {i.customer for i in items if i.customer}
        out: dict[str, dict[str, Any]] = {}
        for item in items:
            sev = item.structured.get("impact_severity", "medium")
            mag = item.structured.get("max_numeric_magnitude", 0)
            # 显式声明：从 impact 描述提取数字作为受影响客户数
            declared = _extract_customer_count(item.impact) if item.impact else None
            if declared is not None:
                customers = declared
            else:
                # 未显式声明：该条反馈涉及的客户数（去重后 1），避免把量级误当客户数
                customers = len({item.customer} & customer_set) if item.customer else None
            out[item.id] = {
                "customer": item.customer,
                "module": item.module or item.structured.get("feedback_type"),
                "severity": sev,
                "magnitude": mag,
                "affected_customers": customers,
                "impact_text": item.impact or item.content[:60],
            }
        return out

    # ---- 优先级建议 ----
    def prioritize(self, items: list[Feedback]) -> dict[str, str]:
        """影响面 + 聚类规模 → P0/P1/P2/P3 建议。"""
        sev_rank = {"high": 3, "medium": 2, "low": 1}
        cluster_sizes = Counter()
        for cl in self.cluster(items):
            for fid in cl["feedback_ids"]:
                cluster_sizes[fid] = cl["count"]
        out: dict[str, str] = {}
        for item in items:
            sev = sev_rank.get(item.structured.get("impact_severity", "medium"), 2)
            scale = cluster_sizes.get(item.id, 1)
            score = sev * 2 + min(scale, 5)
            if score >= 9:
                out[item.id] = "P0"
            elif score >= 7:
                out[item.id] = "P1"
            elif score >= 4:
                out[item.id] = "P2"
            else:
                out[item.id] = "P3"
        return out

    # ---- 来源校验 ----
    async def verify_sources(self, items: list[Feedback]) -> list[dict[str, Any]]:
        """来源校验：每条反馈必须有 content 与 source_ref（或可定位的渠道）。"""
        results = []
        for item in items:
            ok = bool(item.content and (item.source_ref or item.channel != "api"))
            results.append({
                "feedback_id": item.id,
                "has_content": bool(item.content),
                "has_source_ref": bool(item.source_ref),
                "channel": item.channel,
                "verifiable": ok,
            })
        return results

    # ---- 整理与分析（Step4 完整管线） ----
    async def analyze(self, *, customer: str | None = None, module: str | None = None,
                      limit: int = 200, offset: int = 0, workspace_id: str = "default") -> AnalysisResult:
        """对反馈集合执行 分类 → 去重 → 聚类 → 影响分析 → 优先级建议 → 来源校验。"""
        items = await self._feedback.list(
            customer=customer, module=module, limit=limit, offset=offset,
            workspace_id=workspace_id,
        )
        result = AnalysisResult(
            categories=self.categorize(items),
            duplicate_groups=self.find_duplicates(items),
            clusters=self.cluster(items),
            priorities=self.prioritize(items),
            impact=self.impact_analysis(items),
            sources_verified=await self.verify_sources(items),
        )
        prio_counter = Counter(result.priorities.values())
        _logger.info(
            "requirement.analyzed feedbacks=%d categories=%d dup_groups=%d clusters=%d prio=%s",
            len(items), len(result.categories), len(result.duplicate_groups),
            len(result.clusters),
            dict(prio_counter),
        )
        return result

    # ---- 生成需求草稿（Step5） ----
    async def generate_draft(self, *, title: str | None = None, description: str | None = None,
                             module: str | None = None, priority: str | None = None,
                             feedback_ids: list[str] | None = None, include_all: bool = False,
                             customer: str | None = None, workspace_id: str = "default") -> Requirement:
        """基于分析结果生成需求草稿（携带来源引用、置信度、聚类信息）。"""
        items = await self._feedback.list(customer=customer, limit=500, workspace_id=workspace_id)
        if feedback_ids:
            items = [i for i in items if i.id in feedback_ids]
        if not items:
            raise ValueError("没有可用的反馈数据来生成需求草稿")

        analysis = await self.analyze(customer=customer, workspace_id=workspace_id)
        # 置信度：结构化抽取覆盖度 + 聚类规模
        covered = sum(1 for i in items if i.structured.get("feedback_type"))
        confidence = round(0.5 * (covered / len(items)) + 0.3 * min(len(items) / 5, 1) + 0.2, 3)
        # 标题
        if not title:
            top_cluster = max(analysis.clusters, key=lambda c: c["count"]) if analysis.clusters else None
            title = top_cluster["title"] if top_cluster else items[0].content[:20]
        # 优先级建议：取聚类主流
        if not priority:
            counter = Counter(analysis.priorities.get(i.id, "P2") for i in items)
            priority = counter.most_common(1)[0][0]
        # 来源引用
        refs = [
            SourceRef(ref_type="feedback", ref_id=i.id, detail=f"{i.customer} · {i.content[:30]}…")
            for i in items[:5]
        ]
        # 影响客户数：需求覆盖的反馈涉及的唯一客户数（去重；不把问题量级误当客户数）
        item_ids = {i.id for i in items}
        affected = {
            a["customer"] for a in analysis.impact.values() if a["customer"]
        }
        impact_customers = len(affected) or None
        # 相似反馈数：去重分组中与需求覆盖反馈相关的总数
        similar = sum(
            len(g) for g in analysis.duplicate_groups if set(g) & item_ids
        ) or 0
        req = RequirementCreate(
            title=title,
            description=description or self._draft_description(items, analysis),
            module=module or items[0].module or items[0].structured.get("feedback_type"),
            priority=priority,  # type: ignore[arg-type]
            status="draft",
            feedback_ids=[i.id for i in items],
            source_refs=refs,
            cluster_id=top_cluster_id(analysis),
            impact_customers=impact_customers or 0,
            similar_feedback_count=similar,
            confidence=confidence,
            tags=[items[0].structured.get("feedback_type") or "general"],
            extra={"cluster_count": len(analysis.clusters)},
        )
        created = await self.create(req, workspace_id=workspace_id)
        _logger.info(
            "requirement.draft_generated id=%s title=%.60s priority=%s confidence=%.3f "
            "similar_feedback=%d impact_customers=%s cluster=%s",
            created.id, created.title, created.priority, created.confidence,
            created.similar_feedback_count, created.impact_customers, created.cluster_id,
        )
        return created

    # ---- 入库（Step7） ----
    async def create(self, data: RequirementCreate, *, workspace_id: str = "default") -> Requirement:
        now = utcnow()
        req = Requirement(
            id=new_id("req"),
            title=data.title,
            description=data.description,
            module=data.module,
            priority=data.priority,
            status=data.status,
            feedback_ids=data.feedback_ids,
            source_refs=data.source_refs,
            cluster_id=data.cluster_id,
            impact_customers=data.impact_customers,
            similar_feedback_count=data.similar_feedback_count,
            confidence=data.confidence,
            tags=data.tags,
            extra=data.extra,
            workspace_id=workspace_id,
            version=1,
            created_at=now,
            updated_at=now,
            approved_by=None,
            approved_at=None,
        )
        await self._storage.requirement_insert(req.model_dump())
        _logger.info(
            "requirement.created id=%s title=%.60s module=%s priority=%s status=%s version=%d",
            req.id, req.title, req.module, req.priority, req.status, req.version,
        )
        return req

    # ---- 审核（Step6） ----
    async def review(self, rid: str, decision: str, reviewer: str, *, workspace_id: str = "default") -> Requirement:
        """产品经理审核：接受(accepted) / 修改(merging draft 后 accepted) / 合并(merged) / 拒绝(rejected)。"""
        cur = await self._storage.requirement_get(rid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"需求不存在: {rid}")
        decision = decision.lower()
        status_map = {
            "accept": "accepted", "approve": "accepted", "accepted": "accepted",
            "reject": "rejected", "rejected": "rejected",
            "merge": "merged", "merged": "merged",
        }
        if decision not in status_map:
            raise ValueError(f"未知审核决策: {decision}（支持 accept/reject/merge）")
        now = utcnow()
        fields: dict[str, Any] = {
            "status": status_map[decision],
            "approved_by": reviewer,
            "approved_at": now,
            "updated_at": now,
            "version": int(cur.get("version", 1)) + 1,
        }
        row = await self._storage.requirement_update(rid, fields, workspace_id=workspace_id)
        assert row is not None
        result = Requirement.model_validate(row)
        _logger.info(
            "requirement.reviewed id=%s decision=%s reviewer=%s status=%s version=%d",
            rid, decision, reviewer, result.status, result.version,
        )
        return result

    # ---- 归档（Step7 后处理） ----
    # 仅已审核完结的需求可归档；draft/reviewing 必须先完成审核（人工决策权不可让渡）
    _ARCHIVABLE_STATUS = {"accepted", "rejected", "merged"}

    async def archive(self, rid: str, archived_by: str = "maintainer", *, workspace_id: str = "default") -> Requirement:
        """归档需求：移出活跃视图但保留可查询与可恢复。

        - 仅允许已审核状态（accepted/rejected/merged）；未完结（draft/reviewing）拒绝
        - 幂等：已归档重复调用返回当前状态
        """
        cur = await self._storage.requirement_get(rid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"需求不存在: {rid}")
        if cur.get("archived"):
            return Requirement.model_validate(cur)
        if cur.get("status") not in self._ARCHIVABLE_STATUS:
            raise ValueError(
                f"需求 {rid} 状态为 {cur.get('status')}，未完成审核不可归档"
                f"（可归档状态: {'/'.join(sorted(self._ARCHIVABLE_STATUS))}）"
            )
        now = utcnow()
        fields: dict[str, Any] = {
            "archived": True,
            "archived_at": now,
            "archived_by": archived_by,
            "updated_at": now,
            "version": int(cur.get("version", 1)) + 1,
        }
        row = await self._storage.requirement_update(rid, fields, workspace_id=workspace_id)
        assert row is not None
        result = Requirement.model_validate(row)
        _logger.info(
            "requirement.archived id=%s status=%s archived_by=%s version=%d",
            rid, result.status, archived_by, result.version,
        )
        return result

    async def restore(self, rid: str, *, workspace_id: str = "default") -> Requirement:
        """恢复归档需求：清除归档标记，保留状态/版本/审核历史。"""
        cur = await self._storage.requirement_get(rid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"需求不存在: {rid}")
        if not cur.get("archived"):
            return Requirement.model_validate(cur)
        now = utcnow()
        fields: dict[str, Any] = {
            "archived": False,
            "archived_at": None,
            "archived_by": None,
            "updated_at": now,
            "version": int(cur.get("version", 1)) + 1,
        }
        row = await self._storage.requirement_update(rid, fields, workspace_id=workspace_id)
        assert row is not None
        result = Requirement.model_validate(row)
        _logger.info(
            "requirement.restored id=%s status=%s version=%d",
            rid, result.status, result.version,
        )
        return result

    # ---- 查询 ----
    async def get(self, rid: str, *, workspace_id: str = "default") -> Requirement | None:
        row = await self._storage.requirement_get(rid, workspace_id=workspace_id)
        return Requirement.model_validate(row) if row else None

    async def list(self, *, status: str | None = None, priority: str | None = None,
                   module: str | None = None, limit: int = 100, offset: int = 0,
                   include_archived: bool = False, workspace_id: str = "default") -> list[Requirement]:
        rows = await self._storage.requirement_list(
            status=status, priority=priority, module=module,
            limit=limit, offset=offset, include_archived=include_archived,
            workspace_id=workspace_id,
        )
        return [Requirement.model_validate(r) for r in rows]

    async def count(self, *, include_archived: bool = False, workspace_id: str = "default") -> int:
        return await self._storage.requirement_count(
            include_archived=include_archived, workspace_id=workspace_id,
        )

    @staticmethod
    def _draft_description(items: list[Feedback], analysis: AnalysisResult) -> str:
        cats = "、".join(analysis.categories.keys()) or "未分类"
        impact = "；".join(
            f"{a['module']}({a['severity']})" for a in list(analysis.impact.values())[:5]
        )
        return (
            f"由 {len(items)} 条客户反馈整理生成。分类：{cats}。"
            f"影响模块：{impact}。来源可追溯：{len(analysis.sources_verified)} 条校验通过。"
        )


def top_cluster_id(analysis: AnalysisResult) -> str | None:
    if not analysis.clusters:
        return None
    return max(analysis.clusters, key=lambda c: c["count"])["id"]


# ---------------------------------------------------------------------------
# WorkspaceService（工作区多租户隔离）
# ---------------------------------------------------------------------------

class WorkspaceError(ValueError):
    """工作区业务异常（权限不足/状态冲突等），MCP 层映射为业务错误。"""


class WorkspaceService:
    """工作区数据域：创建、加入、审批、成员与隔离查询。

    权限模型：
    - 任意已注册用户可 create / join
    - 仅 owner 可 approve_member（他人加入需审批）
    - 数据读写须为 workspace 成员（由上层工具按成员资格校验）
    """

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    @property
    def storage(self) -> StorageBackend:
        return self._storage

    async def _ensure_user(self, user_id: str) -> None:
        """轻量用户注册：首次出现自动建档（get_or_create）。"""
        await self._storage.user_upsert(user_id)

    @staticmethod
    def _gen_passcode() -> str:
        """生成工作区通行证：DECP-XXXX-XXXX-XXXX（密码学安全，防暴力枚举）。

        用 secrets 生成 12 字节随机数 → 24 位 hex 大写，按 4 位分组便于人工输入。
        """
        raw = secrets.token_hex(12).upper()  # 24 字符 hex
        return "DECP-" + "-".join(raw[i : i + 4] for i in range(0, 24, 4))

    async def create(self, name: str, owner_user_id: str, description: str = "") -> dict[str, Any]:
        """创建 workspace，owner 自动成为已批准成员，自动生成通行证。"""
        if not name or not name.strip():
            raise WorkspaceError("工作区名称不能为空")
        await self._ensure_user(owner_user_id)
        now = utcnow()
        wid = new_id("ws")
        await self._storage.workspace_insert({
            "id": wid,
            "name": name.strip(),
            "owner_user_id": owner_user_id,
            "description": description,
            "passcode": self._gen_passcode(),
            "created_at": now,
        })
        await self._storage.member_upsert(wid, owner_user_id, role="owner", status="approved")
        ws = await self._storage.workspace_get(wid)
        assert ws is not None
        _logger.info(
            "workspace.created id=%s name=%s owner=%s",
            wid, name.strip(), owner_user_id,
        )
        return ws

    async def join(self, workspace_id: str, user_id: str) -> dict[str, Any]:
        """申请加入 workspace（状态 pending，等待 owner 审批）。"""
        ws = await self._storage.workspace_get(workspace_id)
        if ws is None:
            raise WorkspaceError(f"工作区不存在: {workspace_id}")
        await self._ensure_user(user_id)
        # owner 直接批准；已批准成员重复申请返回当前状态
        if user_id == ws["owner_user_id"]:
            return await self._storage.member_upsert(workspace_id, user_id, role="owner", status="approved")
        cur = await self._storage.member_get(workspace_id, user_id)
        if cur and cur["status"] == "approved":
            return cur
        return await self._storage.member_upsert(workspace_id, user_id, role="member", status="pending")

    async def join_by_passcode(
        self, workspace_id: str, passcode: str, user_id: str,
    ) -> dict[str, Any]:
        """凭通行证直接加入 workspace（凭证式授权，绕过 owner 审批）。

        通行证不绑定调用者身份：任何持有者凭正确 passcode 即可加入为已批准 member，
        调用者身份由 user_id 标注（AgentScope 等无法注入用户身份的场景即 default_user）。
        校验失败抛 WorkspaceError。
        """
        ws = await self._storage.workspace_get(workspace_id)
        if ws is None:
            raise WorkspaceError(f"工作区不存在: {workspace_id}")
        stored = ws.get("passcode")
        if not stored or not secrets.compare_digest(str(stored), str(passcode).strip()):
            raise WorkspaceError("通行证错误或已失效，无法加入工作区")
        await self._ensure_user(user_id)
        # owner 持证加入仍为 owner；其余直通 approved member
        if user_id == ws["owner_user_id"]:
            return await self._storage.member_upsert(workspace_id, user_id, role="owner", status="approved")
        return await self._storage.member_upsert(workspace_id, user_id, role="member", status="approved")

    async def approve_member(self, workspace_id: str, user_id: str, approver: str) -> dict[str, Any]:
        """owner 审批通过成员加入。仅 owner 可操作，他人加入需审批。"""
        ws = await self._storage.workspace_get(workspace_id)
        if ws is None:
            raise WorkspaceError(f"工作区不存在: {workspace_id}")
        if ws["owner_user_id"] != approver:
            raise WorkspaceError(f"仅工作区 owner 可审批成员加入: {approver} 无权限")
        if user_id == approver:
            raise WorkspaceError("owner 无需审批自己")
        cur = await self._storage.member_get(workspace_id, user_id)
        if cur is None:
            raise WorkspaceError(f"用户 {user_id} 未申请加入工作区 {workspace_id}")
        return await self._storage.member_upsert(workspace_id, user_id, role="member", status="approved")

    async def reject_member(self, workspace_id: str, user_id: str, approver: str) -> dict[str, Any]:
        """owner 拒绝成员加入申请。仅 owner 可操作。"""
        ws = await self._storage.workspace_get(workspace_id)
        if ws is None:
            raise WorkspaceError(f"工作区不存在: {workspace_id}")
        if ws["owner_user_id"] != approver:
            raise WorkspaceError(f"仅工作区 owner 可拒绝成员加入: {approver} 无权限")
        cur = await self._storage.member_get(workspace_id, user_id)
        if cur is None:
            raise WorkspaceError(f"用户 {user_id} 未申请加入工作区 {workspace_id}")
        if cur["status"] != "pending":
            raise WorkspaceError(f"用户 {user_id} 当前状态为 {cur['status']}，无可拒绝的待审批申请")
        return await self._storage.member_upsert(workspace_id, user_id, role="member", status="rejected")

    async def list(self, user_id: str) -> list[dict[str, Any]]:
        """我的 workspace 列表（本人创建或已批准加入的）。passcode 仅 owner 可见。"""
        await self._ensure_user(user_id)
        workspaces = await self._storage.workspace_list_by_user(user_id)
        return [self._mask_passcode(ws, user_id) for ws in workspaces]

    async def get(self, workspace_id: str, user_id: str) -> dict[str, Any]:
        """workspace 详情。仅本人 workspace（创建者或已批准成员）可查。

        passcode 是敏感凭证：非 owner 查询时剥离，仅 owner 可见（用于传播通行证）。
        """
        ws = await self._storage.workspace_get(workspace_id)
        if ws is None:
            raise WorkspaceError(f"工作区不存在: {workspace_id}")
        member = await self._storage.member_get(workspace_id, user_id)
        if member is None or member["status"] != "approved":
            raise WorkspaceError(f"用户 {user_id} 不是工作区 {workspace_id} 的成员，无权查看")
        return self._mask_passcode(ws, user_id)

    @staticmethod
    def _mask_passcode(ws: dict[str, Any], user_id: str) -> dict[str, Any]:
        """非 owner 查询时剥离 passcode，防成员扩散通行证。"""
        out = dict(ws)
        if out.get("owner_user_id") != user_id:
            out.pop("passcode", None)
        return out

    async def members(self, workspace_id: str, user_id: str) -> list[dict[str, Any]]:
        """成员列表。仅本人 workspace 可查。"""
        ws = await self.get(workspace_id, user_id)  # 复用成员资格校验
        assert ws is not None
        return await self._storage.member_list(workspace_id)

    # ---- 成员资格校验（供数据读写工具复用） ----
    async def assert_member(self, workspace_id: str, user_id: str) -> dict[str, Any]:
        """校验 user 是否为 workspace 已批准成员；非成员抛 WorkspaceError。"""
        member = await self._storage.member_get(workspace_id, user_id)
        if member is None or member["status"] != "approved":
            raise WorkspaceError(
                f"用户 {user_id} 不是工作区 {workspace_id} 的成员（须先申请并被批准）"
            )
        return member

    async def ensure_default(self, *, default_user_id: str = "default_user",
                             default_workspace_id: str = "default") -> None:
        """幂等保障默认工作区存在且默认用户为已批准 owner。

        存量兼容：无身份调用归默认工作区+默认用户，须保证默认工作区对默认用户可访问，
        否则既有单租户数据/测试会被成员校验拦截。
        """
        ws = await self._storage.workspace_get(default_workspace_id)
        if ws is None:
            await self._storage.workspace_insert({
                "id": default_workspace_id,
                "name": "默认工作区",
                "owner_user_id": default_user_id,
                "description": "单租户存量数据与未指定身份调用的默认归属",
                "created_at": utcnow(),
            })
        await self._storage.user_upsert(default_user_id)
        await self._storage.member_upsert(
            default_workspace_id, default_user_id, role="owner", status="approved",
        )


# ===========================================================================
# 团队任务 / 缺陷 / 会议纪要 / 迭代 / 附件（v2 扩展）
# ===========================================================================

_TASK_STATUSES = ("backlog", "todo", "in_progress", "review", "blocked", "done", "cancelled")
_BUG_TRANSITIONS: dict[str, set[str]] = {
    "new":          {"confirmed", "wonfix", "closed"},
    "confirmed":    {"in_progress", "wonfix", "closed"},
    "in_progress":  {"fixed", "wonfix", "closed"},
    "fixed":        {"verified", "in_progress", "closed"},   # 允许回归
    "verified":     {"closed", "in_progress"},               # 允许 reopen
    "closed":       {"in_progress"},                          # 允许 reopen
    "wonfix":       {"in_progress", "closed"},               # 允许 reopen
}


class TaskService:
    """团队任务：看板排期与跟踪、流转审计、方案链接管理。"""

    ALLOWED_UPDATE = {
        "title", "description", "module", "priority", "assignee",
        "sprint_id", "planned_start", "due_at", "estimate", "labels", "extra",
    }

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    async def _member_approved(self, workspace_id: str, user_id: str) -> bool:
        m = await self._storage.member_get(workspace_id, user_id)
        return m is not None and m.get("status") == "approved"

    async def create(self, data: TaskCreate, *, workspace_id: str = "default") -> Task:
        """创建任务。校验：assignee 须为 workspace 已批准成员；requirement 类型须引用已审核需求。"""
        if data.assignee and not await self._member_approved(workspace_id, data.assignee):
            raise ValueError(f"责任人 {data.assignee} 不是工作区已批准成员")
        if data.type == "requirement" and data.requirement_id:
            req = await self._storage.requirement_get(data.requirement_id, workspace_id=workspace_id)
            if req is None:
                raise ValueError(f"关联需求不存在: {data.requirement_id}")
            if req.get("status") not in ("accepted", "merged"):
                raise ValueError(
                    f"仅已审核（accepted/merged）需求可转任务，当前: {req.get('status')}"
                )
        now = utcnow()
        tid = new_id("ts")
        rec = _drop_identity(data.model_dump())
        rec.update({
            "id": tid, "workspace_id": workspace_id,
            "status": "backlog", "order": 0,
            "created_at": now, "updated_at": now, "archived": False,
        })
        await self._storage.task_insert(rec)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": tid, "entity": "task",
            "action": "created", "actor": data.submitted_by, "created_at": now,
        })
        _logger.info("task.created id=%s title=%.60s type=%s assignee=%s ws=%s",
                     tid, data.title, data.type, data.assignee, workspace_id)
        return Task.model_validate(rec)

    async def get(self, tid: str, *, include_log: bool = True, workspace_id: str = "default") -> Task | None:
        row = await self._storage.task_get(tid, workspace_id=workspace_id)
        if row is None:
            return None
        return Task.model_validate(row)

    async def list(self, *, status: str | None = None, type_: str | None = None,
                   sprint_id: str | None = None, assignee: str | None = None,
                   limit: int = 100, offset: int = 0, include_archived: bool = False,
                   workspace_id: str = "default") -> list[Task]:
        rows = await self._storage.task_list(
            status=status, type_=type_, sprint_id=sprint_id, assignee=assignee,
            limit=limit, offset=offset, include_archived=include_archived,
            workspace_id=workspace_id,
        )
        return [Task.model_validate(r) for r in rows]

    async def update(self, tid: str, fields: dict[str, Any], *, actor: str,
                     workspace_id: str = "default") -> Task:
        """白名单字段更新 + 留痕（assigned/sprint_changed/due_changed/status_changed）。"""
        cur = await self._storage.task_get(tid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"任务不存在: {tid}")
        now = utcnow()
        upd: dict[str, Any] = {}
        for key, val in fields.items():
            if key not in self.ALLOWED_UPDATE:
                continue
            # 规范化时间字段：ISO 字符串 → datetime（task_update 工具直传字符串时避免崩溃）
            if key in ("planned_start", "due_at") and isinstance(val, str):
                val = _parse_dt(val)
            upd[key] = val
        if "assignee" in upd and upd["assignee"] and not await self._member_approved(workspace_id, upd["assignee"]):
            raise ValueError(f"责任人 {upd['assignee']} 不是工作区已批准成员")
        upd["updated_at"] = now
        await self._storage.task_update(tid, upd, workspace_id=workspace_id)
        # 留痕（old/new 值统一转 JSON 安全类型，避免 datetime 无法序列化）
        if "assignee" in upd and upd["assignee"] != cur.get("assignee"):
            await self._storage.log_insert({
                "workspace_id": workspace_id, "task_id": tid, "entity": "task",
                "action": "assigned", "field": "assignee",
                "old_value": cur.get("assignee"), "new_value": upd["assignee"],
                "actor": actor, "created_at": now,
            })
        if "sprint_id" in upd and upd["sprint_id"] != cur.get("sprint_id"):
            await self._storage.log_insert({
                "workspace_id": workspace_id, "task_id": tid, "entity": "task",
                "action": "sprint_changed", "field": "sprint_id",
                "old_value": cur.get("sprint_id"), "new_value": upd["sprint_id"],
                "actor": actor, "created_at": now,
            })
        if "due_at" in upd and upd["due_at"] != cur.get("due_at"):
            await self._storage.log_insert({
                "workspace_id": workspace_id, "task_id": tid, "entity": "task",
                "action": "due_changed", "field": "due_at",
                "old_value": _json_safe(cur.get("due_at")), "new_value": _json_safe(upd["due_at"]),
                "actor": actor, "created_at": now,
            })
        return await self.get(tid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def move(self, tid: str, status: str, *, actor: str, order: int | None = None,
                   comment: str | None = None, workspace_id: str = "default") -> Task:
        """看板拖拽：状态流转 + 列内排序。blocked 强制要求 comment；自动记 started_at/done_at。"""
        if status not in _TASK_STATUSES:
            raise ValueError(f"未知任务状态: {status}，可选: {_TASK_STATUSES}")
        cur = await self._storage.task_get(tid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"任务不存在: {tid}")
        if cur.get("status") == status and order is None:
            return Task.model_validate(cur)
        if status == "blocked" and not comment:
            raise ValueError("进入阻塞（blocked）须填写阻塞原因（comment）")
        now = utcnow()
        fields: dict[str, Any] = {"status": status, "updated_at": now}
        if status == "in_progress" and not cur.get("started_at"):
            fields["started_at"] = now
        if status == "done" and not cur.get("done_at"):
            fields["done_at"] = now
        # reopen 语义：从 done/cancelled 回到活跃态时清空 done_at，避免卡片残留旧完成时间
        if status in ("in_progress", "review", "blocked", "todo") and cur.get("done_at"):
            fields["done_at"] = None
        if order is not None:
            fields["order"] = order
        await self._storage.task_update(tid, fields, workspace_id=workspace_id)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": tid, "entity": "task",
            "action": "move", "from_status": cur.get("status"), "to_status": status,
            "comment": comment, "actor": actor, "created_at": now,
        })
        return await self.get(tid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def reorder(self, tid: str, order: int, *, workspace_id: str = "default") -> Task:
        row = await self._storage.task_reorder(tid, order, workspace_id=workspace_id)
        if row is None:
            raise KeyError(f"任务不存在: {tid}")
        return Task.model_validate(row)

    async def upload_plan(self, tid: str, url: str, *, name: str | None = None,
                          actor: str, workspace_id: str = "default") -> Task:
        """方案链接上传：attachment 登记 + plan_links 冗余 + task_log(plan_added)。"""
        now = utcnow()
        await self._storage.attachment_insert({
            "id": new_id("at"), "workspace_id": workspace_id, "entity": "task",
            "entity_id": tid, "url": url, "name": name or url,
            "uploaded_by": actor, "created_at": now,
        })
        cur = await self._storage.task_get(tid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"任务不存在: {tid}")
        links = list(cur.get("plan_links") or [])
        if url not in links:
            links.append(url)
        await self._storage.task_update(tid, {"plan_links": links, "updated_at": now},
                                        workspace_id=workspace_id)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": tid, "entity": "task",
            "action": "plan_added", "field": "plan_links",
            "old_value": None, "new_value": url, "actor": actor, "created_at": now,
        })
        return await self.get(tid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def link_requirement(self, rid: str, *, actor: str,
                               workspace_id: str = "default") -> Task:
        """需求 → 开发任务：仅 accepted/merged 可转；继承 title/priority/module/tags/feedback_ids。"""
        req = await self._storage.requirement_get(rid, workspace_id=workspace_id)
        if req is None:
            raise KeyError(f"需求不存在: {rid}")
        if req.get("status") not in ("accepted", "merged"):
            raise ValueError(f"仅已审核（accepted/merged）需求可转任务，当前: {req.get('status')}")
        # RequirementOrm.feedback_ids 存纯字符串 id（非 SourceRef dict），直接继承
        fb_ids = [fid for fid in (req.get("feedback_ids") or []) if isinstance(fid, str)]
        return await self.create(TaskCreate(
            type="requirement", title=req["title"], description=req.get("description", ""),
            module=req.get("module"), priority=req.get("priority", "P2"),
            requirement_id=rid, feedback_ids=fb_ids, labels=req.get("tags", []),
            source_refs=[SourceRef(ref_type="requirement", ref_id=rid,
                                   detail=f"需求 {rid} 转开发任务")],
            submitted_by=actor,
        ), workspace_id=workspace_id)

    async def link_bug(self, tid: str, bug_id: str, *, workspace_id: str = "default") -> Task:
        """任务关联缺陷（双向：task.bug_ids + bug.task_ids）。"""
        task = await self._storage.task_get(tid, workspace_id=workspace_id)
        if task is None:
            raise KeyError(f"任务不存在: {tid}")
        bug = await self._storage.bug_get(bug_id, workspace_id=workspace_id)
        if bug is None:
            raise KeyError(f"缺陷不存在: {bug_id}")
        bug_ids = list(task.get("bug_ids") or [])
        if bug_id not in bug_ids:
            bug_ids.append(bug_id)
        await self._storage.task_update(tid, {"bug_ids": bug_ids}, workspace_id=workspace_id)
        t_ids = list(bug.get("task_ids") or [])
        if tid not in t_ids:
            t_ids.append(tid)
        await self._storage.bug_update(bug_id, {"task_ids": t_ids}, workspace_id=workspace_id)
        return await self.get(tid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def board(self, *, status: str | None = None, sprint_id: str | None = None,
                    assignee: str | None = None, type_: str | None = None,
                    include_bugs: bool = True, workspace_id: str = "default") -> dict:
        """看板视图：按列分组返回，列内按 order 排序；include_bugs 内嵌关联缺陷子卡片。"""
        rows = await self._storage.task_list(
            status=status, sprint_id=sprint_id, assignee=assignee, type_=type_,
            limit=1000, offset=0, workspace_id=workspace_id,
        )
        columns: dict[str, list[dict]] = {s: [] for s in _TASK_STATUSES}
        bug_cache: dict[str, dict] = {}
        if include_bugs:
            all_bug_ids = sorted({b for r in rows for b in (r.get("bug_ids") or [])})
            if all_bug_ids:
                for b in await self._storage.bug_get_many(all_bug_ids, workspace_id=workspace_id):
                    bug_cache[b["id"]] = b
        for r in rows:
            card = {
                "id": r["id"], "title": r["title"], "type": r["type"],
                "priority": r["priority"], "assignee": r["assignee"],
                "sprint_id": r["sprint_id"], "due_at": r["due_at"],
                "labels": r.get("labels") or [], "status": r["status"],
                "has_plan": bool(r.get("plan_links")),
            }
            if include_bugs:
                card["bugs"] = [
                    {
                        "id": b["id"], "title": b["title"], "status": b["status"],
                        "severity": b["severity"],
                    }
                    for bgid in (r.get("bug_ids") or [])
                    if (b := bug_cache.get(bgid))
                ]
            columns.get(r["status"], []).append(card)
        counts = {s: len(columns[s]) for s in _TASK_STATUSES}
        return {"columns": columns, "counts": counts}

    async def log(self, tid: str, *, workspace_id: str = "default") -> list[TaskLog]:
        rows = await self._storage.log_list(tid, entity="task", workspace_id=workspace_id)
        return [TaskLog.model_validate(r) for r in rows]

    async def archive(self, tid: str, archived_by: str = "maintainer",
                      workspace_id: str = "default") -> Task:
        cur = await self._storage.task_get(tid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"任务不存在: {tid}")
        if cur.get("archived"):
            return Task.model_validate(cur)
        now = utcnow()
        await self._storage.task_update(
            tid, {"archived": True, "archived_at": now, "archived_by": archived_by},
            workspace_id=workspace_id,
        )
        return await self.get(tid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def restore(self, tid: str, workspace_id: str = "default") -> Task:
        cur = await self._storage.task_get(tid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"任务不存在: {tid}")
        if not cur.get("archived"):
            return Task.model_validate(cur)
        await self._storage.task_update(
            tid, {"archived": False, "archived_at": None, "archived_by": None},
            workspace_id=workspace_id,
        )
        return await self.get(tid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def count(self, *, status: str | None = None, workspace_id: str = "default") -> int:
        return await self._storage.task_count(status=status, workspace_id=workspace_id)


class BugService:
    """缺陷：独立全生命周期管理，多域关联（反馈/需求/任务/会议）。"""

    ALLOWED_UPDATE = {
        "title", "description", "module", "severity", "priority",
        "environment", "reproduce_steps", "expected", "actual",
        "assignee", "sprint_id", "due_at", "fix_version", "labels", "extra",
    }
    TRANSITIONS = _BUG_TRANSITIONS

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    async def _member_approved(self, workspace_id: str, user_id: str) -> bool:
        m = await self._storage.member_get(workspace_id, user_id)
        return m is not None and m.get("status") == "approved"

    async def create(self, data: BugCreate, *, workspace_id: str = "default") -> Bug:
        if data.assignee and not await self._member_approved(workspace_id, data.assignee):
            raise ValueError(f"处理人 {data.assignee} 不是工作区已批准成员")
        now = utcnow()
        bgid = new_id("bg")
        rec = _drop_identity(data.model_dump())
        rec.update({
            "id": bgid, "workspace_id": workspace_id,
            "status": "new", "created_at": now, "updated_at": now, "archived": False,
        })
        await self._storage.bug_insert(rec)
        # 反向同步：创建时带 task_ids → 写回 task.bug_ids（保持双向引用一致）
        for tid in data.task_ids or []:
            task = await self._storage.task_get(tid, workspace_id=workspace_id)
            if task is None:
                continue
            bug_ids = list(task.get("bug_ids") or [])
            if bgid not in bug_ids:
                await self._storage.task_update(tid, {"bug_ids": bug_ids + [bgid]},
                                                workspace_id=workspace_id)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": bgid, "entity": "bug",
            "action": "created", "actor": data.submitted_by, "created_at": now,
        })
        _logger.info("bug.created id=%s title=%.60s severity=%s channel=%s ws=%s",
                     bgid, data.title, data.severity, data.channel, workspace_id)
        return Bug.model_validate(rec)

    async def get(self, bgid: str, *, include_relations: bool = True,
                  workspace_id: str = "default") -> Bug | None:
        row = await self._storage.bug_get(bgid, workspace_id=workspace_id)
        if row is None:
            return None
        return Bug.model_validate(row)

    async def search(self, *, status: str | None = None, severity: str | None = None,
                     priority: str | None = None, assignee: str | None = None,
                     module: str | None = None, channel: str | None = None,
                     limit: int = 100, offset: int = 0, include_archived: bool = False,
                     workspace_id: str = "default") -> list[Bug]:
        rows = await self._storage.bug_list(
            status=status, severity=severity, priority=priority, assignee=assignee,
            module=module, channel=channel, limit=limit, offset=offset,
            include_archived=include_archived, workspace_id=workspace_id,
        )
        return [Bug.model_validate(r) for r in rows]

    async def update(self, bgid: str, fields: dict[str, Any], *, actor: str,
                     workspace_id: str = "default") -> Bug:
        cur = await self._storage.bug_get(bgid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"缺陷不存在: {bgid}")
        upd = {k: v for k, v in fields.items() if k in self.ALLOWED_UPDATE}
        # 规范化时间字段：ISO 字符串 → datetime
        if "due_at" in upd and isinstance(upd["due_at"], str):
            upd["due_at"] = _parse_dt(upd["due_at"])
        if "assignee" in upd and upd["assignee"] and not await self._member_approved(workspace_id, upd["assignee"]):
            raise ValueError(f"处理人 {upd['assignee']} 不是工作区已批准成员")
        upd["updated_at"] = utcnow()
        await self._storage.bug_update(bgid, upd, workspace_id=workspace_id)
        return await self.get(bgid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def transition(self, bgid: str, status: str, *, actor: str,
                         comment: str | None = None, workspace_id: str = "default") -> Bug:
        """状态机流转：校验非法跳转；fixed→fixed_at；closed→closed_at；wonfix 强制原因。"""
        cur = await self._storage.bug_get(bgid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"缺陷不存在: {bgid}")
        if status not in self.TRANSITIONS:
            raise ValueError(f"未知缺陷状态: {status}")
        if status not in self.TRANSITIONS.get(cur.get("status"), set()):
            raise ValueError(
                f"非法状态流转: {cur.get('status')} → {status}，允许: {sorted(self.TRANSITIONS.get(cur.get('status'), set()))}"
            )
        if status == "wonfix" and not comment:
            raise ValueError("标记为不修复（wonfix）须填写原因（comment）")
        now = utcnow()
        fields: dict[str, Any] = {"status": status, "updated_at": now}
        if status == "fixed":
            fields["fixed_at"] = now
        if status == "closed":
            fields["closed_at"] = now
        await self._storage.bug_update(bgid, fields, workspace_id=workspace_id)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": bgid, "entity": "bug",
            "action": "move", "from_status": cur.get("status"), "to_status": status,
            "comment": comment, "actor": actor, "created_at": now,
        })
        return await self.get(bgid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def link(self, bgid: str, *, feedback_ids: list[str] | None = None,
                   requirement_ids: list[str] | None = None,
                   task_ids: list[str] | None = None,
                   meeting_ids: list[str] | None = None,
                   workspace_id: str = "default") -> Bug:
        """多域关联：四域引用逐一追加去重；task_ids 变更同步反向写 task.bug_ids。"""
        cur = await self._storage.bug_get(bgid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"缺陷不存在: {bgid}")
        upd: dict[str, Any] = {}
        if feedback_ids is not None:
            upd["feedback_ids"] = _merge_ids(cur.get("feedback_ids") or [], feedback_ids)
        if requirement_ids is not None:
            upd["requirement_ids"] = _merge_ids(cur.get("requirement_ids") or [], requirement_ids)
        if meeting_ids is not None:
            upd["meeting_ids"] = _merge_ids(cur.get("meeting_ids") or [], meeting_ids)
        if task_ids is not None:
            new_task_ids = _merge_ids(cur.get("task_ids") or [], task_ids)
            upd["task_ids"] = new_task_ids
        upd["updated_at"] = utcnow()
        await self._storage.bug_update(bgid, upd, workspace_id=workspace_id)
        # 反向：task.bug_ids 同步
        for tid in task_ids or []:
            task = await self._storage.task_get(tid, workspace_id=workspace_id)
            if task is None:
                continue
            bug_ids = list(task.get("bug_ids") or [])
            if bgid not in bug_ids:
                await self._storage.task_update(tid, {"bug_ids": bug_ids + [bgid]},
                                                workspace_id=workspace_id)
        return await self.get(bgid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def from_feedback(self, fb: dict[str, Any], *, actor: str,
                            workspace_id: str = "default") -> Bug:
        """客户反馈 → 缺陷：title=content 截断，severity 由 structured.impact_severity 映射。"""
        structured = fb.get("structured") or {}
        sev_map = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
        sev = sev_map.get(str(structured.get("impact_severity", "")).lower(), "medium")
        return await self.create(BugCreate(
            title=(fb.get("content") or "")[:60],
            description=fb.get("content") or "",
            module=fb.get("module"),
            severity=sev,  # type: ignore[arg-type]
            channel="feedback",
            feedback_ids=[fb["id"]],
            source_refs=[SourceRef(ref_type="feedback", ref_id=fb["id"],
                                   detail=f"客户反馈 {fb['id']} 转缺陷")],
            submitted_by=actor,
        ), workspace_id=workspace_id)

    async def upload_plan(self, bgid: str, url: str, *, name: str | None = None,
                          actor: str, workspace_id: str = "default") -> Bug:
        now = utcnow()
        await self._storage.attachment_insert({
            "id": new_id("at"), "workspace_id": workspace_id, "entity": "bug",
            "entity_id": bgid, "url": url, "name": name or url,
            "uploaded_by": actor, "created_at": now,
        })
        cur = await self._storage.bug_get(bgid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"缺陷不存在: {bgid}")
        links = list(cur.get("plan_links") or [])
        if url not in links:
            links.append(url)
        await self._storage.bug_update(bgid, {"plan_links": links, "updated_at": now},
                                       workspace_id=workspace_id)
        await self._storage.log_insert({
            "workspace_id": workspace_id, "task_id": bgid, "entity": "bug",
            "action": "plan_added", "field": "plan_links",
            "old_value": None, "new_value": url, "actor": actor, "created_at": now,
        })
        return await self.get(bgid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def count(self, *, status: str | None = None, workspace_id: str = "default") -> int:
        return await self._storage.bug_count(status=status, workspace_id=workspace_id)

    async def archive(self, bgid: str, archived_by: str = "maintainer",
                      workspace_id: str = "default") -> Bug:
        cur = await self._storage.bug_get(bgid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"缺陷不存在: {bgid}")
        if cur.get("archived"):
            return Bug.model_validate(cur)
        now = utcnow()
        await self._storage.bug_update(
            bgid, {"archived": True, "archived_at": now, "archived_by": archived_by},
            workspace_id=workspace_id,
        )
        return await self.get(bgid, workspace_id=workspace_id)  # type: ignore[return-value]

    async def restore(self, bgid: str, workspace_id: str = "default") -> Bug:
        cur = await self._storage.bug_get(bgid, workspace_id=workspace_id)
        if cur is None:
            raise KeyError(f"缺陷不存在: {bgid}")
        if not cur.get("archived"):
            return Bug.model_validate(cur)
        await self._storage.bug_update(
            bgid, {"archived": False, "archived_at": None, "archived_by": None},
            workspace_id=workspace_id,
        )
        return await self.get(bgid, workspace_id=workspace_id)  # type: ignore[return-value]


class SprintService:
    """迭代排期：创建与查询。"""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    async def create(self, data: SprintCreate, *, workspace_id: str = "default") -> Sprint:
        if data.end_date <= data.start_date:
            raise ValueError("迭代结束时间须晚于开始时间")
        now = utcnow()
        rec = _drop_identity(data.model_dump())
        rec.update({"id": new_id("sp"), "workspace_id": workspace_id, "created_at": now})
        await self._storage.sprint_insert(rec)
        _logger.info("sprint.created id=%s name=%s status=%s ws=%s",
                     rec["id"], data.name, data.status, workspace_id)
        return Sprint.model_validate(rec)

    async def list(self, *, status: str | None = None, workspace_id: str = "default") -> list[Sprint]:
        rows = await self._storage.sprint_list(status=status, workspace_id=workspace_id)
        return [Sprint.model_validate(r) for r in rows]


class MeetingMinutesService:
    """会议纪要：启发式提取（摘要/决议/待办/关键词）+ 存档 + 待办任务化。"""

    DEV_KEYWORDS = ("开发", "实现", "修复", "接口", "重构", "优化", "测试", "部署", "联调",
                    "排查", "代码", "SQL", "前端", "后端", "上线", "发布", "升级", "bug", "缺陷")
    CHORE_KEYWORDS = ("跟进", "协调", "安排", "确认", "沟通", "对齐", "文档", "评审", "会议",
                      "催办", "整理", "通知", "汇报", "培训")
    TECH_DEBT_KEYWORDS = ("技术债", "重构", "架构")
    OPS_KEYWORDS = ("活动", "运营", "配置", "数据维护")

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    @classmethod
    def _classify_kind(cls, desc: str) -> str:
        """dev / chore：强事务词（跟进/协调/安排等）优先 → chore；否则命中开发词 → dev。

        歧义处理：`跟进部署安排` 含 dev 词「部署」但整体是协调动作 → chore。
        词典权重：chore 词优先，dev 词次之。
        """
        d = (desc or "").lower()
        if any(k.lower() in d for k in cls.CHORE_KEYWORDS):
            return "chore"
        if any(k.lower() in d for k in cls.DEV_KEYWORDS):
            return "dev"
        return "chore"

    @classmethod
    def _classify_type(cls, desc: str) -> str:
        """task.type 判定：技术债词 → tech_debt；运营词 → ops；dev → project；否则 chore。"""
        d = (desc or "").lower()
        if any(k.lower() in d for k in cls.TECH_DEBT_KEYWORDS):
            return "tech_debt"
        if any(k.lower() in d for k in cls.OPS_KEYWORDS):
            return "ops"
        if any(k.lower() in d for k in cls.DEV_KEYWORDS):
            return "project"
        return "chore"

    @classmethod
    def _extract(cls, raw_text: str) -> dict:
        """启发式提取：段头识别 → 摘要/决议/待办/关键词。"""
        from datetime import date as _date

        lines = [ln.strip() for ln in (raw_text or "").splitlines() if ln.strip()]
        summary_parts: list[str] = []
        decisions: list[dict] = []
        action_items: list[ActionItem] = []
        keywords: list[str] = []
        section: str | None = None
        for ln in lines:
            # 段头识别：匹配到段头时，若同一行带内容（含冒号/内容非空），把内容归入该段
            if any(ln.startswith(h) for h in ("决议", "结论", "决定", "Decisions", "decisions")):
                section = "decisions"
                _tail = ln.split("：", 1)[-1].split(":", 1)[-1].strip()
                if _tail:
                    decisions.append({"item": _tail, "owner": None})
                continue
            if any(ln.startswith(h) for h in ("待办", "行动项", "下一步", "TODO", "Action Items", "action_items")):
                section = "action"
                _tail = ln.split("：", 1)[-1].split(":", 1)[-1].strip()
                if _tail and not _tail.startswith(("1.", "2.", "3.", "-", "•")):
                    summary_parts.append(_tail)  # 段头行带的内联内容，宽松归入 summary
                continue
            if any(ln.startswith(h) for h in ("会议内容", "进展", "Summary", "摘要")):
                section = "summary"
                _tail = ln.split("：", 1)[-1].split(":", 1)[-1].strip()
                if _tail:
                    summary_parts.append(_tail)
                continue
            if section is None:
                # 跳过字段行
                if any(ln.startswith(h) for h in ("时间", "地点", "参会", "议程", "录屏", "主持人", "会议主题", "标题")):
                    continue
                if ln.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "-", "•")):
                    continue
                summary_parts.append(ln)
            elif section == "summary":
                summary_parts.append(ln)
            elif section == "decisions":
                item = ln.lstrip("0123456789.-•。 ")
                if item:
                    decisions.append({"item": item, "owner": None})
            elif section == "action":
                item = ln.lstrip("0123456789.-•。 ")
                if not item:
                    continue
                owner = _parse_owner(item)
                due = _parse_due(item)
                kind = cls._classify_kind(item)
                action_items.append(ActionItem(
                    desc=item, owner=owner, due=due, kind=kind,  # type: ignore[arg-type]
                ))
        # 关键词：提取较长中文词片段（简单实现：分词辅助——取含业务词的短语）
        for ln in lines[:20]:
            for seg in re.findall(r"[一-鿿]{2,10}", ln):
                if seg not in keywords and len(keywords) < 10:
                    keywords.append(seg)
        return {
            "summary": "；".join(summary_parts)[:2000],
            "decisions": decisions,
            "action_items": action_items,
            "keywords": keywords,
        }

    async def submit(self, data: MeetingMinutesCreate, *, workspace_id: str = "default") -> MeetingMinutes:
        extracted = self._extract(data.raw_text)
        now = utcnow()
        mid = new_id("mt")
        # 显式传入的 action_items 优先；否则用启发式提取
        action_items = data.action_items if data.action_items else extracted["action_items"]
        rec = _drop_identity(data.model_dump())
        # MeetingMinutesOrm 有 submitted_by 列，须保留提交者身份（勿随 _drop_identity 丢失）
        rec["submitted_by"] = data.submitted_by or "maintainer"
        rec.update({
            "id": mid, "workspace_id": workspace_id,
            "held_at": data.held_at or now,
            "summary": data.summary or extracted["summary"],
            "decisions": data.decisions or extracted["decisions"],
            "action_items": [_action_item_to_json(a) for a in action_items],
            "keywords": data.keywords or extracted["keywords"],
            "created_at": now, "updated_at": now, "archived": False,
        })
        await self._storage.meeting_insert(rec)
        _logger.info("meeting.submitted id=%s title=%.60s action_items=%d ws=%s",
                     mid, data.title, len(action_items), workspace_id)
        return MeetingMinutes.model_validate(rec)

    async def get(self, mid: str, *, workspace_id: str = "default") -> MeetingMinutes | None:
        row = await self._storage.meeting_get(mid, workspace_id=workspace_id)
        if row is None:
            return None
        return MeetingMinutes.model_validate(row)

    async def list(self, *, module: str | None = None, participant: str | None = None,
                   limit: int = 100, offset: int = 0, include_archived: bool = False,
                   workspace_id: str = "default") -> list[MeetingMinutes]:
        rows = await self._storage.meeting_list(
            module=module, participant=participant, limit=limit, offset=offset,
            include_archived=include_archived, workspace_id=workspace_id,
        )
        return [MeetingMinutes.model_validate(r) for r in rows]

    async def to_tasks(self, mid: str, *, actor: str, workspace_id: str = "default",
                       dry_run: bool = False) -> list[dict]:
        """纪要待办 → 批量任务（dry_run 预览/入库，幂等：已生成过则返回既有清单）。"""
        m = await self._storage.meeting_get(mid, workspace_id=workspace_id)
        if m is None:
            raise KeyError(f"会议纪要不存在: {mid}")
        # 幂等守卫：该会议已生成过任务则不再重复创建（防 LLM 重试/重复确认产生重复 backlog）
        existing = await self._tasks_from_meeting(mid, workspace_id=workspace_id)
        if existing:
            return existing
        task_svc = TaskService(self._storage)
        items = m.get("action_items") or []
        results: list[dict] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("desc"):
                continue
            desc = item["desc"]
            ttype = self._classify_type(desc)
            owner = item.get("owner")
            # 宽容处理：非成员 owner 不指派（写入 note），避免单条无效阻塞整批
            assignee = None
            note: str | None = None
            if owner:
                mbr = await self._storage.member_get(workspace_id, owner)
                if mbr is not None and mbr.get("status") == "approved":
                    assignee = owner
                else:
                    note = f"待办责任人：{owner}（非工作区已批准成员，未指派）"
            plan = {
                "type": ttype, "title": desc[:120],
                "assignee": assignee,
                "due_at": _due_to_datetime(item.get("due")),
                "source_refs": [SourceRef(ref_type="meeting", ref_id=mid,
                                          detail=f"会议 {m.get('title')} 待办")],
                "note": note,
            }
            if dry_run:
                results.append({"desc": desc, "type": ttype,
                                "assignee": plan["assignee"], "due_at": plan["due_at"]})
            else:
                t = await task_svc.create(TaskCreate(
                    type=plan["type"], title=plan["title"], assignee=plan["assignee"],
                    due_at=plan["due_at"], source_refs=plan["source_refs"],
                    extra={"meeting_note": note} if note else {},
                    submitted_by=actor,
                ), workspace_id=workspace_id)
                results.append({"task_id": t.id, "title": t.title, "type": t.type,
                                "status": t.status})
        return results

    async def _tasks_from_meeting(self, mid: str, *, workspace_id: str) -> list[dict]:
        """查询某会议已生成的任务（按 source_refs 反查），供幂等守卫使用。"""
        rows = await self._storage.task_list(limit=1000, offset=0, workspace_id=workspace_id)
        out: list[dict] = []
        for r in rows:
            refs = r.get("source_refs") or []
            if any(isinstance(s, dict) and s.get("ref_type") == "meeting" and s.get("ref_id") == mid
                   for s in refs):
                out.append({"task_id": r["id"], "title": r["title"], "type": r["type"],
                            "status": r["status"]})
        return out

    async def _bugs_from_meeting(self, mid: str, *, workspace_id: str) -> list[dict]:
        """查询某会议已生成的缺陷（按 meeting_ids 反查），供幂等守卫使用。"""
        rows = await self._storage.bug_list(limit=1000, offset=0, workspace_id=workspace_id)
        out: list[dict] = []
        for r in rows:
            if mid in (r.get("meeting_ids") or []):
                out.append({"bug_id": r["id"], "title": r["title"], "status": r["status"]})
        return out

    async def to_bugs(self, mid: str, *, actor: str, workspace_id: str = "default",
                      dry_run: bool = False) -> list[dict]:
        """纪要中缺陷语义段落 → Bug（channel=meeting，幂等）。"""
        m = await self._storage.meeting_get(mid, workspace_id=workspace_id)
        if m is None:
            raise KeyError(f"会议纪要不存在: {mid}")
        # 幂等守卫：该会议已生成过缺陷则不再重复创建
        existing = await self._bugs_from_meeting(mid, workspace_id=workspace_id)
        if existing:
            return existing
        bug_svc = BugService(self._storage)
        lines = [ln.strip() for ln in (m.get("raw_text") or "").splitlines() if ln.strip()]
        results: list[dict] = []
        for ln in lines:
            if any(k in ln for k in ("发现", "存在", "报错", "异常", "bug", "BUG", "缺陷")):
                title = ln[:60]
                if dry_run:
                    results.append({"title": title, "channel": "meeting"})
                else:
                    b = await bug_svc.create(BugCreate(
                        title=title, description=ln, channel="meeting",
                        meeting_ids=[mid], submitted_by=actor,
                    ), workspace_id=workspace_id)
                    results.append({"bug_id": b.id, "title": b.title, "status": b.status})
        return results


class AttachmentService:
    """方案/附件链接登记（task/bug/meeting 复用）。"""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    async def upload(self, entity: str, entity_id: str, url: str, *,
                     name: str | None = None, mime: str | None = None,
                     size: int = 0, actor: str = "maintainer",
                     workspace_id: str = "default") -> Attachment:
        if entity not in ("task", "bug", "meeting", "requirement"):
            raise ValueError(f"不支持的实体类型: {entity}")
        rec = {
            "id": new_id("at"), "workspace_id": workspace_id, "entity": entity,
            "entity_id": entity_id, "url": url, "name": name or url,
            "mime": mime, "size": size, "uploaded_by": actor, "created_at": utcnow(),
        }
        await self._storage.attachment_insert(rec)
        return Attachment.model_validate(rec)

    async def list(self, entity: str, entity_id: str, *, workspace_id: str = "default") -> list[Attachment]:
        rows = await self._storage.attachment_list(entity, entity_id, workspace_id=workspace_id)
        return [Attachment.model_validate(r) for r in rows]


def _parse_dt(value: Any) -> Any:
    """ISO 时间字符串 → datetime（容错解析）；非字符串原样返回。"""
    if not isinstance(value, str) or not value:
        return value
    from datetime import datetime
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value


_NON_STORED_FIELDS = {"submitted_by"}


def _drop_identity(rec: dict[str, Any]) -> dict[str, Any]:
    """剔除身份/非存储字段（submitted_by 等），避免传给 ORM 触发 invalid keyword。

    身份在 task_log/created 审计中落账，不入实体行。
    """
    return {k: v for k, v in rec.items() if k not in _NON_STORED_FIELDS}


def _action_item_to_json(a: ActionItem) -> dict[str, Any]:
    """ActionItem → 可 JSON 序列化 dict：date 转 ISO 字符串。"""
    d = a.model_dump()
    if d.get("due") is not None:
        d["due"] = d["due"].isoformat() if hasattr(d["due"], "isoformat") else d["due"]
    return d


def _json_safe(v: Any) -> Any:
    """任意值 → JSON 可序列化：date/datetime 转 ISO 字符串；dict/list 递归。"""
    from datetime import date, datetime
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


def _merge_ids(base: list[str], extra: list[str]) -> list[str]:
    """追加去重合并 id 列表。"""
    seen = list(base or [])
    for x in extra or []:
        if x not in seen:
            seen.append(x)
    return seen


_OWNER_PREFIX_STOPWORDS = {
    "技术债", "需求", "任务", "接口", "优化", "重构", "修复", "开发", "实现", "上线",
    "跟进", "安排", "确认", "协调", "沟通", "整理", "评审", "文档", "测试", "部署",
    "联调", "排查", "完成", "负责", "项目", "运营", "活动",
}


def _looks_like_name(candidate: str) -> bool:
    """候选 owner 是否像人名：2-6 个汉字 或 3-16 个字母数字，不含时间/动作词。"""
    if not candidate:
        return False
    # 时间特征排除：含「前/截止」或为单字时间词（周三/周五前等）不算人名
    if "前" in candidate or "截止" in candidate:
        return False
    if re.fullmatch(r"[一-鿿]{1,4}", candidate):
        # 排除单字时间词（周五前/三 等）
        if candidate in ("一", "二", "三", "四", "五", "六", "日", "天"):
            return False
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,15}", candidate):
        return True
    return False


def _parse_owner(text: str) -> str | None:
    """条目内提取责任人：`（张三）` / `张三：` / `负责人:张三` / `@张三`。

    - 括号内容若是时间（含 前/日/截止）或过长，则不是 owner，跳过
    - `：` 前若非人名形态（复合动作前缀如「跟进部署安排」）则不当作 owner
    """
    # 1) `负责人:张三` / `负责人：张三` 显式标签优先
    m = re.search(r"负责人[:：]\s*([一-鿿A-Za-z0-9_]{1,12})", text)
    if m:
        return m.group(1)
    # 2) @张三
    m = re.search(r"@([一-鿿A-Za-z0-9_]{1,16})", text)
    if m:
        return m.group(1)
    # 3) 括号内：`（张三，周五前）` → 张三；`（周五前）` → 非人名跳过
    m = re.search(r"[（(]([^）)]{1,12})[）)]", text)
    if m:
        content = m.group(1)
        # 先尝试整体（可能是纯人名）
        if _looks_like_name(content):
            return content
        # 再按分隔符拆：第一段若是人名（`张三，周五前`）→ 张三
        parts = re.split(r"[，,、]", content)
        if parts and _looks_like_name(parts[0].strip()):
            return parts[0].strip()
    # 4) `张三：完成接口` —— 冒号前缀须像人名
    m = re.search(r"([一-鿿A-Za-z0-9_]{1,12})[:：]", text)
    if m:
        candidate = m.group(1)
        if _looks_like_name(candidate) and candidate not in _OWNER_PREFIX_STOPWORDS:
            return candidate
    return None


def _parse_due(text: str) -> Any:
    """条目内提取截止：`2026-08-20` / `明天` / `本周五` / `周五前` / `下周三`。"""
    from datetime import date as _date
    from datetime import timedelta
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", text)
    if m:
        return _date.fromisoformat(m.group(1))
    if "明天" in text:
        return _date.today() + timedelta(days=1)
    # 下周三 / 本周五 / 周五前 / 括号内周五前 → 下一个指定的星期几
    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    # 显式前缀：下X / 下周X / 本周X / 这X / 这周X
    m = re.search(r"(?:下|本周|这周|这)(?:星期|周)?([一二三四五六日天])", text)
    if not m:
        # 裸周几 + 前：`周五前` / `周五`（要求带「前」或位于括号结尾，避免误匹配人名中的「三」）
        m = re.search(r"([一二三四五六日天])前", text)
        if not m:
            m = re.search(r"[（(]([一二三四五六日天])[）)]", text)
    if m:
        wd = weekday_map.get(m.group(1))
        if wd is not None:
            today = _date.today()
            days_ahead = (wd - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead)
    return None


def _due_to_datetime(d: Any) -> Any:
    """date / ISO 字符串 → datetime（转任务 due_at）。"""
    if d is None:
        return None
    from datetime import datetime as _dt
    from datetime import date as _date
    if isinstance(d, _dt):
        return d
    if isinstance(d, _date):
        return _dt.combine(d, _dt.min.time())
    if isinstance(d, str):
        try:
            return _dt.fromisoformat(d)
        except ValueError:
            return None
    return None
