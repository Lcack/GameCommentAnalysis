from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from .core import ASPECTS, POLARITIES, ROOT, HTTPFailure, digest, dump_json, load_env, new_id, now, request_json, require_row
from .sampling import build_sample, resolve_settings


def validate_mentions(content, review_text):
    value = json.loads(content)
    if not isinstance(value, dict) or set(value) != {"mentions"} or not isinstance(value["mentions"], list):
        raise ValueError("输出必须是仅含 mentions 数组的 JSON 对象")
    if len(value["mentions"]) > 30:
        raise ValueError("反馈超过 30 条")
    cleaned, seen = [], set()
    expected = {"aspect", "issue", "polarity", "target", "progress", "evidence"}
    for m in value["mentions"]:
        if not isinstance(m, dict) or set(m) != expected:
            raise ValueError("反馈字段不完整或含额外字段")
        if m["aspect"] not in ASPECTS or m["polarity"] not in POLARITIES:
            raise ValueError("标签或态度不在字典中")
        for name in ("issue", "evidence"):
            if not isinstance(m[name], str) or not m[name].strip():
                raise ValueError(f"{name} 不能为空")
        if len(m["issue"]) > 200 or len(m["evidence"]) > 4000 or m["evidence"] not in review_text:
            raise ValueError("证据不在原文中或字段过长")
        for name in ("target", "progress"):
            if m[name] is not None and (not isinstance(m[name], str) or not m[name].strip() or len(m[name]) > 300):
                raise ValueError(f"{name} 必须为非空短字符串或 null")
        fingerprint = json.dumps(m, sort_keys=True, ensure_ascii=False)
        if fingerprint not in seen:
            seen.add(fingerprint)
            cleaned.append(m)
    return cleaned


def prepare(db, args):
    load_env()
    dataset = require_row(db, "datasets", args.dataset)
    if dataset["status"] != "complete" and not args.allow_partial:
        raise ValueError("数据集尚未完整采集。先继续采集；仅验证流程可显式添加 --allow-partial")
    sampling = resolve_settings(args)
    prompt_path = ROOT / "prompts/extract_v1.txt" if not args.prompt else Path(args.prompt).resolve()
    prompt = prompt_path.read_text(encoding="utf-8")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API 地址必须是无凭据、查询参数和片段的 HTTPS URL")
    thinking = os.environ.get("DEEPSEEK_THINKING", "disabled")
    if thinking not in ("enabled", "disabled"):
        raise ValueError("DEEPSEEK_THINKING 必须是 enabled 或 disabled")
    settings = {"max_tokens": int(os.environ.get("DEEPSEEK_MAX_TOKENS", "2048")),
                "thinking": {"type": thinking}, "temperature": 0,
                "max_input_chars": 20000, "http_attempts": 2,
                "timeout": int(os.environ.get("DEEPSEEK_TIMEOUT", "90"))}
    settings["sampling"] = sampling
    if settings["max_tokens"] <= 0 or settings["timeout"] <= 0:
        raise ValueError("max_tokens/timeout 必须为正")
    run_id = new_id("ds")
    selected, strata, summaries = build_sample(db, args.dataset, sampling)
    if not selected:
        raise ValueError("数据集没有评论，不能准备分析运行")
    with db:
        db.execute("INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, args.dataset, now(), os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
                    base_url, prompt_path.stem, digest(prompt), prompt,
                    json.dumps(settings), sampling["seed"], sampling["per_game"]))
        for order, r in enumerate(selected):
            reason = "empty_text" if not r["review_text"].strip() else "too_long" if len(r["review_text"]) > settings["max_input_chars"] else None
            db.execute("INSERT INTO extraction_results(run_id,review_id,input_hash,status,error,updated_at) VALUES (?,?,?,?,?,?)",
                       (run_id, r["review_id"], r["content_hash"], "skipped" if reason else "pending", reason, now()))
            db.execute("INSERT INTO sample_members VALUES (?,?,?,?)", (run_id, r["review_id"], r["block_index"], order))
        for s in strata:
            db.execute("INSERT INTO sampling_strata VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (run_id, s["appid"], s["block_index"], s["from_percent"], s["to_percent"],
                        s["pool_count"], s["target_count"], s["selected_count"], s["min_minutes"],
                        s["max_minutes"], s["inclusion_probability"]))
    dump_json(ROOT / "data/manifests" / f"{run_id}.json", {
        "run_id": run_id, "dataset_id": args.dataset, "dataset_status": dataset["status"],
        "seed": sampling["seed"], "per_game": sampling["per_game"], "prompt_hash": digest(prompt),
        "sampling": sampling, "games": summaries, "strata": strata,
        "review_ids": [r["review_id"] for r in selected],
    })
    for s in summaries:
        warning = "；配额因块容量不足已调整" if s["quota_adjusted"] else ""
        print(f"{s['game']}：可用={s['eligible_after_top']}，目标={s['requested']}，实际={s['selected']}{warning}")
    print(f"run_id={run_id}，固定入选评论={len(selected)}；尚未调用模型")
    return run_id


