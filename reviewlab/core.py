from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))
STEAM_URL = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
ASPECTS = ("难度与容错", "战斗资源", "构筑与强弱", "成长节奏", "资源经济", "其他体验")
POLARITIES = ("positive", "negative", "mixed", "neutral")


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix):
    return prefix + "_" + datetime.now(CST).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dump_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def games():
    return json.loads((ROOT / "config/games.json").read_text(encoding="utf-8"))


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(".env 存在缺少等号的配置行")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("DEEPSEEK_"):
            os.environ.setdefault(key, value.strip("\"'"))


def connect(path=None):
    path = Path(path) if path else ROOT / "data/reviews.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.create_function("sample_tie_key", 2, lambda rid, seed: digest(f"{seed}:{rid}"), deterministic=True)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript((ROOT / "sql/schema.sql").read_text(encoding="utf-8"))
    with db:
        db.executemany("INSERT OR IGNORE INTO games VALUES (:appid,:slug,:name)", games())
        db.executemany("INSERT OR IGNORE INTO aspects VALUES (?)", [(a,) for a in ASPECTS])
    return db


def rows(db, sql, params=()):
    return [dict(r) for r in db.execute(sql, params)]


def require_row(db, table, item_id):
    if table not in {"datasets", "analysis_runs"}:
        raise ValueError("不支持的表")
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise ValueError(f"找不到 {table}: {item_id}")
    return row


def date_window(days=30, start=None, end=None):
    end_date = datetime.fromisoformat(end).replace(tzinfo=CST) if end else datetime.now(CST).replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = datetime.fromisoformat(start).replace(tzinfo=CST) if start else end_date - timedelta(days=days)
    if start_date >= end_date:
        raise ValueError("开始时间必须早于结束时间，结束日期不包含在窗口内")
    return int(start_date.timestamp()), int(end_date.timestamp())


class HTTPFailure(RuntimeError):
    def __init__(self, message, attempts=0):
        super().__init__(message)
        self.attempts = attempts


def request_json(method, url, *, session=None, attempts=3, **kwargs):
    """不打印请求头、密钥或响应正文；超时重试可能发生服务器端重复计费。"""
    client = session or requests
    for attempt in range(1, attempts + 1):
        try:
            r = client.request(method, url, **kwargs)
        except requests.RequestException:
            if attempt == attempts:
                raise HTTPFailure(f"网络请求失败（已尝试 {attempt} 次，未输出密钥或响应正文）", attempt) from None
        else:
            if r.status_code == 200:
                try:
                    return r.json(), attempt
                except ValueError:
                    raise HTTPFailure("HTTP 200，但响应不是有效 JSON", attempt) from None
            if r.status_code != 429 and r.status_code < 500:
                raise HTTPFailure(f"HTTP {r.status_code}，停止重试，请检查接口配置或账户", attempt)
            if attempt == attempts:
                raise HTTPFailure(f"HTTP {r.status_code}，已达到重试上限", attempt)
        time.sleep(min(2 ** (attempt - 1), 8))
    raise HTTPFailure("请求未完成")


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, records, fields=None):
    records = list(records)
    fields = fields or (list(records[0]) if records else [])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: csv_value(r.get(k)) for k in fields} for r in records)
