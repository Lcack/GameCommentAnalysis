from __future__ import annotations

import html
import json
from datetime import datetime

from .core import CST, ROOT, dump_json, now, require_row, rows, write_csv

LABELS = {
    "game":"游戏", "collection_status":"采集状态", "page_count":"页数",
    "collected_reviews":"采集评论数", "empty_texts":"空文本", "missing_playtime":"时长缺失",
    "edited_reviews":"已编辑评论", "first_created_cst":"最早发表时间", "last_created_cst":"最晚发表时间",
    "duration_group":"评论时游玩时长", "reviews":"评论数", "not_recommended_reviews":"不推荐数",
    "not_recommended_pct":"不推荐比例 %", "selected_reviews":"入选评论数", "success_reviews":"成功处理数",
    "pending_reviews":"待处理数", "failed_reviews":"失败数", "skipped_reviews":"跳过数", "success_pct":"成功率 %",
    "aspect":"方面", "mentioned_reviews":"提及评论数", "negative_reviews":"负面提及数",
    "positive_reviews":"正面提及数", "mixed_reviews":"混合态度数", "negative_mention_pct":"负面提及率 %",
    "appid":"游戏ID", "created_day":"发表日期", "any_negative_reviews":"含任一负面反馈数",
    "any_negative_pct":"含负面反馈比例 %", "previous_observed_day":"上一有样本日期",
    "change_vs_previous_observed_day_pp":"较上一有样本日变化（百分点）",
    "aspect_a":"负面方面 A", "aspect_b":"负面方面 B", "cooccurring_reviews":"共现评论数",
    "pair_rank":"游戏内排名", "review_id":"评论ID", "voted_up":"整体推荐",
    "issue":"具体反馈", "polarity":"方面态度", "target":"对象", "progress":"明确进度", "evidence":"原文证据",
    "block_index":"时长块（1为最高）", "from_percent":"降序起始百分位", "to_percent":"降序结束百分位",
    "pool_count":"块内可用数", "target_count":"原目标名额", "selected_count":"实际抽取数",
    "min_minutes":"块内最低分钟数", "max_minutes":"块内最高分钟数", "inclusion_probability":"块内入选概率",
}


