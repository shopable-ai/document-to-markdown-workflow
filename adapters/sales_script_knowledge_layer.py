#!/usr/bin/env python3
"""Build a deterministic, source-grounded knowledge layer from Markdown files."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import struct
import unicodedata
from pathlib import Path
from typing import Any, Iterable


EXPECTED_TREE_HASH = "11c2e9ac04bd8f1dba5dad1c6d939dd89b71ae015ea50d7704724a24d643925a"
EXPECTED_TREE_FILES = 3739
EXPECTED_TREE_BYTES = 251108525
EXPECTED_MARKDOWN_FILES = 389

THEMES: dict[str, dict[str, Any]] = {
    "开场破冰": {"keywords": ["开场", "开场白", "破冰", "第一印象", "自我介绍"], "objective": "建立初始注意与沟通许可"},
    "关系建立": {"keywords": ["关系", "信任", "亲和", "赞美", "倾听", "沟通"], "objective": "建立信任并维持对话"},
    "需求发现": {"keywords": ["需求", "痛点", "提问", "了解客户", "挖掘", "问题"], "objective": "识别显性与潜在需求"},
    "资格判断": {"keywords": ["预算", "决策人", "购买力", "资格", "意向", "决策"], "objective": "判断机会质量与决策条件"},
    "价值呈现": {"keywords": ["价值", "利益", "好处", "优势", "卖点", "fab", "fabe"], "objective": "把产品能力连接到客户价值"},
    "产品演示": {"keywords": ["演示", "展示", "试用", "体验", "示范", "操作"], "objective": "通过演示帮助客户理解方案"},
    "异议处理": {"keywords": ["异议", "拒绝", "担心", "不需要", "考虑一下", "太贵", "没兴趣"], "objective": "识别并回应客户顾虑"},
    "报价议价": {"keywords": ["报价", "价格", "优惠", "折扣", "议价", "砍价", "便宜"], "objective": "解释价格并推进条件协商"},
    "成交推进": {"keywords": ["成交", "签单", "购买", "下单", "促成", "逼单", "付款"], "objective": "确认承诺并推进交易动作"},
    "跟进复购": {"keywords": ["跟进", "回访", "复购", "续费", "老客户", "转介绍", "邀约"], "objective": "维持关系并推动后续机会"},
    "客服售后": {"keywords": ["售后", "投诉", "客服", "服务", "退货", "退款", "维护"], "objective": "处理交付后的服务问题"},
    "销售方法与训练": {"keywords": ["销售技巧", "培训", "方法", "原则", "管理", "心态", "流程"], "objective": "沉淀通用销售方法与训练材料"},
}

CHANNELS = {
    "电话": ["电话", "电销", "来电"],
    "面谈/门店": ["面谈", "门店", "店面", "导购", "接待"],
    "线上沟通": ["微信", "网络", "在线", "短信", "邮件", "qq"],
    "邀约": ["邀约", "预约", "到店"],
    "回访": ["回访", "跟进", "复访"],
}

INDUSTRIES = {
    "汽车": ["汽车", "4s店", "车险"],
    "房地产": ["房地产", "房产", "楼盘", "置业"],
    "金融保险": ["保险", "银行", "贷款", "理财", "金融"],
    "医疗健康": ["医疗", "医院", "药品", "健康", "保健"],
    "教育培训": ["教育", "培训", "课程", "学校"],
    "零售门店": ["门店", "导购", "零售", "服装", "珠宝"],
    "互联网/企业服务": ["软件", "网站", "互联网", "saas", "企业服务"],
    "家居建材": ["家具", "家居", "建材", "装修"],
}

OBJECTIONS = {
    "价格异议": ["太贵", "价格高", "便宜", "打折", "预算"],
    "需求异议": ["不需要", "没需求", "不感兴趣", "没兴趣"],
    "时间异议": ["没时间", "以后再说", "考虑一下", "改天"],
    "信任异议": ["不相信", "担心", "风险", "骗人", "可靠"],
    "竞品比较": ["别家", "竞争", "对比", "其他品牌"],
}

METHODS = {
    "FABE": ["fabe", "fab法", "特征", "利益"],
    "SPIN": ["spin", "背景问题", "难点问题", "暗示问题"],
    "顾问式销售": ["顾问式", "诊断", "解决方案"],
    "故事/案例": ["故事", "案例", "见证"],
    "提问与倾听": ["提问", "倾听", "追问"],
}

RISKS = {
    "强压/操控": ["逼单", "歹毒", "攻心", "操控", "必须买", "最后机会"],
    "欺骗/夸大": ["欺骗", "骗人", "百分之百", "绝对保证", "包治", "无风险"],
    "过时承诺": ["永久免费", "终身免费", "永不", "永久有效"],
    "金融敏感": ["贷款", "理财", "收益", "保险", "投资", "利率"],
    "医疗敏感": ["治疗", "疗效", "疾病", "药品", "医疗", "保健品"],
    "法律敏感": ["合同", "法律", "违法", "诉讼", "赔偿"],
    "隐私/个人信息": ["身份证", "手机号", "个人信息", "隐私"],
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_fingerprint(root: Path) -> dict[str, Any]:
    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        size = path.stat().st_size
        digest.update(struct.pack(">Q", len(relative)))
        digest.update(relative)
        digest.update(struct.pack(">Q", size))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        total_bytes += size
    return {"files": len(files), "bytes": total_bytes, "sha256": digest.hexdigest()}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value)


def normalized_document(value: str) -> str:
    return normalize(value)


def paragraph_key(value: str) -> str:
    value = re.sub(r"(?m)^#{1,6}\s+", "", value)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    return normalize(value)


def markdown_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    heading = ""
    offset = 0
    active_start: int | None = None
    active_lines: list[str] = []

    def flush(end: int) -> None:
        nonlocal active_start, active_lines
        if active_start is None:
            return
        raw = "".join(active_lines).strip()
        leading = len("".join(active_lines)) - len("".join(active_lines).lstrip())
        start = active_start + leading
        if raw and len(paragraph_key(raw)) >= 40:
            blocks.append({"raw": raw, "start": start, "end": start + len(raw), "heading": heading})
        active_start = None
        active_lines = []

    for line in text.splitlines(keepends=True):
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line.rstrip("\r\n"))
        if not line.strip():
            flush(offset)
        else:
            if active_start is None:
                active_start = offset
            active_lines.append(line)
        if heading_match:
            heading = heading_match.group(1)
        offset += len(line)
    flush(len(text))
    return blocks


def tag_matches(text: str, mapping: dict[str, list[str]]) -> list[str]:
    lowered = unicodedata.normalize("NFKC", text).lower()
    scored = []
    for label, keywords in mapping.items():
        score = sum(lowered.count(keyword.lower()) for keyword in keywords)
        if score:
            scored.append((score, label))
    return [label for _, label in sorted(scored, key=lambda item: (-item[0], item[1]))]


def classify(text: str) -> dict[str, Any]:
    theme_scores = []
    lowered = unicodedata.normalize("NFKC", text).lower()
    for theme, config in THEMES.items():
        score = sum(lowered.count(keyword.lower()) for keyword in config["keywords"])
        if score:
            theme_scores.append((score, theme))
    theme_scores.sort(key=lambda item: (-item[0], item[1]))
    stages = [theme for _, theme in theme_scores[:2]] or ["销售方法与训练"]
    channels = tag_matches(text, CHANNELS) or ["未明确"]
    industries = tag_matches(text, INDUSTRIES) or ["通用"]
    objections = tag_matches(text, OBJECTIONS)
    methods = tag_matches(text, METHODS)
    risks = tag_matches(text, RISKS)
    primary = stages[0]
    return {
        "primary_theme": primary,
        "sales_stage": stages,
        "channel": channels,
        "industry": industries,
        "objection_type": objections,
        "sales_method": methods,
        "risk_tags": risks,
        "scenario": primary,
        "customer_signal": objections[0] if objections else "原文未显式描述",
        "objective": THEMES[primary]["objective"],
    }


def image_links(text: str) -> list[str]:
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)


def headings(text: str) -> list[dict[str, Any]]:
    return [{"level": len(m.group(1)), "text": m.group(2).strip()} for m in re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text)]


def json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl_write(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def ensure_output(output_root: Path, input_root: Path) -> None:
    if output_root.resolve() == input_root.resolve() or input_root.resolve() in output_root.resolve().parents:
        raise SystemExit("output_root must be outside input_root")
    marker = output_root / ".state" / "build-config.json"
    if output_root.exists() and any(output_root.iterdir()) and not marker.is_file():
        raise SystemExit(f"refusing non-empty unowned output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    if marker.is_file():
        prior = json.loads(marker.read_text(encoding="utf-8"))
        if Path(prior["input_root"]).resolve() != input_root.resolve():
            raise SystemExit("existing output state belongs to a different input root")


def clean_generated(output_root: Path) -> None:
    pilot_report = None
    pilot_path = output_root / "reports" / "pilot-validation.json"
    if pilot_path.is_file():
        pilot_report = pilot_path.read_bytes()
    for name in ["主题库", "话术卡片", "索引", "reports"]:
        path = output_root / name
        if path.exists():
            shutil.rmtree(path)
    for name in ["README.md", "00-总目录.md", "taxonomy.yaml", "source-index.jsonl", "topic-index.json", "duplicate-clusters.json"]:
        path = output_root / name
        if path.exists():
            path.unlink()
    if pilot_report is not None:
        pilot_path.parent.mkdir(parents=True, exist_ok=True)
        pilot_path.write_bytes(pilot_report)


def build(args: argparse.Namespace) -> int:
    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    ensure_output(output_root, input_root)
    clean_generated(output_root)
    state = output_root / ".state"
    state.mkdir(parents=True, exist_ok=True)
    before = tree_fingerprint(input_root)
    expected = {"files": args.expected_tree_files, "bytes": args.expected_tree_bytes, "sha256": args.expected_tree_hash}
    if before != expected:
        raise SystemExit(f"input tree baseline mismatch: {before}")
    json_write(state / "build-config.json", {"input_root": str(input_root), "expected": expected})
    started = now()
    progress = {"status": "running", "phase": "pilot" if args.pilot else "full", "started_at": started, "input_tree_before": before}
    json_write(state / "progress-current.json", progress)
    with (state / "progress.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(progress, ensure_ascii=False) + "\n")

    files = sorted(input_root.rglob("*.md"), key=lambda p: p.relative_to(input_root).as_posix())
    if len(files) != args.expected_markdown_files:
        raise SystemExit(f"expected {args.expected_markdown_files} Markdown files, found {len(files)}")

    source_records: list[dict[str, Any]] = []
    block_occurrences: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    block_wording: dict[str, str] = {}
    raw_doc_groups: dict[str, list[str]] = collections.defaultdict(list)
    normalized_doc_groups: dict[str, list[str]] = collections.defaultdict(list)
    repeated_paragraphs: dict[str, dict[str, Any]] = {}
    paragraph_sources: dict[str, set[str]] = collections.defaultdict(set)
    effective_paragraph_characters = 0
    total_characters = 0
    total_markdown_bytes = 0
    total_image_links = 0

    for path in files:
        relative = path.relative_to(input_root).as_posix()
        data = path.read_bytes()
        text = data.decode("utf-8")
        total_characters += len(text)
        total_markdown_bytes += len(data)
        links = image_links(text)
        total_image_links += len(links)
        title_match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        title = title_match.group(1).strip() if title_match else path.stem
        source_blocks = markdown_blocks(text)
        unit_raw_ids = []
        source_themes: set[str] = set()
        for index, block in enumerate(source_blocks, 1):
            raw = block["raw"]
            raw_hash = sha256_bytes(raw.encode("utf-8"))
            block_wording[raw_hash] = raw
            classification = classify(f"{title}\n{block['heading']}\n{raw}")
            source_themes.update(classification["sales_stage"])
            occurrence = {
                "source_path": relative,
                "source_title": title,
                "heading": block["heading"] or title,
                "paragraph_index": index,
                "start_char": block["start"],
                "end_char": block["end"],
                "excerpt_sha256": raw_hash,
            }
            block_occurrences[raw_hash].append(occurrence)
            unit_raw_ids.append(raw_hash)
        source_paragraph_keys: set[str] = set()
        for raw_block in re.split(r"\n\s*\n+", text):
            key = paragraph_key(raw_block)
            if len(key) >= 40:
                paragraph_sources[key].add(relative)
                source_paragraph_keys.add(key)
        effective_paragraph_characters += sum(len(key) for key in source_paragraph_keys)
        raw_doc_groups[sha256_bytes(data)].append(relative)
        normalized_doc_groups[sha256_bytes(normalized_document(text).encode("utf-8"))].append(relative)
        source_records.append({
            "id": "SRC-" + sha256_bytes(relative.encode("utf-8"))[:16],
            "path": relative,
            "absolute_path": str(path),
            "title": title,
            "status": "processed",
            "unclassified_reason": None,
            "needs_review_reason": None,
            "bytes": len(data),
            "characters": len(text),
            "sha256": sha256_bytes(data),
            "headings": headings(text),
            "image_references": links,
            "topics": sorted(source_themes or {"销售方法与训练"}),
            "candidate_unit_count": len(unit_raw_ids),
            "classification_method": "deterministic-keyword-v1",
        })

    for key, sources in paragraph_sources.items():
        if len(sources) > 1:
            repeated_paragraphs[key] = {"normalized_sha256": sha256_bytes(key.encode("utf-8")), "normalized_characters": len(key), "member_sources": sorted(sources)}

    selected_theme_set = set(THEMES)
    if args.pilot:
        selected_theme_set = {"开场破冰", "异议处理", "成交推进"}

    units: list[dict[str, Any]] = []
    source_to_units: dict[str, list[str]] = collections.defaultdict(list)
    topic_to_units: dict[str, list[str]] = collections.defaultdict(list)
    for raw_hash in sorted(block_wording):
        wording = block_wording[raw_hash]
        refs = block_occurrences[raw_hash]
        first = refs[0]
        classification = classify(f"{first['source_title']}\n{first['heading']}\n{wording}")
        if args.pilot and classification["primary_theme"] not in selected_theme_set:
            continue
        unit_id = "KU-" + raw_hash[:20]
        risks = classification["risk_tags"]
        unit = {
            "id": unit_id,
            "content_type": "verbatim",
            "sales_stage": classification["sales_stage"],
            "channel": classification["channel"],
            "industry": classification["industry"],
            "scenario": classification["scenario"],
            "customer_signal": classification["customer_signal"],
            "objective": classification["objective"],
            "wording": wording,
            "next_step": "未从该摘录中确定；使用前结合完整来源上下文人工判断。",
            "usage_boundary": "历史语料摘录，不自动代表当前最佳实践；涉及承诺、价格、健康、金融或法律内容时须复核。",
            "risk_tags": risks,
            "objection_type": classification["objection_type"],
            "sales_method": classification["sales_method"],
            "source_refs": refs,
            "confidence": 0.78 if risks else 0.92,
            "needs_review_reason": "检测到潜在风险表达，使用前需人工复核。" if risks else None,
            "extraction_method": "deterministic-verbatim-paragraph-v1",
        }
        units.append(unit)
        for ref in refs:
            source_to_units[ref["source_path"]].append(unit_id)
        for topic in classification["sales_stage"]:
            topic_to_units[topic].append(unit_id)

    card_root = output_root / "话术卡片"
    card_root.mkdir(parents=True, exist_ok=True)
    unit_by_id = {unit["id"]: unit for unit in units}
    for unit in units:
        source_lines = "\n".join(f"- `{ref['source_path']}` · {ref['heading']} · 段落 {ref['paragraph_index']} · `{ref['excerpt_sha256']}`" for ref in unit["source_refs"])
        content = (
            f"# {unit['id']}\n\n"
            f"- 内容类型：`{unit['content_type']}`\n"
            f"- 销售阶段：{'、'.join(unit['sales_stage'])}\n"
            f"- 渠道：{'、'.join(unit['channel'])}\n"
            f"- 行业：{'、'.join(unit['industry'])}\n"
            f"- 场景：{unit['scenario']}\n"
            f"- 客户信号/异议：{unit['customer_signal']}\n"
            f"- 使用目标：{unit['objective']}\n"
            f"- 置信度：{unit['confidence']}\n"
            f"- 风险标签：{'、'.join(unit['risk_tags']) if unit['risk_tags'] else '无自动命中'}\n\n"
            f"## 原文话术\n\n<pre>{html.escape(unit['wording'])}</pre>\n\n"
            f"## 建议下一步\n\n{unit['next_step']}\n\n"
            f"## 适用边界\n\n{unit['usage_boundary']}\n\n"
            f"## 来源\n\n{source_lines}\n"
        )
        (card_root / f"{unit['id']}.md").write_text(content, encoding="utf-8")

    topic_root = output_root / "主题库"
    topic_root.mkdir(parents=True, exist_ok=True)
    topic_index: dict[str, Any] = {"format_version": "1.0", "topics": {}}
    for theme, config in THEMES.items():
        ids = sorted(set(topic_to_units.get(theme, [])))
        topic_index["topics"][theme] = {"unit_count": len(ids), "unit_ids": ids}
        links = "\n".join(f"- [{unit_id}](../话术卡片/{unit_id}.md)" for unit_id in ids)
        (topic_root / f"{theme}.md").write_text(
            f"# {theme}\n\n目标：{config['objective']}\n\n本页仅索引可回指原文的知识单元，不构成当前销售或合规建议。\n\n## 话术单元（{len(ids)}）\n\n{links or '- 本次范围内无匹配单元。'}\n",
            encoding="utf-8",
        )

    source_to_topic = {}
    for record in source_records:
        path = record["path"]
        ids = sorted(set(source_to_units.get(path, [])))
        source_to_topic[path] = {"source_id": record["id"], "status": record["status"], "topics": record["topics"], "unit_ids": ids}

    taxonomy = {
        "format_version": "1.0",
        "primary_topics": [{"id": name, "objective": config["objective"], "keywords": config["keywords"]} for name, config in THEMES.items()],
        "dimensions": {
            "sales_stage": list(THEMES),
            "channel": list(CHANNELS) + ["未明确"],
            "industry": list(INDUSTRIES) + ["通用"],
            "objection_type": list(OBJECTIONS),
            "sales_method": list(METHODS),
            "risk": list(RISKS),
        },
        "classification": {"method": "deterministic-keyword-v1", "fallback": "销售方法与训练", "uncertain_channel": "未明确"},
    }
    json_write(output_root / "taxonomy.yaml", taxonomy)
    jsonl_write(output_root / "source-index.jsonl", source_records)
    json_write(output_root / "topic-index.json", topic_index)
    json_write(output_root / "索引" / "source-to-topic.json", {"format_version": "1.0", "sources": source_to_topic})
    json_write(output_root / "索引" / "phrase-to-source.json", {"format_version": "1.0", "units": units})

    exact_groups = [members for members in raw_doc_groups.values() if len(members) > 1]
    normalized_groups = [members for members in normalized_doc_groups.values() if len(members) > 1]
    duplicate_payload = {
        "format_version": "1.0",
        "algorithms": {
            "exact_document": "sha256-file-bytes",
            "normalized_document": "NFKC+lower+retain-only-alnum-and-CJK+sha256",
            "cross_document_paragraph": "blank-line-blocks+strip-heading-markers+strip-images+NFKC+lower+retain-only-alnum-and-CJK; min_normalized_chars=40",
        },
        "exact_document_groups": [{"canonical_id": "DOC-EXACT-" + sha256_bytes("\n".join(members).encode())[:16], "member_sources": members, "algorithm": "sha256-file-bytes"} for members in exact_groups],
        "normalized_document_groups": [{"canonical_id": "DOC-NORM-" + sha256_bytes("\n".join(members).encode())[:16], "member_sources": members, "algorithm": "normalized-document-v1"} for members in normalized_groups],
        "cross_document_repeated_paragraphs": [{"canonical_id": "PARA-" + item["normalized_sha256"][:20], **item, "algorithm": "normalized-paragraph-v1", "parameters": {"min_normalized_characters": 40}} for item in sorted(repeated_paragraphs.values(), key=lambda x: x["normalized_sha256"])],
    }
    json_write(output_root / "duplicate-clusters.json", duplicate_payload)

    inventory = {
        "format_version": "1.0", "generated_at": now(), "input_root": str(input_root),
        "markdown_files": len(files), "markdown_bytes": total_markdown_bytes, "markdown_characters": total_characters,
        "tree": before, "heading_count": sum(len(r["headings"]) for r in source_records), "image_reference_count": total_image_links,
        "exact_document_duplicate_groups": len(exact_groups), "normalized_document_duplicate_groups": len(normalized_groups),
        "cross_document_repeated_paragraph_classes": len(repeated_paragraphs),
        "documents_with_cross_document_repeated_paragraphs": len(set().union(*(set(x["member_sources"]) for x in repeated_paragraphs.values()))) if repeated_paragraphs else 0,
        "effective_paragraph_characters": effective_paragraph_characters,
        "eliminable_repeated_paragraph_characters": sum(item["normalized_characters"] * (len(item["member_sources"]) - 1) for item in repeated_paragraphs.values()),
    }
    inventory["eliminable_repeated_paragraph_ratio"] = round(
        inventory["eliminable_repeated_paragraph_characters"] / effective_paragraph_characters, 6
    ) if effective_paragraph_characters else 0
    json_write(output_root / "reports" / "inventory.json", inventory)
    json_write(output_root / "reports" / "token-usage.json", {
        "format_version": "1.0", "generated_at": now(),
        "codex_conversation": {"calls": "unknown", "input_tokens": "unknown", "output_tokens": "unknown", "corpus_text_characters": "unknown"},
        "local_or_lan_model": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "corpus_text_characters": 0},
        "external_cloud_model": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "corpus_text_characters": 0},
        "corpus_text_external_cloud_calls": 0, "corpus_text_external_cloud_characters": 0,
        "semantic_synthesis_status": "not-run",
        "note": "Corpus processing was deterministic and local; no semantic model was needed or invoked. Codex token and viewed-sample character counts are unavailable and recorded as unknown.",
    })
    processing = {
        "format_version": "1.0", "generated_at": now(), "mode": "pilot" if args.pilot else "full",
        "total": len(source_records), "completed": len(source_records), "processed": len(source_records), "unclassified": 0,
        "needs_review": 0, "failed": 0, "pending": 0, "silent_drop": 0,
        "knowledge_units": len(units), "source_references": sum(len(u["source_refs"]) for u in units),
        "content_types": dict(collections.Counter(u["content_type"] for u in units)),
        "risk_tagged_units": sum(bool(u["risk_tags"]) for u in units), "unsupported_unit_count": 0,
        "dangling_source_ref_count": 0, "synthesis_without_evidence_count": 0,
    }
    json_write(output_root / "reports" / "processing.json", processing)
    json_write(output_root / "reports" / "validation-result.json", {
        "format_version": "1.0",
        "status": "pending",
        "mode": "pilot" if args.pilot else "full",
        "errors": ["Independent validation has not run yet."],
    })

    readme = f"""# 212 销冠话术知识化整理

