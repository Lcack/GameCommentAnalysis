"""全部是隔离的离线测试；虚构评论仅用于校验统计和失败处理。"""
import argparse
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from reviewlab.analyze import analyze, prepare, validate_mentions
from reviewlab.collect import collect, normalize, unpack_page
from reviewlab.core import HTTPFailure, ROOT, connect, date_window, request_json
from reviewlab.evaluate import evaluate, export_labels, gold_set, metrics
from reviewlab.report import table


def mention(evidence="闪两下就没体力了", **kwargs):
    return dict(aspect="战斗资源", issue="精力消耗偏高", polarity="negative", target=None, progress=None, evidence=evidence, **kwargs)


def steam_review(rid="1", text="闪两下就没体力了", minutes=120):
    return {"recommendationid": rid, "review": text, "language": "schinese",
            "timestamp_created": 1500, "timestamp_updated": 1500, "voted_up": False,
            "author": {"playtime_at_review": minutes}}


class ValidationTests(unittest.TestCase):
    def test_response_wrapper_and_error_are_distinct(self):
        self.assertEqual(unpack_page({"response": {"reviews": []}})["reviews"], [])
        with self.assertRaises(ValueError):
            unpack_page({"response": {"error": "no access"}})

    def test_zero_is_not_missing(self):
        self.assertEqual(normalize(steam_review(minutes=0), "d", 2358720, "raw", "now")["playtime_minutes"], 0)
        self.assertIsNone(normalize(steam_review(minutes=None), "d", 2358720, "raw", "now")["playtime_minutes"])

    def test_fabricated_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_mentions(json.dumps({"mentions": [mention()]}), "战斗很好")

    def test_duplicate_mentions_deduplicated(self):
        result = validate_mentions(json.dumps({"mentions": [mention(), mention()]}), "闪两下就没体力了")
        self.assertEqual(len(result), 1)
        self.assertEqual(validate_mentions('{"mentions":[]}', "好"), [])

    def test_invalid_labels_and_json_rejected(self):
        bad = mention()
        bad["polarity"] = "bad"
        for content in ("", "[]", json.dumps({"mentions": [bad]})):
            with self.assertRaises(ValueError):
                validate_mentions(content, "闪两下就没体力了")

    def test_beijing_window_end_exclusive(self):
        start, end = date_window(start="2026-10-06", end="2026-11-05")
        self.assertEqual(end-start, 30*86400)
        self.assertEqual(end, 1793808000)
        with self.assertRaises(ValueError):
            date_window(start="2026-11-06", end="2026-11-05")

    def test_html_escapes_untrusted_review(self):
        rendered = table([{"evidence": "<script>alert(1)</script>"}])
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_auth_failure_not_retried(self):
        session = Mock()
        session.request.return_value.status_code = 401
        with self.assertRaises(HTTPFailure) as ctx:
            request_json("POST", "https://example.invalid", session=session)
        self.assertEqual(ctx.exception.attempts, 1)
        self.assertEqual(session.request.call_count, 1)

    @patch("reviewlab.core.time.sleep")
    def test_rate_limit_retried(self, _):
        session = Mock()
        first, second = Mock(), Mock()
        first.status_code = 429
        second.status_code = 200
        second.json.return_value = {"ok": True}
        session.request.side_effect = [first, second]
        self.assertEqual(request_json("GET", "https://example.invalid", session=session), ({"ok": True}, 2))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root / "test.sqlite3")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def seed(self):
        db = self.db
        db.execute("INSERT INTO datasets VALUES ('d','now',1000,2000,'{}','complete')")
        db.execute("INSERT INTO collection_jobs(dataset_id,appid,status) VALUES ('d',2358720,'complete')")
        db.execute("INSERT INTO analysis_runs VALUES ('r','d','now','test-model','https://example.invalid','v1','h','json',?,42,10)",
                   (json.dumps({"max_tokens": 10, "temperature": 0, "thinking": {"type": "disabled"}, "http_attempts": 2, "timeout": 10}),))
        for rid, status in (("1", "success"), ("2", "success"), ("3", "failed"), ("4", "pending")):
            record = normalize(steam_review(rid), "d", 2358720, "raw", "now")
            self.db.execute(f"INSERT INTO reviews({','.join(record)}) VALUES ({','.join('?' for _ in record)})", tuple(record.values()))
            self.db.execute("INSERT INTO extraction_results(run_id,review_id,input_hash,status,updated_at) VALUES ('r',?,'hash',?,'now')", (rid, status))
        for i in range(2):
            self.db.execute("INSERT INTO mentions VALUES ('r','1',?,'战斗资源','精力偏高','negative',NULL,NULL,'闪两下就没体力了')", (i,))
        self.db.commit()

    def test_sql_denominator_excludes_failed_pending_and_deduplicates(self):
        self.seed()
        sql = (ROOT / "sql/04_aspect_rates.sql").read_text(encoding="utf-8")
        values = [dict(r) for r in self.db.execute(sql, {"run_id": "r"})]
        resource = next(r for r in values if r["aspect"] == "战斗资源")
        self.assertEqual(resource["success_reviews"], 2)
        self.assertEqual(resource["negative_reviews"], 1)
        self.assertEqual(resource["negative_mention_pct"], 50.0)
        self.assertEqual(resource["duration_group"], "02 2—10小时")
        for path in (ROOT / "sql").glob("0*.sql"):
            self.db.execute(path.read_text(encoding="utf-8"), {"dataset_id": "d", "run_id": "r", "seed": 42,
                            "min_minutes": 0, "min_chars": 0, "max_chars": 20000}).fetchall()

    def test_prepare_freezes_config_and_dry_run_needs_no_key(self):
        self.seed()
        args = argparse.Namespace(dataset="d", allow_partial=False, per_game=2, seed=42, prompt=str(ROOT / "prompts/extract_v1.txt"))
        with patch("reviewlab.analyze.ROOT", self.root), patch.dict("os.environ", {"DEEPSEEK_MODEL": "test-model", "DEEPSEEK_API_KEY": ""}):
            run = prepare(self.db, args)
            analyze(self.db, argparse.Namespace(run=run, limit=1, retry_failed=False, dry_run=True))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM extraction_results WHERE run_id=?", (run,)).fetchone()[0], 2)
        payload = json.loads((self.root / "data/requests" / run / "preview.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["model"], "test-model")
        self.assertNotIn("Authorization", json.dumps(payload))

    def test_annotation_export_excludes_development_and_refuses_overwrite(self):
        self.seed()
        dev, test = self.root / "dev.csv", self.root / "test.csv"
        args = argparse.Namespace(run="r", output=str(dev), split="development", per_game=2, seed=1, exclude=None)
        export_labels(self.db, args)
        with self.assertRaises(ValueError):
            export_labels(self.db, args)
        args.output, args.split, args.exclude = str(test), "test", str(dev)
        export_labels(self.db, args)
        def ids(path):
            with path.open(encoding="utf-8-sig", newline="") as f:
                return {r["review_id"] for r in csv.DictReader(f)}
        self.assertFalse(ids(dev) & ids(test))

    def test_collect_resume_does_not_duplicate(self):
        args = argparse.Namespace(page_size=2, max_pages=1, pause=0, days=30, dataset=None,
                                  game="wukong", start=None, end=None)
        page = {"response": {"reviews": [steam_review()], "cursor": "next"}}
        with patch("reviewlab.collect.ROOT", self.root), patch("reviewlab.collect.date_window", return_value=(1000, 2000)), patch("reviewlab.collect.request_json", return_value=(page, 1)):
            dataset = collect(self.db, args)
        self.assertEqual(self.db.execute("SELECT status FROM datasets").fetchone()[0], "partial")
        args.dataset = dataset
        args.max_pages = 0
        pages = [({"response": {"reviews": [steam_review()], "cursor": "next2"}}, 1),
                 ({"response": {"reviews": [], "cursor": "next2"}}, 1)]
        with patch("reviewlab.collect.ROOT", self.root), patch("reviewlab.collect.request_json", side_effect=pages):
            collect(self.db, args)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT status FROM datasets").fetchone()[0], "complete")

    def test_model_failure_recorded_and_valid_empty_is_success(self):
        self.seed()
        args = argparse.Namespace(run="r", limit=1, retry_failed=False, dry_run=False)
        invalid = {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}], "usage": {"total_tokens": 5}}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), patch("reviewlab.analyze.ROOT", self.root), patch("reviewlab.analyze.request_json", return_value=(invalid, 1)):
            with self.assertRaises(RuntimeError):
                analyze(self.db, args)
        self.assertEqual(self.db.execute("SELECT status FROM extraction_results WHERE review_id='4'").fetchone()[0], "failed")
        self.db.execute("UPDATE extraction_results SET status='pending' WHERE review_id='4'")
        self.db.commit()
        valid = {"choices": [{"finish_reason": "stop", "message": {"content": '{"mentions":[]}'}}]}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), patch("reviewlab.analyze.ROOT", self.root), patch("reviewlab.analyze.request_json", return_value=(valid, 1)):
            analyze(self.db, args)
        self.assertEqual(self.db.execute("SELECT status FROM extraction_results WHERE review_id='4'").fetchone()[0], "success")

    def test_evaluation_blank_is_unlabeled_empty_array_is_negative(self):
        self.seed()
        labels = self.root / "labels.csv"
        with labels.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["review_id", "split", "gold_labels_json"])
            w.writeheader()
            w.writerows([{"review_id": "1", "split": "test", "gold_labels_json": ""},
                         {"review_id": "2", "split": "test", "gold_labels_json": "[]"},
                         {"review_id": "3", "split": "test", "gold_labels_json": "[]"}])
        with patch("reviewlab.evaluate.ROOT", self.root):
            result = evaluate(self.db, argparse.Namespace(run="r", labels=str(labels)))
        self.assertEqual(result["annotated_test_reviews"], 2)
        self.assertEqual(result["evaluated_success_reviews"], 1)
        self.assertEqual(result["excluded_unsuccessful_reviews"], 1)
        self.assertIsNone(result["micro"]["recall"])
        self.assertEqual(gold_set([]), set())
        self.assertIsNone(metrics(0, 0, 0)["precision"])


if __name__ == "__main__":
    unittest.main()
