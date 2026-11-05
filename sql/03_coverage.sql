SELECT game,duration_group,COUNT(*) AS selected_reviews,
 SUM(extraction_status='success') AS success_reviews,
 SUM(extraction_status='pending') AS pending_reviews,
 SUM(extraction_status='failed') AS failed_reviews,
 SUM(extraction_status='skipped') AS skipped_reviews,
 ROUND(100.0*SUM(extraction_status='success')/COUNT(*),2) AS success_pct
FROM analysis_review_base WHERE run_id=:run_id
GROUP BY appid,duration_group ORDER BY appid,duration_group;