本目录是从只读的 389 份已验收 Markdown 派生的知识层。原始转换层未被修改。

- 来源索引：[source-index.jsonl](source-index.jsonl)
- 多维分类：[taxonomy.yaml](taxonomy.yaml)
- 主题入口：[00-总目录.md](00-总目录.md)
- 双向索引：[source-to-topic.json](索引/source-to-topic.json) / [phrase-to-source.json](索引/phrase-to-source.json)
- 重复证据：[duplicate-clusters.json](duplicate-clusters.json)
- 独立验收：[validation-result.json](reports/validation-result.json)

## 内容边界

本次生成 {len(units)} 个 `verbatim` 单元；每个单元都保存来源相对路径、段落位置和可复算摘录哈希。未生成 `normalized` 或 `canonical-synthesis` 内容，也未调用本地/LAN或外部云语义模型。原文中的强压、夸大、金融、医疗、法律或隐私表达仅作为历史证据保留并加风险标签，不代表推荐实践。
"""
    (output_root / "README.md").write_text(readme, encoding="utf-8")
    dimension_lines = []
    for label, mapping in [("销售阶段", {k: None for k in THEMES}), ("渠道", CHANNELS), ("行业", INDUSTRIES), ("异议", OBJECTIONS)]:
        dimension_lines.append(f"## 按{label}\n")
        dimension_lines.extend(f"- {item}" for item in mapping)
        dimension_lines.append("")
    theme_links = "\n".join(f"- [{theme}](主题库/{theme}.md)（{topic_index['topics'][theme]['unit_count']} 个单元）" for theme in THEMES)
    (output_root / "00-总目录.md").write_text(
        f"# 总目录\n\n## 按主题\n\n{theme_links}\n\n" + "\n".join(dimension_lines) + "\n## 按来源\n\n来源到主题及知识单元的完整入口见 [source-to-topic.json](索引/source-to-topic.json)。\n",
        encoding="utf-8",
    )

    after = tree_fingerprint(input_root)
    if after != before:
        raise SystemExit(f"input tree changed during build: before={before}, after={after}")
    processing["input_tree_before"] = before
    processing["input_tree_after"] = after
    json_write(output_root / "reports" / "processing.json", processing)
    completed = {"status": "generated", "phase": "pilot" if args.pilot else "full", "started_at": started, "completed_at": now(), "input_tree_before": before, "input_tree_after": after, "knowledge_units": len(units)}
    json_write(state / "progress-current.json", completed)
    with (state / "progress.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(completed, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "generated", "mode": processing["mode"], "sources": len(source_records), "units": len(units), "output_root": str(output_root)}, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--input-root", required=True)
    result.add_argument("--output-root", required=True)
    result.add_argument("--pilot", action="store_true")
    result.add_argument("--expected-tree-hash", default=EXPECTED_TREE_HASH)
    result.add_argument("--expected-tree-files", type=int, default=EXPECTED_TREE_FILES)
    result.add_argument("--expected-tree-bytes", type=int, default=EXPECTED_TREE_BYTES)
    result.add_argument("--expected-markdown-files", type=int, default=EXPECTED_MARKDOWN_FILES)
    return result


if __name__ == "__main__":
    raise SystemExit(build(parser().parse_args()))
