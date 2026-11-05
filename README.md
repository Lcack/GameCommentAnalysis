# GameCommentAnalysis

面向游戏数值策划的 Steam 评论分析项目，结合 **SQL、DeepSeek API 与人工评估**，从玩家评论中提取难度、战斗资源、构筑、成长和资源经济反馈，生成可追溯的分析结果。

默认研究对象为《影之刃零》《黑神话：悟空》《鬼武者 Way of the Sword》。项目关注玩家对具体机制的体验描述，支持优先抽取高游玩时长评论。

> 当前提供采集、抽样、模型调用、SQL 分析与评估流程。原始评论、数据库、密钥、机器生成结果和个人运行记录不随仓库发布。模型分类质量需通过人工标注评估，项目不预设任何游戏的分析结论。

## 功能

- **数据采集**：Steam 官方评论接口、日期与语言筛选、分页、去重、重试与游标续采。
- **时长倾斜抽样**：按发表评论时的游玩时长排序，按百分位分块，在高时长块分配更多名额。
- **结构化提取**：DeepSeek JSON 输出、多标签、方面级态度、原文证据与提示词版本追踪。
- **SQL 分析**：数据质量、时长分布、模型覆盖率、方面提及率、日期趋势、问题共现与抽样审计。
- **人工评估**：盲标样本导出、开发/测试样本排除、逐标签 precision / recall / F1 与错误案例。
- **本地交付**：中文 HTML 看板、CSV、分析报告模板与运行元数据。

## 数据流程

```text
Steam 评论 API
      ↓
原始 JSON → SQLite 数据快照
      ↓
时长筛选 → 百分位分块 → 固定种子的块内随机抽样
      ↓
DeepSeek API → JSON / 标签 / 原文证据校验
      ↓
SQL 指标与抽样审计 ← 人工标注评估
      ↓
HTML 看板 / CSV / 数值问题诊断报告
```

## 环境与安装

Python **3.11+**，第三方依赖为 `requests`；数据库使用 Python 的 `sqlite3` 接口。可以直接使用已有 conda 环境，无需单独部署数据库服务。

```bash
git clone git@github.com:Lcack/GameCommentAnalysis.git
cd GameCommentAnalysis
python -m pip install -r requirements.txt
python -m reviewlab init
```

Windows 可使用 `python -X utf8 -m reviewlab ...` 保持重定向输出的中文编码一致。PowerShell 辅助入口为 `./运行项目.ps1`，优先使用已激活 conda 环境，未激活时尝试 conda base。

## API 配置

将 `.env.example` 复制为项目根目录的 `.env`，仅在本地填写密钥：

```dotenv
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_THINKING=disabled
DEEPSEEK_MAX_TOKENS=2048
DEEPSEEK_TIMEOUT=90
```