def table(records, limit=120):
    if not records:
        return '<p class="empty">没有可展示的数据；不等于该问题不存在。</p>'
    fields = list(records[0])
    head = "".join(f"<th>{html.escape(LABELS.get(k,k))}</th>" for k in fields)
    body = []
    for r in records[:limit]:
        cells = []
        for k in fields:
            value = "—" if r[k] is None else str(r[k])
            cells.append(f"<td>{html.escape(value)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    notice = f"<p>页面显示前 {min(len(records),limit)} / {len(records)} 行，完整结果见对应 CSV。</p>"
    return notice + '<div class="scroll"><table><thead><tr>' + head + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"


def duration_bars(records):
    groups = {}
    for r in records:
        groups.setdefault(r["game"], []).append(r)
    blocks = []
    for game, group in groups.items():
        total = sum(r["reviews"] for r in group)
        bars = []
        for r in group:
            pct = 100*r["reviews"]/total if total else 0
            bars.append(f'<div class="barrow"><span>{html.escape(r["duration_group"])}</span>'
                        f'<div class="track"><div class="bar" style="width:{pct:.3f}%"></div></div>'
                        f'<span>{r["reviews"]} 条 · {pct:.1f}%</span></div>')
        blocks.append(f'<div class="chart"><h3>{html.escape(game)} · {total} 条</h3>{"".join(bars)}</div>')
    return '<section><h2>采集评论的时长分布</h2><p>比例分母为各游戏已采集评论数；不是全部玩家的时长分布。</p>' + "".join(blocks) + "</section>"


def report(db, args):
    dataset = require_row(db, "datasets", args.dataset)
    run = require_row(db, "analysis_runs", args.run) if args.run else None
    if run and run["dataset_id"] != args.dataset:
        raise ValueError("run 不属于指定 dataset")
    out = ROOT / "reports" / args.dataset / (args.run or "overview")
    out.mkdir(parents=True, exist_ok=True)
    params = {"dataset_id": args.dataset, "run_id": args.run}
    specs = [("01_quality.sql", "采集质量"), ("02_duration.sql", "时长与推荐分布")]
    if run:
        specs += [("09_sampling_audit.sql", "时长百分位与实际抽样配额"),
                  ("03_coverage.sql", "模型处理覆盖"), ("04_aspect_rates.sql", "样本内各方面负面提及率"),
                  ("05_daily_trend.sql", "按发表日期的样本趋势"), ("06_cooccurrence.sql", "负面方面共现"),
                  ("07_evidence.sql", "可追溯原文证据")]
    results, sections = {}, []
    for file, title in specs:
        records = rows(db, (ROOT / "sql" / file).read_text(encoding="utf-8"), params)
        results[file] = records
        write_csv(out / file.replace(".sql", ".csv"), records)
        sections.append(f'<section><h2>{title}</h2><a href="{file.replace(".sql", ".csv")}">下载完整 CSV</a>{table(records)}</section>')
    start = datetime.fromtimestamp(dataset["start_ts"], CST).isoformat()
    end = datetime.fromtimestamp(dataset["end_ts"], CST).isoformat()
    model_status = f"模型：{run['model']}；提示词：{run['prompt_version']}；分析运行：{run['id']}" if run else "尚未执行大模型分析；此页面只展示采集与基础 SQL 结果。"
    if run and not any(r["success_reviews"] for r in results["03_coverage.sql"]):
        model_status += "。尚无成功的模型结果，以下不展示任何标签结论。"
    warning = "采集尚不完整：结果仅用于流程验证，不能代表整个时间窗口。" if dataset["status"] != "complete" else "已遍历到接口空页；这不代表覆盖全体玩家。"
    notes = [warning, "统计范围为所选 Steam 简体中文评论，不代表全体玩家。",
             "按评论时游玩时长分组，不能将时长组解释为技术水平、游戏进度或留存。",
             "负面提及率分母只包含成功处理的入选评论；需结合失败/跳过比例判断。",
             "多标签比例不可直接相加；mixed 与 negative 分开统计。",
             "样本按游戏分别抽取，三款游戏合并时需要考虑不同抽样比例。",
             "评论按创建时间筛选，文本为采集时可见版本，无法恢复历史原文；修改过的评论需单独核查。",
             "发行阶段、语言群体与评论选择偏差可能影响比较，不能从评论直接推断机制因果或调整参数。",
             "标签尚需人工评估；非空证据校验不能证明分类或态度判断正确。"]
    sampling = json.loads(run["settings_json"]).get("sampling") if run else None
    if sampling:
        sampling_note = (f"抽样方式：{sampling['mode']}；仅保留筛选母体按时长降序前{sampling['top_percent']}%；"
                         f"最低游玩{sampling['min_hours']}小时；五块目标权重{sampling['weights']}。")
        notes.insert(1, sampling_note)
        notes.insert(2, "当前提及率为未加权的入选样本比例。高时长倾斜或顶部截取后的结果不能直接作为完整窗口总体比例；入选概率仅作抽样审计，不自动修正失败或未抽到的块。")
    metadata = {"generated_at": now(), "dataset": dict(dataset), "run": dict(run) if run else None, "limitations": notes}
    dump_json(out / "metadata.json", metadata)
    count = sum(r["collected_reviews"] for r in results["01_quality.sql"])
    md = f"# Steam 游戏评论分析快照\n\n生成时间：{metadata['generated_at']}\n\n数据集：{args.dataset}\n\n窗口（左闭右开，北京时间）：{start} 至 {end}\n\n采集状态：{dataset['status']}；已保存评论：{count}\n\n{model_status}\n\n## 边界\n\n" + "\n".join("- " + n for n in notes)
    md += "\n\n## 数值策划诊断（待人工撰写）\n\n- 问题与原文证据：待核查。\n- SQL 分组结果与分母：见 CSV。\n- 候选机制解释（逻辑推断）：待填写，不将相关性写作因果。\n- 其他解释与反例：待填写。\n- 需补充的机制、参数或实测数据：待填写。\n- 调整假设及验证方式：待填写，不从评论直接生成调整幅度。\n\n分类与置信度：表中数量属于计算得出，高（可复算，不等于样本具有代表性）；模型标签属于待人工评估的机器提取结果，其准确率未知；候选机制解释暂未填写。\n"
    (out / "分析报告.md").write_text(md, encoding="utf-8")
    note_html = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Steam 游戏数值反馈分析</title><style>
:root{{color-scheme:light}}body{{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f1f5f9;color:#152437;line-height:1.7}}
main{{max-width:1440px;margin:auto;padding:28px}}header,section,aside{{background:white;padding:24px;margin-bottom:20px;border-radius:12px;border:1px solid #dbe3ed}}
h1{{margin:0 0 10px;font-size:28px}}h2{{font-size:20px}}.status{{background:#fff5d6;border-left:4px solid #c79217;padding:12px}}
.scroll{{overflow:auto;max-height:620px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #dde5ed;padding:10px;text-align:left;vertical-align:top;min-width:85px;max-width:420px;overflow-wrap:anywhere}}
th{{position:sticky;top:0;background:#eaf0f7;white-space:nowrap}}a{{color:#1359a0}}.empty{{color:#52657a}}footer{{color:#52657a;font-size:13px}}</style>
<style>.chart{{margin-bottom:24px}}.barrow{{display:grid;grid-template-columns:150px minmax(80px,1fr) 140px;gap:16px;align-items:center;margin:8px 0;font-size:14px}}.track{{background:#edf2f7;height:15px;border-radius:4px}}.bar{{background:#287d96;height:100%;border-radius:4px}}@media(max-width:650px){{.barrow{{grid-template-columns:110px minmax(50px,1fr) 90px;gap:6px;font-size:12px}}main{{padding:12px}}}}</style>
<main><header><h1>Steam 游戏数值反馈分析</h1><p>数据集：{html.escape(args.dataset)}<br>北京时间窗口：[ {start}，{end} )<br>已保存评论：{count}</p>
<p>{html.escape(model_status)}</p><p class="status">{html.escape(warning)}</p></header>
{duration_bars(results['02_duration.sql'])}{''.join(sections)}<aside><h2>指标口径与解释边界</h2><ul>{note_html}</ul></aside>
<footer>数量：计算得出，可复算；模型标签准确率未知，需人工评估。原文为外部数据，未执行其中的任何指令。</footer></main></html>'''
    (out / "看板.html").write_text(page, encoding="utf-8")
    print(f"报告已生成：{out / '看板.html'}")
    return out
