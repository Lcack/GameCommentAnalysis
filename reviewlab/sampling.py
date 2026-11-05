from __future__ import annotations

import json
import math
import random

from .core import ROOT, dump_json, new_id, require_row, rows, write_csv


def sampling_defaults():
    return json.loads((ROOT / "config/sampling.json").read_text(encoding="utf-8"))


def resolve_settings(args):
    defaults = sampling_defaults()
    mapping = {"mode": "sampling", "per_game": "per_game", "top_percent": "top_percent",
               "min_hours": "min_hours", "min_chars": "min_chars", "seed": "seed"}
    result = {key: getattr(args, flag, None) if getattr(args, flag, None) is not None else defaults[key]
              for key, flag in mapping.items()}
    raw_weights = getattr(args, "weights", None)
    result["weights"] = [float(w) for w in raw_weights.split(",")] if raw_weights else defaults["weights"]
    if result["mode"] not in ("longplay", "uniform"):
        raise ValueError("sampling 必须是 longplay 或 uniform")
    if result["per_game"] <= 0 or not math.isfinite(result["top_percent"]) or not 0 < result["top_percent"] <= 100:
        raise ValueError("per-game 必须为正，top-percent 必须大于0且不超过100")
    if not math.isfinite(result["min_hours"]) or result["min_hours"] < 0 or result["min_chars"] < 0:
        raise ValueError("min-hours/min-chars 不能为负或非有限值")
    weights = result["weights"]
    if len(weights) != 5 or any(not math.isfinite(w) or w <= 0 for w in weights):
        raise ValueError("weights 必须为5个正数，用英文逗号分隔")
    result["min_minutes"] = math.ceil(result["min_hours"]*60)
    result["max_chars"] = 20000
    result["tie_rule"] = "sha256(seed:review_id)"
    result["shortage_policy"] = "cap_then_redistribute_by_weights"
    return result


def apportion(total, weights):
    if total == 0:
        return [0]*len(weights)
    s = sum(weights)
    exact = [total*w/s for w in weights]
    result = [math.floor(v) for v in exact]
    for i in sorted(range(len(weights)), key=lambda i: (-(exact[i]-result[i]), i))[:total-sum(result)]:
        result[i] += 1
    return result


def capped_allocation(total, weights, capacities):
    if total < 0 or total > sum(capacities):
        raise ValueError("抽样总量超出可用评论数")
    target = apportion(total, weights)
    actual = [min(t, cap) for t, cap in zip(target, capacities)]
    remaining = total-sum(actual)
    while remaining:
        available = [i for i, cap in enumerate(capacities) if actual[i] < cap]
        increments = apportion(remaining, [weights[i] for i in available])
        for i, inc in zip(available, increments):
            added = min(inc, capacities[i]-actual[i])
            actual[i] += added
            remaining -= added
    return target, actual


def build_sample(db, dataset_id, settings):
    require_row(db, "datasets", dataset_id)
    ranked = rows(db, (ROOT / "sql/08_sampling_rank.sql").read_text(encoding="utf-8"),
                  {"dataset_id": dataset_id, **settings})
    selected, strata, summaries = [], [], []
    appids = [r[0] for r in db.execute("SELECT appid FROM collection_jobs WHERE dataset_id=? ORDER BY appid", (dataset_id,))]
    for appid in appids:
        full_pool = [r for r in ranked if r["appid"] == appid]
        count = math.ceil(len(full_pool)*settings["top_percent"]/100)
        pool = full_pool[:count]
        total = min(settings["per_game"], len(pool))
        game = db.execute("SELECT name FROM games WHERE appid=?", (appid,)).fetchone()[0]
        rng = random.Random(f"{settings['seed']}:{appid}")
        if settings["mode"] == "uniform":
            blocks, weights = [pool], [1]
        else:
            blocks, offset = [], 0
            for i in range(5):
                size = len(pool)//5 + (i < len(pool)%5)
                blocks.append(pool[offset:offset+size])
                offset += size
            weights = settings["weights"]
        target, actual = capped_allocation(total, weights, [len(b) for b in blocks])
        for i, block in enumerate(blocks):
            n = actual[i]
            for r in rng.sample(block, n):
                selected.append({**r, "block_index": i+1})
            strata.append({"appid": appid, "game": game, "block_index": i+1,
                "from_percent": round(100*(block[0]["rank_position"]-1)/len(full_pool), 6) if block else None,
                "to_percent": round(100*block[-1]["rank_position"]/len(full_pool), 6) if block else None,
                "pool_count": len(block), "target_count": target[i], "selected_count": n,
                "min_minutes": min((r["playtime_minutes"] for r in block), default=None),
                "max_minutes": max((r["playtime_minutes"] for r in block), default=None),
                "inclusion_probability": n/len(block) if block else None})
        summaries.append({"appid": appid, "game": game, "eligible_before_top": len(full_pool),
            "eligible_after_top": len(pool), "requested": settings["per_game"], "selected": total,
            "quota_adjusted": target != actual,
            "shortage": total < settings["per_game"]})
    random.Random(settings["seed"]).shuffle(selected)
    return selected, strata, summaries


def preview(db, args):
    dataset = require_row(db, "datasets", args.dataset)
    settings = resolve_settings(args)
    selected, strata, summaries = build_sample(db, args.dataset, settings)
    out = ROOT / "reports" / args.dataset / new_id("sampling_preview")
    dump_json(out / "抽样预览.json", {"dataset_status": dataset["status"], "settings": settings,
              "games": summaries, "strata": strata, "review_ids": [r["review_id"] for r in selected]})
    write_csv(out / "百分位分块.csv", strata)
    print(json.dumps({"dataset_status": dataset["status"], "settings": settings, "games": summaries, "strata": strata}, ensure_ascii=False, indent=2))
    print(f"仅预览，不调用API、不创建分析run：{out}")
    return out
