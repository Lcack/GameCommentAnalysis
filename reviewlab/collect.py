from __future__ import annotations

import json
import time

import requests

from .core import ROOT, STEAM_URL, date_window, digest, dump_json, games, new_id, now, request_json, require_row


def unpack_page(body):
    if not isinstance(body, dict):
        raise ValueError("Steam 响应不是对象")
    payload = body.get("response", body)
    if not isinstance(payload, dict) or not isinstance(payload.get("reviews"), list):
        raise ValueError("Steam 响应缺少 reviews 数组，不能将异常响应当作采集完成")
    return payload


def normalize(item, dataset_id, appid, raw_path, observed_at):
    if not isinstance(item, dict) or not isinstance(item.get("author"), dict):
        raise ValueError("评论或 author 结构无效")
    for field in ("recommendationid", "review", "timestamp_created"):
        if field not in item:
            raise ValueError(f"评论缺少字段 {field}")
    if not isinstance(item["review"], str):
        raise ValueError("评论原文不是字符串")
    author = item["author"]

    def optional_int(source, key):
        value = source.get(key)
        return int(value) if value is not None else None

    text = item["review"]
    return {
        "dataset_id": dataset_id, "review_id": str(item["recommendationid"]),
        "appid": appid, "review_text": text, "content_hash": digest(text),
        "language": item.get("language", ""),
        "timestamp_created": int(item["timestamp_created"]),
        "timestamp_updated": int(item.get("timestamp_updated", item["timestamp_created"])),
        "voted_up": optional_int(item, "voted_up"),
        "playtime_minutes": optional_int(author, "playtime_at_review"),
        "playtime_forever_minutes": optional_int(author, "playtime_forever"),
        "steam_purchase": optional_int(item, "steam_purchase"),
        "received_for_free": optional_int(item, "received_for_free"),
        "votes_up": optional_int(item, "votes_up"),
        "observed_at": observed_at, "raw_path": str(raw_path),
    }


def collect(db, args):
    if not 1 <= args.page_size <= 100 or args.max_pages < 0 or args.pause < 0 or args.days <= 0:
        raise ValueError("page-size 应为 1—100，max-pages/pause 不能为负，days 必须为正")
    if args.dataset:
        dataset = require_row(db, "datasets", args.dataset)
        dataset_id = dataset["id"]
        filters = json.loads(dataset["filters_json"])
        start_ts, end_ts = dataset["start_ts"], dataset["end_ts"]
        print("继续原窗口与筛选条件；--days/--start/--end/--game 不会修改已有数据集")
    else:
        selected = games() if args.game == "all" else [g for g in games() if args.game in (g["slug"], str(g["appid"]))]
        if not selected:
            raise ValueError("game 应为 all、phantom、wukong、onimusha 或对应 App ID")
        start_ts, end_ts = date_window(args.days, args.start, args.end)
        filters = {"filter": 1, "languages": ["schinese"], "review_type": 0,
                   "purchase_type": 1, "filter_offtopic_activity": False}
        dataset_id = new_id("steam")
        with db:
            db.execute("INSERT INTO datasets VALUES (?,?,?,?,?,?)",
                       (dataset_id, now(), start_ts, end_ts, json.dumps(filters), "collecting"))
            db.executemany("INSERT INTO collection_jobs(dataset_id,appid) VALUES (?,?)",
                           [(dataset_id, g["appid"]) for g in selected])
    print(f"dataset_id={dataset_id}", flush=True)
    failures = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "SteamReviewLab/0.1 (personal portfolio research)"})
        for job in db.execute("SELECT * FROM collection_jobs WHERE dataset_id=? ORDER BY appid", (dataset_id,)).fetchall():
            if job["status"] == "complete":
                continue
            cursor, page_count, appid = job["cursor"], job["page_count"], job["appid"]
            with db:
                db.execute("UPDATE collection_jobs SET status='collecting',error=NULL WHERE dataset_id=? AND appid=?", (dataset_id, appid))
            try:
                fetched = 0
                while True:
                    params = dict(filters, appid=appid, cursor=cursor, num_per_page=args.page_size,
                                  date_range_start=start_ts, date_range_end=end_ts - 1)
                    body, _ = request_json("GET", STEAM_URL, session=session,
                                           params={"input_json": json.dumps(params)}, timeout=(10, 45))
                    page = unpack_page(body)
                    raw_path = ROOT / "data/raw" / dataset_id / str(appid) / f"page_{page_count+1:06d}.json"
                    observed = now()
                    dump_json(raw_path, {"observed_at": observed, "request": params, "body": body})
                    records = [normalize(v, dataset_id, appid, raw_path.relative_to(ROOT), observed) for v in page["reviews"]]
                    # 再次按本地窗口与语言校验，防止接口参数未生效时误纳入。
                    records = [r for r in records if start_ts <= r["timestamp_created"] < end_ts and r["language"] == "schinese"]
                    next_cursor = page.get("cursor")
                    complete = not page["reviews"]
                    if not complete and (not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor):
                        raise ValueError("非空页缺少前进游标，停止以避免重复请求")
                    page_count += 1
                    fetched += 1
                    with db:
                        for record in records:
                            keys = list(record)
                            db.execute(f"INSERT OR IGNORE INTO reviews ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", tuple(record.values()))
                        db.execute("UPDATE collection_jobs SET cursor=?,page_count=?,status=? WHERE dataset_id=? AND appid=?",
                                   (next_cursor or cursor, page_count, "complete" if complete else "partial", dataset_id, appid))
                    print(f"  appid={appid} page={page_count} 本页纳入={len(records)} 状态={'complete' if complete else 'partial'}", flush=True)
                    if complete or (args.max_pages and fetched >= args.max_pages):
                        break
                    cursor = next_cursor
                    time.sleep(args.pause)
            except Exception as exc:
                if not isinstance(exc, (ValueError, RuntimeError, OSError)):
                    raise
                error = str(exc)
                with db:
                    db.execute("UPDATE collection_jobs SET status='failed',error=? WHERE dataset_id=? AND appid=?", (error, dataset_id, appid))
                failures.append(f"{appid}: {error}")
                print(f"  appid={appid} 采集失败: {error}", flush=True)
    statuses = [r[0] for r in db.execute("SELECT status FROM collection_jobs WHERE dataset_id=?", (dataset_id,))]
    status = "complete" if all(s == "complete" for s in statuses) else "failed" if "failed" in statuses else "partial"
    with db:
        db.execute("UPDATE datasets SET status=? WHERE id=?", (status, dataset_id))
    count = db.execute("SELECT COUNT(*) FROM reviews WHERE dataset_id=?", (dataset_id,)).fetchone()[0]
    print(f"完成：{dataset_id}，评论={count}，数据集状态={status}")
    if failures:
        raise RuntimeError("部分采集失败，已保留成功页面，可使用 --dataset 继续")
    return dataset_id