密钥在 [DeepSeek 开放平台](https://platform.deepseek.com/) 创建。API 地址、模型名与 JSON 模式配置依据 [DeepSeek 官方文档](https://api-docs.deepseek.com/zh-cn/)和 [JSON Output 文档](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)，模型可在配置中替换。

```bash
python -m reviewlab doctor
```

`doctor` 只检查本地配置，显示密钥是否已设置，不输出密钥、不联网，也不验证余额与账户有效性。系统环境变量优先于 `.env`。

## 快速开始

以下命令中的 `<dataset_id>`、`<run_id>` 均替换为前一步实际输出。

### 1. 采集评论

```bash
# 三款游戏，最近30个完整的北京时间自然日
python -m reviewlab collect --game all --days 30

# 或先采每款游戏一页验证流程
python -m reviewlab collect --game all --days 30 --max-pages 1 --page-size 20

# 沿用原始窗口与游标继续采集
python -m reviewlab collect --dataset <dataset_id>
```

`--game` 支持 `all`、`phantom`、`wukong`、`onimusha` 或对应 App ID。也可通过 `--start YYYY-MM-DD --end YYYY-MM-DD` 指定窗口，开始包含、结束不包含。

达到分页上限时状态为 `partial`；遍历到接口空页时为 `complete`；异常为 `failed`。有界采集不能冒充完整时间窗口，评论总量不足时不会自动扩大日期范围。

### 2. 配置时长抽样

默认配置位于 [`config/sampling.json`](config/sampling.json)：

```json
{
  "mode": "longplay",
  "per_game": 5000,
  "top_percent": 100,
  "min_hours": 0,
  "min_chars": 0,
  "weights": [50, 25, 15, 7, 3],
  "seed": 42
}
```

先过滤未知/负时长、空文本及超过20,000字符的文本，再按评论时游玩时长从高到低排名。每款游戏独立分为五个近似等人数块：

| 降序时长百分位 | 样本名额比例 | 每游戏5,000条时的理想配额 |
|---|---:|---:|
| 前0—20%，最高时长 | 50% | 2,500 |
| 20—40% | 25% | 1,250 |
| 40—60% | 15% | 750 |
| 60—80% | 7% | 350 |
| 80—100%，最低时长 | 3% | 150 |

**百分位按评论数量划分，不是把最大游玩时长分成五段。**块内无放回随机抽样；同时长用种子与评论 ID 的哈希打破平局。每次运行保存样本 ID、抽样配置、实际配额及块内入选概率。

样本不足时按实际可用总量计算配额；某块容量不够时先封顶，再按权重把剩余名额分给有余量的块。实际比例会明确记录，不重复抽取、不伪造条目。要满足2,500条最高时长块的理想配额，五块母体约需12,500条符合条件的评论；小母体抽取全部评论时无法保持倾斜。

```bash
# 预览默认抽样，不调用模型、不创建分析运行
python -m reviewlab sample-preview --dataset <dataset_id>

# 可选：只研究时长排名前50%，且发表评论时至少游玩10小时的评论
python -m reviewlab sample-preview --dataset <dataset_id> --top-percent 50 --min-hours 10

# 可选：覆盖默认配置，使用其他分块比例
python -m reviewlab sample-preview --dataset <dataset_id> --weights 70,20,7,2,1
```

`--top-percent 50` 会先保留筛选母体中最高时长的前50%，然后把这部分再分为五块。`--min-chars` 可设文本长度下限，但长度不保证内容质量。预览会生成百分位分块CSV与抽样JSON；超长文本仍保留在原始数据中，等待另行核查。

### 3. 固定样本与提示词

```bash
python -m reviewlab prepare --dataset <dataset_id>
```

输出 `run_id`。可覆盖 `--per-game 5000 --seed 42 --sampling longplay --top-percent 100 --weights 50,25,15,7,3`。`--sampling uniform` 提供同一筛选母体内的均匀随机抽样，便于对照。

完整分析默认要求 `complete` 数据集。仅做流程验证时可显式加入 `--allow-partial --per-game 10`。准备运行会固定模型、提示词原文/哈希、生成设置与抽样清单，修改配置后需新建 run。

### 4. 预览请求与小批调用

```bash
# 不联网、不需要密钥
python -m reviewlab analyze --run <run_id> --dry-run

# 显式调用DeepSeek，先处理10条
python -m reviewlab analyze --run <run_id> --limit 10

# 后续批次，成功项不会再次调用
python -m reviewlab analyze --run <run_id> --limit 100

# 修正问题后显式重试失败项
python -m reviewlab analyze --run <run_id> --limit 10 --retry-failed
```

`--limit` 是评论数上限，不是金额上限；每条最多2次HTTP尝试。超时重试可能发生服务端重复处理或计费，未收到响应时用量未知。收到的响应独立留档；格式或证据校验失败不会自动反复调用模型。API返回的实际用量见原始响应的 `usage`。

### 5. 人工标注与评估

```bash
python -m reviewlab export-labels --run <run_id> --split development --per-game 30 --output data/annotations/development.csv
python -m reviewlab export-labels --run <run_id> --split test --per-game 60 --exclude data/annotations/development.csv --output data/annotations/test.csv
```

人工填写 `gold_labels_json`，留空表示未标注，`[]` 表示确认无标签。例如：

```json
[{"aspect":"战斗资源","polarity":"negative"}]
```

```bash
python -m reviewlab evaluate --run <run_id> --labels data/annotations/test.csv
```

只评估非空人工 test 标签与成功模型结果的交集，失败覆盖单列。指标单位为评论级“方面+态度”集合，不覆盖具体问题、对象、进度的全部正确性。测试集不能反复用于提示词调优。

### 6. 生成结果

```bash
python -m reviewlab report --dataset <dataset_id> --run <run_id>
python -m reviewlab status
```

看板、CSV和报告位于 `reports/<dataset_id>/<run_id>/`。省略 `--run` 可只查看采集质量与基础时长分布。

## 提取标签

| 方面 | 关注内容 |
|---|---|
| 难度与容错 | 难度高低、失败惩罚、容错与难度突变 |
| 战斗资源 | 精力、法力、恢复与技能消耗 |
| 构筑与强弱 | 武器、技能、装备的强弱与选择空间 |
| 成长节奏 | 升级、强化速度与成长感受 |
| 资源经济 | 获取、消耗、掉落与重复刷取 |
| 其他体验 | 性能、镜头、操作、剧情等具体体验 |

方面态度支持 `positive`、`negative`、`mixed`、`neutral`。每条反馈保留原文连续证据；对象与进度仅从原文明示内容提取。详见[标注规则](docs/标签与标注规则.md)。

## SQL 与数据模型

`sql/schema.sql` 定义游戏、数据集、采集任务、评论、分析运行、处理结果、具体反馈、抽样分层与入选清单。九份查询分别处理：

1. 数据质量与覆盖窗口。
2. 时长与推荐状态分布。
3. 模型处理成功、失败、待处理与跳过覆盖。
4. 各方面的评论级去重提及率。
5. 日期趋势与上一有样本日期的百分点变化。
6. 负面方面共现与游戏内排名。
7. 可追溯原文证据。
8. 时长排名、有效母体及确定性平局处理。
9. 百分位分块、配额与实际成功处理数量审计。

完整字段和分母定义见[数据字典](docs/数据字典与指标口径.md)。

## 项目结构

```text
reviewlab/       采集、抽样、模型调用、SQL报告、评估与CLI
sql/             数据库结构、视图与分析查询
config/          游戏清单及抽样默认配置
prompts/         中文结构化提取提示词
docs/            数据字典、标注规则
tests/           隔离的离线测试
.github/         GitHub Actions测试配置
.env.example     API配置示例
运行项目.ps1     PowerShell辅助入口
data/            本地数据与响应，不提交
reports/         本地报告与评估，不提交
```

## 测试

```bash
python -m unittest discover -s tests -v
```

覆盖采集续跑与去重、北京时间窗口、HTTP重试、证据校验、失败分母、空输出、盲标集排除、时长排名、百分位截取、配额容量、随机复现、跨游戏抽样审计与HTML转义。测试不调用真实付费模型。

## 分析边界

- 当前提及率是**入选样本内的未加权比例**。高时长倾斜与顶部截取后的结果不能直接作为全部评论或全体玩家的比例；入选概率被记录，但尚未实现总体加权估计。
- 游玩时长是发表评论时的分钟数，不能直接代表熟练度、通关进度、留存或流失。
- 失败或空响应不当作“未提及”；有效空标签数组才计为成功处理。
- 多标签比例不可相加，`mixed` 与 `negative` 分开统计。
- 评论文本为采集时可见版本；编辑后的文字可能不对应首次发表评论时的体验。
- 发行阶段、语言和评论选择偏差会影响跨游戏比较；评论不直接证明机制因果或具体参数调整幅度。
- 原文证据存在性检查不能代替语义准确率评估。

## 官方资料

- [Valve：Steam 用户评论接口](https://partner.steamgames.com/doc/webapi/IUserReviewsService)
- [DeepSeek：首次调用 API](https://api-docs.deepseek.com/zh-cn/)
- [DeepSeek：JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)
- [DeepSeek：Token 用量计算](https://api-docs.deepseek.com/zh-cn/quick_start/token_usage/)
