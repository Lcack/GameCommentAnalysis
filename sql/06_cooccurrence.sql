WITH labels AS (
 SELECT DISTINCT run_id,review_id,aspect FROM mentions
 WHERE run_id=:run_id AND polarity='negative'
), pairs AS (
 SELECT b.appid,b.game,l.aspect AS aspect_a,r.aspect AS aspect_b,
 COUNT(DISTINCT b.review_id) AS cooccurring_reviews
 FROM labels l JOIN labels r ON l.run_id=r.run_id AND l.review_id=r.review_id AND l.aspect<r.aspect
 JOIN analysis_review_base b ON b.run_id=l.run_id AND b.review_id=l.review_id
 WHERE b.extraction_status='success' GROUP BY b.appid,l.aspect,r.aspect
)
SELECT *,DENSE_RANK() OVER(PARTITION BY appid ORDER BY cooccurring_reviews DESC) AS pair_rank
FROM pairs ORDER BY appid,pair_rank,aspect_a,aspect_b;