def payload_for(run, review):
    settings = json.loads(run["settings_json"])
    return {
        "model": run["model"], "stream": False,
        "response_format": {"type": "json_object"},
        "max_tokens": settings["max_tokens"], "temperature": settings["temperature"],
        "thinking": settings["thinking"],
        "messages": [{"role": "system", "content": run["prompt_text"]},
                     {"role": "user", "content": json.dumps({"game": review["game"], "review_text": review["review_text"]}, ensure_ascii=False)}],
    }


def analyze(db, args):
    load_env()
    run = require_row(db, "analysis_runs", args.run)
    if args.limit <= 0:
        raise ValueError("limit 必须为正，用来限制本次最多处理的评论数")
    statuses = ("pending", "failed") if args.retry_failed else ("pending",)
    placeholders = ",".join("?" for _ in statuses)
    pending = db.execute(f"""SELECT r.*,g.name AS game FROM extraction_results e
        JOIN analysis_runs a ON a.id=e.run_id
        JOIN reviews r ON r.dataset_id=a.dataset_id AND r.review_id=e.review_id
        JOIN games g USING(appid)
        LEFT JOIN sample_members s ON s.run_id=e.run_id AND s.review_id=e.review_id
        WHERE e.run_id=? AND e.status IN ({placeholders})
        ORDER BY COALESCE(s.selection_order,2147483647),e.review_id LIMIT ?""",
        (args.run, *statuses, args.limit)).fetchall()
    if not pending:
        print("没有待处理评论；失败项需显式添加 --retry-failed")
        return
    if args.dry_run:
        path = ROOT / "data/requests" / args.run / "preview.json"
        dump_json(path, payload_for(run, pending[0]))
        print(f"仅生成请求预览，未联网、未计费：{path}\n本次最多处理 {len(pending)} 条；每条 HTTP 重试上限为 2 次")
        return
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("未配置 DEEPSEEK_API_KEY。请在本地 .env 或环境变量配置，勿在聊天中发送密钥")
    settings = json.loads(run["settings_json"])
    failures = 0
    for review in pending:
        start = time.monotonic()
        response_path, usage, attempts = None, {}, 0
        try:
            body, attempts = request_json("POST", run["base_url"] + "/chat/completions",
                attempts=settings["http_attempts"], json=payload_for(run, review),
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                timeout=(10, settings["timeout"]))
            response_path = ROOT / "data/llm" / args.run / f"{review['review_id']}_{new_id('call')}.json"
            dump_json(response_path, body)
            if not isinstance(body, dict) or not isinstance(body.get("choices"), list) or not body["choices"]:
                raise ValueError("模型响应缺少非空 choices 数组")
            usage = body.get("usage", {})
            choice = body["choices"][0]
            if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
                raise ValueError("模型响应的 choice/message 结构无效")
            if choice.get("finish_reason") != "stop":
                raise ValueError("生成未正常结束，可能被截断，结果不会计作成功")
            content = choice.get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("模型返回空内容，结果不会计作未提及")
            mentions = validate_mentions(content, review["review_text"])
            with db:
                db.execute("DELETE FROM mentions WHERE run_id=? AND review_id=?", (args.run, review["review_id"]))
                for i, m in enumerate(mentions):
                    db.execute("INSERT INTO mentions VALUES (?,?,?,?,?,?,?,?,?)",
                        (args.run, review["review_id"], i, m["aspect"], m["issue"], m["polarity"], m["target"], m["progress"], m["evidence"]))
                db.execute("UPDATE extraction_results SET status='success',error=NULL WHERE run_id=? AND review_id=?", (args.run, review["review_id"]))
            print(f"  {review['review_id']}: success，反馈={len(mentions)}", flush=True)
        except (RuntimeError, ValueError, KeyError, IndexError, TypeError, OSError) as exc:
            failures += 1
            if isinstance(exc, HTTPFailure):
                attempts = exc.attempts
            # 不记录任意异常的原文，防止第三方库错误信息包含请求内容。
            error = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
            with db:
                db.execute("UPDATE extraction_results SET status='failed',error=? WHERE run_id=? AND review_id=?", (error, args.run, review["review_id"]))
            print(f"  {review['review_id']}: failed，{error}", flush=True)
        finally:
            with db:
                db.execute("""UPDATE extraction_results SET attempts=attempts+?,elapsed_seconds=?,
                    response_path=?,usage_json=?,updated_at=? WHERE run_id=? AND review_id=?""",
                    (attempts, round(time.monotonic()-start, 3), str(response_path.relative_to(ROOT)) if response_path else None,
                     json.dumps(usage), now(), args.run, review["review_id"]))
    if failures:
        raise RuntimeError(f"本次 {failures} 条失败，已保存状态；修正后用 --retry-failed 继续")
