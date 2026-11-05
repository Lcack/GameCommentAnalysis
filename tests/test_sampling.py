"""按时长百分位倾斜抽样的容量、边界、复现与审计测试。"""
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reviewlab.analyze import prepare
from reviewlab.core import ROOT, connect
from reviewlab.sampling import apportion, build_sample, capped_allocation, resolve_settings


class AllocationTests(unittest.TestCase):
    def test_5000_quota(self):
        target, actual = capped_allocation(5000, [50,25,15,7,3], [3000]*5)
        self.assertEqual(target, [2500,1250,750,350,150])
        self.assertEqual(actual, target)

    def test_shortage_redistribution_and_rounding(self):
        for n in range(1,100):
            target, actual = capped_allocation(n, [50,25,15,7,3], [20]*5)
            self.assertEqual(sum(actual), n)
            self.assertTrue(all(0 <= a <= 20 for a in actual))
        self.assertEqual(apportion(3,[50,25,15,7,3]), [2,1,0,0,0])
        self.assertEqual(capped_allocation(4,[50,25,15,7,3],[1,1,1,1,0])[1],[1,1,1,1,0])

    def test_invalid_settings_rejected(self):
        for key, value in (("weights","50,25,-1,7,3"),("weights","nan,25,15,7,3"),
                           ("top_percent",0),("top_percent",101),("min_hours",-1)):
            with self.assertRaises(ValueError):
                resolve_settings(argparse.Namespace(**{key:value}))


class SamplingDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root/'test.sqlite3')
        self.db.execute("INSERT INTO datasets VALUES ('d','now',1,2000,'{}','complete')")
        self.db.execute("INSERT INTO collection_jobs(dataset_id,appid,status) VALUES ('d',2358720,'complete')")
        for i in range(100):
            self.db.execute("""INSERT INTO reviews(dataset_id,review_id,appid,review_text,content_hash,language,
                timestamp_created,timestamp_updated,playtime_minutes,observed_at,raw_path)
                VALUES ('d',?,2358720,'具体反馈文本','h','schinese',1000,1000,?,'now','raw')""", (str(i), i*60))
        self.db.execute("UPDATE reviews SET playtime_minutes=NULL WHERE review_id='0'")
        self.db.execute("UPDATE reviews SET review_text='' WHERE review_id='1'")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def settings(self, **kwargs):
        return resolve_settings(argparse.Namespace(per_game=20, seed=42, **kwargs))

    def test_quotas_reproducible_and_long_block_highest(self):
        settings = self.settings()
        selected, strata, _ = build_sample(self.db,'d',settings)
        self.assertEqual(len(selected),20)
        self.assertEqual([s['selected_count'] for s in strata],[10,5,3,1,1])
        self.assertEqual(len({r['review_id'] for r in selected}),20)
        again = build_sample(self.db,'d',settings)[0]
        self.assertEqual([r['review_id'] for r in selected],[r['review_id'] for r in again])
        self.assertGreater(strata[0]['min_minutes'],strata[1]['max_minutes'])
        self.assertNotIn('0',{r['review_id'] for r in selected})
        self.assertNotIn('1',{r['review_id'] for r in selected})

    def test_top_percent_is_applied_before_blocks(self):
        selected, strata, summary = build_sample(self.db,'d',self.settings(top_percent=50))
        self.assertEqual(summary[0]['eligible_before_top'],98)
        self.assertEqual(summary[0]['eligible_after_top'],49)
        self.assertTrue(all(r['playtime_minutes']>=51*60 for r in selected))
        self.assertEqual(strata[-1]['to_percent'],50)

    def test_min_hours_and_small_pool_no_replacement(self):
        selected, strata, summary = build_sample(self.db,'d',self.settings(min_hours=98))
        self.assertEqual(len(selected),2)
        self.assertTrue(summary[0]['shortage'])
        self.assertEqual(sum(s['pool_count'] for s in strata),2)

    def test_ties_are_seeded_and_missing_duration_excluded(self):
        self.db.execute("UPDATE reviews SET playtime_minutes=600 WHERE playtime_minutes IS NOT NULL")
        self.db.commit()
        a = build_sample(self.db,'d',self.settings(top_percent=10))[0]
        args = argparse.Namespace(per_game=20,seed=43,top_percent=10)
        b = build_sample(self.db,'d',resolve_settings(args))[0]
        self.assertNotEqual({r['review_id'] for r in a},{r['review_id'] for r in b})
        self.assertTrue(all(r['playtime_minutes']==600 for r in a))

    def test_prepare_persists_sampling_and_report_audit(self):
        args=argparse.Namespace(dataset='d',allow_partial=False,per_game=20,seed=42,prompt=str(ROOT/'prompts/extract_v1.txt'))
        with patch('reviewlab.analyze.ROOT',self.root):
            run=prepare(self.db,args)
        stored=json.loads(self.db.execute('SELECT settings_json FROM analysis_runs WHERE id=?',(run,)).fetchone()[0])
        self.assertEqual(stored['sampling']['weights'],[50,25,15,7,3])
        audit=self.db.execute((ROOT/'sql/09_sampling_audit.sql').read_text(encoding='utf-8'),{'run_id':run}).fetchall()
        self.assertEqual(sum(r['selected_count'] for r in audit),20)
        self.assertEqual(len(audit),5)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sample_members WHERE run_id=?',(run,)).fetchone()[0],20)

    def test_audit_keeps_games_separate(self):
        self.db.execute("INSERT INTO collection_jobs(dataset_id,appid,status) VALUES ('d',2638890,'complete')")
        self.db.execute("""INSERT INTO reviews SELECT dataset_id,'second_'||review_id,2638890,review_text,
            content_hash,language,timestamp_created,timestamp_updated,voted_up,playtime_minutes,
            playtime_forever_minutes,steam_purchase,received_for_free,votes_up,observed_at,raw_path
            FROM reviews WHERE appid=2358720""")
        self.db.commit()
        args=argparse.Namespace(dataset='d',allow_partial=False,per_game=20,seed=42,prompt=str(ROOT/'prompts/extract_v1.txt'))
        with patch('reviewlab.analyze.ROOT',self.root):
            run=prepare(self.db,args)
        self.db.execute("UPDATE extraction_results SET status='success' WHERE run_id=?",(run,))
        self.db.commit()
        audit=self.db.execute((ROOT/'sql/09_sampling_audit.sql').read_text(encoding='utf-8'),{'run_id':run}).fetchall()
        self.assertEqual(len(audit),10)
        self.assertEqual(sum(r['success_reviews'] for r in audit),40)
        for r in audit:
            self.assertEqual(r['success_reviews'],r['selected_count'])


if __name__=='__main__':
    unittest.main()
