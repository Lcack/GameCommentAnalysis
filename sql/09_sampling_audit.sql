WITH members AS (
 SELECT m.run_id,m.block_index,r.appid,e.status
 FROM sample_members m JOIN analysis_runs a ON a.id=m.run_id
 JOIN reviews r ON r.dataset_id=a.dataset_id AND r.review_id=m.review_id
 JOIN extraction_results e ON e.run_id=m.run_id AND e.review_id=m.review_id
 WHERE m.run_id=:run_id
)
SELECT g.name AS game,s.block_index,s.from_percent,s.to_percent,
 s.pool_count,s.target_count,s.selected_count,s.min_minutes,s.max_minutes,
 ROUND(s.inclusion_probability,6) AS inclusion_probability,
 COUNT(CASE WHEN m.status='success' THEN 1 END) AS success_reviews
FROM sampling_strata s JOIN games g USING(appid)
LEFT JOIN members m ON m.run_id=s.run_id AND m.block_index=s.block_index AND m.appid=s.appid
WHERE s.run_id=:run_id GROUP BY s.appid,s.block_index
ORDER BY s.appid,s.block_index;
