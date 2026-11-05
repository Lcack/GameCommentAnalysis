SELECT b.game,b.review_id,b.created_day,b.duration_group,b.voted_up,
 m.aspect,m.issue,m.polarity,m.target,m.progress,m.evidence
FROM mentions m JOIN analysis_review_base b ON b.run_id=m.run_id AND b.review_id=m.review_id
WHERE m.run_id=:run_id AND b.extraction_status='success'
ORDER BY b.appid,b.review_id,m.mention_index;
