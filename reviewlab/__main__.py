from __future__ import annotations

import argparse
import json
import os
import sys

from .analyze import analyze, prepare
from .collect import collect
from .core import ROOT, connect, load_env, rows
from .evaluate import evaluate, export_labels
from .report import report
from .sampling import preview


def sampling_arguments(p):
    p.add_argument("--dataset", required=True)
    p.add_argument("--per-game", type=int, help="每游戏目标样本量，默认读取config/sampling.json（5000）")
    p.add_argument("--seed", type=int)
    p.add_argument("--sampling", choices=["longplay", "uniform"], help="默认longplay：高时长分块倾斜")
    p.add_argument("--weights", help="五块名额比例，最高时长在前，例如50,25,15,7,3")
    p.add_argument("--top-percent", type=float, help="只保留按时长降序的前百分之几，默认100")
    p.add_argument("--min-hours", type=float, help="评论时游玩时长下限，默认0")
    p.add_argument("--min-chars", type=int, help="原文长度下限，默认0；长度不是质量证明")


def parser():
    p = argparse.ArgumentParser(description="Steam 游戏数值反馈分析：采集 / DeepSeek / SQL / 评估")
    p.add_argument("--db", help="指定数据库路径，默认 data/reviews.sqlite3")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="初始化本地数据库，不联网")
    commands.add_parser("status", help="查看数据集和模型运行状态")
    commands.add_parser("doctor", help="只检查本地配置与密钥是否已填写，不打印密钥、不联网")
    c = commands.add_parser("collect", help="采集 Steam 评论，默认最近30个完整北京时间自然日")
    c.add_argument("--game", default="all")
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--start", help="北京时间起始日期 YYYY-MM-DD，包含")
    c.add_argument("--end", help="北京时间结束日期 YYYY-MM-DD，不包含")
    c.add_argument("--page-size", type=int, default=100)
    c.add_argument("--max-pages", type=int, default=0, help="每游戏本次最大页数，0为不限；达到上限标记partial")
    c.add_argument("--pause", type=float, default=1.0)
    c.add_argument("--dataset", help="按原窗口/筛选条件/游标继续已有数据集")
    a = commands.add_parser("prepare", help="固定随机样本和模型配置，不调用API")
    sampling_arguments(a)
    a.add_argument("--allow-partial", action="store_true")
    a.add_argument("--prompt", help="自定义提示词文件路径；内容与哈希会固定到run")
    sampling_arguments(commands.add_parser("sample-preview", help="查看时长百分位、配额和样本不足情况，不调用API"))
    a = commands.add_parser("analyze", help="显式调用DeepSeek，默认最多处理10条；支持续跑")
    a.add_argument("--run", required=True)
    a.add_argument("--limit", type=int, default=10)
    a.add_argument("--dry-run", action="store_true", help="只保存请求预览，不需要密钥、不联网")
    a.add_argument("--retry-failed", action="store_true")
    r = commands.add_parser("report", help="执行SQL并生成本地中文HTML和CSV")
    r.add_argument("--dataset", required=True)
    r.add_argument("--run", help="省略时仅生成真实基础统计，不生成虚构标签")
    e = commands.add_parser("export-labels", help="导出不含模型答案的人工盲标CSV，不覆盖已有文件")
    e.add_argument("--run", required=True)
    e.add_argument("--per-game", type=int, default=60)
    e.add_argument("--seed", type=int, default=2026)
    e.add_argument("--split", choices=["development", "test"], default="test")
    e.add_argument("--output")
    e.add_argument("--exclude", help="排除已有标注CSV中的评论，例如准备test时排除development")
    e = commands.add_parser("evaluate", help="评估人工test标签与模型标签，输出逐标签指标和错误案例")
    e.add_argument("--run", required=True)
    e.add_argument("--labels", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with connect(args.db) as db:
            if args.command == "init":
                print("数据库已初始化；未联网、未调用大模型。")
            elif args.command == "doctor":
                load_env()
                print(json.dumps({"python": sys.executable, "env_file_exists": (ROOT / ".env").exists(),
                    "api_key_configured": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()),
                    "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                    "model_for_new_runs": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
                    "note": "仅本地检查，不验证账户有效性；环境变量优先于.env，已有run保留原配置"}, ensure_ascii=False, indent=2))
            elif args.command == "status":
                print(json.dumps({"datasets": rows(db, "SELECT * FROM datasets ORDER BY created_at DESC"),
                    "runs": rows(db, """SELECT a.id,a.dataset_id,a.model,e.status,COUNT(e.review_id) AS reviews
                    FROM analysis_runs a LEFT JOIN extraction_results e ON e.run_id=a.id GROUP BY a.id,e.status
                    ORDER BY a.created_at DESC""")}, ensure_ascii=False, indent=2))
            else:
                {"collect": collect, "prepare": prepare, "analyze": analyze,
                 "report": report, "export-labels": export_labels, "evaluate": evaluate,
                 "sample-preview": preview}[args.command](db, args)
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
