-- 前值指上一个存在成功分析样本的日期，不保证是前一自然日。
WITH negative AS (
 SELECT DISTINCT run_id,review_id FROM mentions WHERE run_id=:run_id AND polarity='negative'
), daily AS (
 SELECT b.appid,b.game,b.created_day,COUNT(*) AS success_reviews,
 COUNT(n.review_id) AS any_negative_reviews,
 ROUND(100.0*COUNT(n.review_id)/COUNT(*),2) AS any_negative_pct
 FROM analysis_review_base b LEFT JOIN negative n
 ON n.run_id=b.run_id AND n.review_id=b.review_id
 WHERE b.run_id=:run_id AND b.extraction_status='success'
 GROUP BY b.appid,b.created_day
)
SELECT *,LAG(created_day) OVER(PARTITION BY appid ORDER BY created_day) AS previous_observed_day,
 ROUND(any_negative_pct-LAG(any_negative_pct) OVER(PARTITION BY appid ORDER BY created_day),2)
 AS change_vs_previous_observed_day_pp
FROM daily ORDER BY appid,created_day;
