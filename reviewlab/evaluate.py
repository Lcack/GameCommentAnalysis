from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from .core import ASPECTS, POLARITIES, ROOT, dump_json, new_id, require_row, rows, write_csv


def export_labels(db, args):
    require_row(db, "analysis_runs", args.run)
    path = Path(args.output).resolve() if args.output else ROOT / "data/annotations" / f"{args.run}_{args.split}.csv"
    if path.exists():
        raise ValueError("标注文件已存在，拒绝覆盖；请换一个 --output 路径")
    if args.per_game <= 0:
        raise ValueError("per-game 必须为正")
    pool = rows(db, """SELECT review_id,appid,game,review_text,duration_group
        FROM analysis_review_base WHERE run_id=? ORDER BY appid,review_id""", (args.run,))
    if args.exclude:
        with Path(args.exclude).open(encoding="utf-8-sig", newline="") as f:
            excluded = {r["review_id"] for r in csv.DictReader(f)}
        pool = [r for r in pool if r["review_id"] not in excluded]
    chosen = []
    rng = random.Random(args.seed)
    for appid in sorted({r["appid"] for r in pool}):
        group = [r for r in pool if r["appid"] == appid]
        chosen.extend(rng.sample(group, min(args.per_game, len(group))))
    for r in chosen:
        r.update(split=args.split, gold_labels_json="", notes="")
    if not chosen:
        raise ValueError("没有可导出的样本；排除开发集后可能已无剩余评论")
    fields = ["review_id", "appid", "game", "review_text", "duration_group", "split", "gold_labels_json", "notes"]
    write_csv(path, chosen, fields)
    print(f"已导出 {len(chosen)} 条盲标样本：{path}\n空白=未标注；[] = 人工确认无标签，二者不同。不要用 test 样本反复修改提示词。")


def gold_set(value):
    if not isinstance(value, list):
        raise ValueError("gold_labels_json 必须为 JSON 数组")
    labels = set()
    for label in value:
        if not isinstance(label, dict) or set(label) != {"aspect", "polarity"}:
            raise ValueError("人工标签须仅含 aspect 与 polarity")
        if label["aspect"] not in ASPECTS or label["polarity"] not in POLARITIES:
            raise ValueError("人工标签不在标签字典中")
        labels.add((label["aspect"], label["polarity"]))
    return labels


def metrics(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn, "support": tp+fn,
            "precision": round(tp/(tp+fp), 4) if tp+fp else None,
            "recall": round(tp/(tp+fn), 4) if tp+fn else None,
            "f1": round(2*tp/(2*tp+fp+fn), 4) if 2*tp+fp+fn else None}


def evaluate(db, args):
    require_row(db, "analysis_runs", args.run)
    predictions = {r["review_id"]: set() for r in db.execute("SELECT review_id FROM extraction_results WHERE run_id=? AND status='success'", (args.run,))}
    enrolled = {r["review_id"] for r in db.execute("SELECT review_id FROM extraction_results WHERE run_id=?", (args.run,))}
    for r in db.execute("SELECT review_id,aspect,polarity FROM mentions WHERE run_id=?", (args.run,)):
        if r["review_id"] in predictions:
            predictions[r["review_id"]].add((r["aspect"], r["polarity"]))
    records, seen = [], set()
    with Path(args.labels).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not {"review_id", "split", "gold_labels_json"}.issubset(reader.fieldnames or []):
            raise ValueError("标注 CSV 缺少必要列")
        for r in reader:
            rid = r["review_id"].strip()
            if rid in seen:
                raise ValueError("人工标注文件中存在重复评论 ID")
            seen.add(rid)
            if rid not in enrolled:
                raise ValueError(f"评论 {rid} 不属于本次分析运行")
            if r["split"] != "test" or not r["gold_labels_json"].strip():
                continue
            records.append((rid, gold_set(json.loads(r["gold_labels_json"]))))
    pairs = [(rid, gold, predictions[rid]) for rid, gold in records if rid in predictions]
    if not pairs:
        raise ValueError("没有同时具备人工 test 标签与成功模型结果的评论，不能计算准确率")
    counts = {label: [0, 0, 0] for label in ((a, p) for a in ASPECTS for p in POLARITIES)}
    errors = []
    for rid, gold, pred in pairs:
        for label in gold & pred:
            counts[label][0] += 1
        for label in pred - gold:
            counts[label][1] += 1
        for label in gold - pred:
            counts[label][2] += 1
        if gold != pred:
            errors.append({"review_id": rid, "missing": [list(x) for x in sorted(gold-pred)], "extra": [list(x) for x in sorted(pred-gold)]})
    detail = [{"aspect": a, "polarity": p, **metrics(*counts[(a,p)])} for a in ASPECTS for p in POLARITIES]
    totals = [sum(c[i] for c in counts.values()) for i in range(3)]
    result = {"run_id": args.run, "annotated_test_reviews": len(records), "evaluated_success_reviews": len(pairs),
              "excluded_unsuccessful_reviews": len(records)-len(pairs), "micro": metrics(*totals),
              "exact_set_match": round(sum(g == p for _, g, p in pairs)/len(pairs), 4),
              "limitations": ["评估单位为评论级 aspect+polarity 集合，不评估具体 issue/对象/进度的正确性。",
                              "样本量、稀少标签支持数和抽样方式限制结论；空分母输出 null。",
                              "仅在成功模型输出上计算分类指标，未成功项另列，不能据此宣称端到端准确率。"]}
    out = ROOT / "reports/evaluation" / args.run / new_id("eval")
    dump_json(out / "summary.json", result)
    dump_json(out / "errors.json", errors)
    write_csv(out / "per_label.csv", detail)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"评估已保存：{out}")
    return result
