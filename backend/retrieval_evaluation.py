"""Fixed, human-labelled development set for free lexical retrieval comparisons."""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.curriculum import LESSONS
from backend.retrieval import SearchStrategy, retrieve_scored

Track = Literal["agent", "fullstack"]
DATASET_VERSION = "course-retrieval-v1"
NOTICE = (
    "免费词法检索实验：标题策略每个命中词计 1 分；加权策略每个词只取标题 4、目标 2、"
    "正文 1 的最高分，同分按课程顺序排列。基于人工标注的小型开发集，"
    "正例 precision@k 的分母固定为 k；无证据样本单独统计无结果准确率。"
    "不调用模型，不执行用户代码，不包含向量检索或语义重排，也不衡量生成回答质量；"
    "请使用独立验收集验证泛化效果。"
)


class RetrievalConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: SearchStrategy
    top_k: int = Field(strict=True, ge=1, le=5)


class RetrievalEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track: Track
    baseline: RetrievalConfiguration
    candidate: RetrievalConfiguration


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    query: str
    relevant_ids: tuple[str, ...]


# Labels describe lessons that directly teach the question's subject. They are
# editorial judgments, not generated from whichever documents a strategy finds.
# Change DATASET_VERSION whenever a query, label, or scoring definition changes.
DATASETS: dict[Track, tuple[EvaluationCase, ...]] = {
    "agent": (
        EvaluationCase(
            "agent-loop-budget",
            "怎样限制 Agent 循环步数和运行成本？",
            ("agent-agent-loop", "agent-budgets"),
        ),
        EvaluationCase(
            "agent-tool-authorization",
            "工具调用如何校验参数、控制权限并避免重复写入？",
            ("agent-tool-contract", "agent-tool-safety"),
        ),
        EvaluationCase(
            "agent-mcp-discovery",
            "MCP 客户端怎样发现服务端工具并验证授权？",
            ("agent-mcp",),
        ),
        EvaluationCase(
            "agent-document-evidence",
            "文档切分时怎样保留来源，并在 RAG 回答中引用证据？",
            ("agent-chunking", "agent-rag"),
        ),
        EvaluationCase(
            "agent-retrieval-metrics",
            "如何建立固定问题集并比较检索召回与重排效果？",
            ("agent-reranking", "agent-datasets"),
        ),
        EvaluationCase(
            "agent-memory-retention",
            "长期记忆保存哪些事实，怎样处理过期记录与隐私？",
            ("agent-memory", "agent-privacy"),
        ),
        EvaluationCase(
            "agent-checkpoint-recovery",
            "工作流失败后如何从检查点恢复，并防止重复副作用？",
            ("agent-state-machine", "agent-tool-safety"),
        ),
        EvaluationCase(
            "agent-trace-failures",
            "怎样用 Trace 和 run_id 诊断模型、工具与系统失败？",
            ("agent-tracing",),
        ),
        EvaluationCase(
            "agent-untrusted-code",
            "运行不可信代码时怎样限制文件、网络和超时？",
            ("agent-sandbox",),
        ),
        EvaluationCase(
            "agent-stream-cancel",
            "SSE 如何解析拆包、报告错误并在取消时关闭上游请求？",
            ("agent-streaming",),
        ),
        EvaluationCase("agent-no-evidence-baking", "烤箱烘焙可颂的温度是多少？", ()),
        EvaluationCase("agent-no-evidence-gardening", "如何给番茄种植设置浇水时间？", ()),
    ),
    "fullstack": (
        EvaluationCase(
            "fullstack-boundary-validation",
            "怎样定义 HTTP API 契约，并校验未知字段和非法输入？",
            ("fullstack-http", "fullstack-types", "fullstack-validation"),
        ),
        EvaluationCase(
            "fullstack-reproducible-ci",
            "pnpm、uv 与 Go 如何通过锁文件和 CI 实现可重复安装？",
            ("fullstack-toolchain", "fullstack-git"),
        ),
        EvaluationCase(
            "fullstack-component-accessibility",
            "React 组件怎样设计类型化 Props 和可访问的交互？",
            ("fullstack-components", "fullstack-design"),
        ),
        EvaluationCase(
            "fullstack-local-progress",
            "Jotai 如何保存学习进度，并在刷新后恢复持久化状态？",
            ("fullstack-jotai",),
        ),
        EvaluationCase(
            "fullstack-framework-contract",
            "Hono、Gin 和 FastAPI 怎样实现相同的分层接口契约？",
            ("fullstack-routing",),
        ),
        EvaluationCase(
            "fullstack-cancellation",
            "并发请求如何设置 deadline，并让取消传递到模型流？",
            ("fullstack-async", "fullstack-ai-stream"),
        ),
        EvaluationCase(
            "fullstack-safe-migrations",
            "数据库唯一约束、事务和迁移怎样保护数据一致性？",
            ("fullstack-database", "fullstack-migrations"),
        ),
        EvaluationCase(
            "fullstack-resource-auth",
            "认证、授权和会话如何防止跨用户访问？",
            ("fullstack-auth",),
        ),
        EvaluationCase(
            "fullstack-provider-errors",
            "Provider Adapter 如何适配模型并区分演示与上游失败？",
            ("fullstack-providers",),
        ),
        EvaluationCase(
            "fullstack-rag-upload",
            "文档问答怎样展示引用，并限制上传和 URL 摄取？",
            ("fullstack-ai-rag",),
        ),
        EvaluationCase("fullstack-no-evidence-baking", "烤箱烘焙可颂的温度是多少？", ()),
        EvaluationCase("fullstack-no-evidence-gardening", "如何给番茄种植设置浇水时间？", ()),
    ),
}


