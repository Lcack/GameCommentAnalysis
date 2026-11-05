-- 分母：指定运行、游戏和时长组内成功处理的去重评论；一次只比较同一运行。
WITH labels AS (
 SELECT DISTINCT run_id,review_id,aspect,polarity FROM mentions WHERE run_id=:run_id
)
SELECT b.game,b.duration_group,a.aspect,COUNT(DISTINCT b.review_id) AS success_reviews,
 COUNT(DISTINCT l.review_id) AS mentioned_reviews,
 COUNT(DISTINCT CASE WHEN l.polarity='negative' THEN b.review_id END) AS negative_reviews,
 COUNT(DISTINCT CASE WHEN l.polarity='positive' THEN b.review_id END) AS positive_reviews,
 COUNT(DISTINCT CASE WHEN l.polarity='mixed' THEN b.review_id END) AS mixed_reviews,
 ROUND(100.0*COUNT(DISTINCT CASE WHEN l.polarity='negative' THEN b.review_id END)
       /COUNT(DISTINCT b.review_id),2) AS negative_mention_pct
FROM analysis_review_base b CROSS JOIN aspects a
LEFT JOIN labels l ON l.run_id=b.run_id AND l.review_id=b.review_id AND l.aspect=a.aspect
WHERE b.run_id=:run_id AND b.extraction_status='success'
GROUP BY b.appid,b.duration_group,a.aspect ORDER BY b.appid,b.duration_group,a.aspect;
