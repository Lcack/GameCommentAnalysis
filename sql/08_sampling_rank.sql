-- 排名母体：当前数据集内已知时长、非空且满足筛选条件的评论。
-- 同时长用种子+评论ID的哈希打破平局，避免按时间或ID固定切分。
SELECT r.*,g.name AS game,
 ROW_NUMBER() OVER(PARTITION BY r.appid ORDER BY r.playtime_minutes DESC,
                  sample_tie_key(r.review_id,:seed),r.review_id) AS rank_position,
 COUNT(*) OVER(PARTITION BY r.appid) AS population_size
FROM reviews r JOIN games g USING(appid)
WHERE r.dataset_id=:dataset_id AND r.playtime_minutes>=:min_minutes
 AND LENGTH(TRIM(r.review_text))>0
 AND LENGTH(r.review_text)>=:min_chars AND LENGTH(r.review_text)<=:max_chars
ORDER BY r.appid,rank_position;