def corpus_revision(track: Track) -> str:
    """Hash actual retrieval fields and tie-breaking order, excluding unrelated UI data."""
    corpus = [
        {key: lesson[key] for key in ("id", "track", "title", "objective", "body")}
        for lesson in LESSONS.values()
        if lesson["track"] == track
    ]
    encoded = json.dumps(corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def case_metrics(retrieved_ids: list[str], relevant_ids: tuple[str, ...], top_k: int) -> dict:
    """Positive metrics use only the top-k unique ranked lessons; negatives are separate."""
    if not relevant_ids:
        return {
            "precision_at_k": None,
            "recall_at_k": None,
            "reciprocal_rank": None,
            "no_result_accuracy": float(not retrieved_ids),
        }
    relevant = set(relevant_ids)
    ranked = retrieved_ids[:top_k]
    hits = len(set(ranked) & relevant)
    first = next((index for index, id in enumerate(ranked, 1) if id in relevant), None)
    return {
        "precision_at_k": hits / top_k,
        "recall_at_k": hits / len(relevant),
        "reciprocal_rank": 1 / first if first else 0.0,
        "no_result_accuracy": None,
    }


def _evaluate_case(case: EvaluationCase, track: Track, config: RetrievalConfiguration) -> dict:
    matches = retrieve_scored(case.query, track, config.top_k, config.strategy)
    results = [
        {
            "id": item["lesson"]["id"],
            "title": item["lesson"]["title"],
            "score": item["score"],
            "matched_terms": item["matched_terms"],
            "is_relevant": item["lesson"]["id"] in case.relevant_ids,
        }
        for item in matches
    ]
    return {
        "results": results,
        "metrics": case_metrics([item["id"] for item in results], case.relevant_ids, config.top_k),
    }


def _summarize(cases: list[dict], comparison: str) -> dict:
    positives = [case[comparison]["metrics"] for case in cases if not case["is_negative"]]
    negatives = [case[comparison]["metrics"] for case in cases if case["is_negative"]]
    return {
        "positive_cases": len(positives),
        "negative_cases": len(negatives),
        "precision_at_k": sum(item["precision_at_k"] for item in positives) / len(positives),
        "recall_at_k": sum(item["recall_at_k"] for item in positives) / len(positives),
        "mrr": sum(item["reciprocal_rank"] for item in positives) / len(positives),
        "no_result_accuracy": sum(item["no_result_accuracy"] for item in negatives)
        / len(negatives),
    }


def evaluate_retrieval(request: RetrievalEvaluationRequest) -> dict:
    """Evaluate the fixed course corpus only; no remote IO, provider, or code execution."""
    cases = []
    for case in DATASETS[request.track]:
        cases.append(
            {
                "id": case.id,
                "query": case.query,
                "is_negative": not case.relevant_ids,
                "relevant": [{"id": id, "title": LESSONS[id]["title"]} for id in case.relevant_ids],
                "baseline": _evaluate_case(case, request.track, request.baseline),
                "candidate": _evaluate_case(case, request.track, request.candidate),
            }
        )
    return {
        "track": request.track,
        "dataset_version": DATASET_VERSION,
        "corpus_revision": corpus_revision(request.track),
        "run_id": str(uuid4()),
        "model_calls": 0,
        "notice": NOTICE,
        "configurations": {
            "baseline": request.baseline.model_dump(),
            "candidate": request.candidate.model_dump(),
        },
        "metrics": {
            comparison: _summarize(cases, comparison) for comparison in ("baseline", "candidate")
        },
        "cases": cases,
    }
