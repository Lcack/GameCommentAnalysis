SELECT game,duration_group,COUNT(*) AS reviews,
 SUM(voted_up=0) AS not_recommended_reviews,
 ROUND(100.0*SUM(voted_up=0)/NULLIF(COUNT(voted_up),0),2) AS not_recommended_pct
FROM reviews_enriched WHERE dataset_id=:dataset_id
GROUP BY appid,duration_group ORDER BY appid,duration_group;
