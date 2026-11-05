SELECT g.name AS game, j.status AS collection_status, j.page_count,
 COUNT(r.review_id) AS collected_reviews,
 COALESCE(SUM(TRIM(r.review_text)=''),0) AS empty_texts,
 COALESCE(SUM(r.playtime_minutes IS NULL OR r.playtime_minutes<0),0) AS missing_playtime,
 COALESCE(SUM(r.timestamp_updated>r.timestamp_created),0) AS edited_reviews,
 MIN(datetime(r.timestamp_created,'unixepoch','+8 hours')) AS first_created_cst,
 MAX(datetime(r.timestamp_created,'unixepoch','+8 hours')) AS last_created_cst
FROM collection_jobs j JOIN games g USING(appid)
LEFT JOIN reviews r ON r.dataset_id=j.dataset_id AND r.appid=j.appid
WHERE j.dataset_id=:dataset_id
GROUP BY j.appid;
