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
    AnalysisResult,
    Feedback,
    FeedbackCreate,
    Requirement,
    RequirementCreate,
    SourceRef,
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
