# 播客转写字幕分段 API（Podcast Subtitle Segmentation API）

接收播客转写后按时间排列的词序列，在硬约束下用**动态规划**求出总分最低的字幕分段方案，
同时返回分段 JSON 与 SRT 字幕文本。基于 Python 3.12 + FastAPI。

## 一条命令启动

```bash
docker compose up --build
```

服务监听并映射到宿主机 **`http://localhost:8000`**。

- 分段接口：`POST http://localhost:8000/segments`
- 健康检查：`GET http://localhost:8000/health`
- 交互式 API 文档（Swagger UI）：`http://localhost:8000/docs`

> 不想用 Docker 时也可：`pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000`

## 请求

`POST /segments`，`Content-Type: application/json`

```json
{
  "words": [
    {"start_ms": 0,    "end_ms": 1800, "text": "大家好。",       "speaker": "A"},
    {"start_ms": 1800, "end_ms": 6100, "text": "今天聊聊",       "speaker": "A"},
    {"start_ms": 6100, "end_ms": 7000, "text": "动态规划。",     "speaker": "A"},
    {"start_ms": 7000, "end_ms": 9100, "text": "我同意你的看法。", "speaker": "B"}
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `start_ms` | 非负整数 | 词起始时间（毫秒） |
| `end_ms` | 非负整数 | 词结束时间（毫秒，须不早于 `start_ms`） |
| `text` | 非空字符串 | 词文本；**段文本为词文本原样拼接，不插入空格** |
| `speaker` | 非空字符串 | 说话人标识 |

## 分段规则（硬约束）

每一段必须同时满足：

1. **换人必断**：段内所有词的 `speaker` 相同，说话人变化处强制断开；
2. **长度**：段文本不超过 **18 个 Unicode 字符**（按 code point 计数，`😀` 算 1 个）；
3. **时长**：首词 `start_ms` 到末词 `end_ms` 在 **1000～6000 毫秒**之间（含边界）；
4. 段至少包含一个词。

输入还必须满足：词按时间排列（`start_ms` 非倒序）、相邻词时间不重叠
（允许端点相接，即下一个词的 `start_ms` 等于上一个词的 `end_ms`），否则返回 **422**。

## 计分规则（动态规划最小化）

对**非末段**累计分数（末段不参与扣分）：

- 末词的文本**不以** `。`、`！`、`？` 结尾：**+2 分**；
- 段时长不足 **2000 毫秒**：**+1 分**。

在所有总分相同的方案中，取**断点序列字典序最小**者。断点序列只记录“词之间切开
的位置”（除第一段外每段首词的序号，不含固定的起点 0 与终点 n），按标准字典序
比较——因此同分时自然优先段数更少、首个断点更靠前的方案。例如断在词 8 与词 12
之后：断点序列 `(8,)` 小于 `(12,)`。

## 响应示例（200）

```json
{
  "segments": [
    {
      "index": 0,
      "start_word": 0,
      "end_word": 0,
      "start_ms": 0,
      "end_ms": 1800,
      "duration_ms": 1800,
      "speaker": "A",
      "text": "大家好。",
      "char_count": 4
    },
    {
      "index": 1,
      "start_word": 1,
      "end_word": 2,
      "start_ms": 1800,
      "end_ms": 7000,
      "duration_ms": 5200,
      "speaker": "A",
      "text": "今天聊聊动态规划。",
      "char_count": 9
    },
    {
      "index": 2,
      "start_word": 3,
      "end_word": 3,
      "start_ms": 7000,
      "end_ms": 9100,
      "duration_ms": 2100,
      "speaker": "B",
      "text": "我同意你的看法。",
      "char_count": 8
    }
  ],
  "total_score": 1,
  "srt": "1\n00:00:00,000 --> 00:00:01,800\n大家好。\n\n2\n00:00:01,800 --> 00:00:07,000\n今天聊聊动态规划。\n\n3\n00:00:07,000 --> 00:00:09,100\n我同意你的看法。\n"
}
```

> `start_word` / `end_word` 均为 **0-based、含端点**的词序号。
> 上例总分 1：第 1 段仅 1800ms（不足 2000ms）记 1 分，它以 `。` 结尾不记标点分；
> 第 2 段为非末段但以 `。` 结尾且时长达标，记 0 分；末段不记分。
> 词 0 不能与词 1 合段（0→6100ms 共 6100ms 超上限）；若词 1 单独成段，
> “今天聊聊”不以句末标点结尾会被记 2 分，故 DP 选择把词 1、2 合为一段（总分 1）。

### curl 示例

```bash
curl -s http://localhost:8000/segments \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":1800,"text":"大家好。","speaker":"A"},{"start_ms":1800,"end_ms":6100,"text":"今天聊动态规划。","speaker":"A"},{"start_ms":6100,"end_ms":8200,"text":"我同意。","speaker":"B"}]}'
```

## 错误响应（HTTP 422）

统一形如：`{"error": {"code": "...", "message": "...", ...}}`

| `code` | 触发条件 | 附加字段 |
| --- | --- | --- |
| `invalid_request` | 请求体结构/字段类型不合法（含空 `words`） | `errors` |
| `invalid_time_range` | 某个词 `end_ms < start_ms` | `word_index` |
| `time_out_of_order` | 相邻词 `start_ms` 倒序 | `word_index` |
| `overlapping_time_range` | 某词 `start_ms` 早于前词 `end_ms`（重叠） | `word_index` |
| `infeasible_segmentation` | 不存在满足全部硬约束的分段 | `last_word_index` |

### 无可行分段

当任何切分方式都无法满足硬约束时，返回 422，并在 `last_word_index` 中给出
**最早无法到达的前缀末词序号（0-based）**：即最小的满足“前 k 个词无法被任何合法
分段完整覆盖”的 k，其末词序号为 `k-1`。

```bash
curl -s http://localhost:8000/segments \
  -H 'Content-Type: application/json' \
  -d '{"words":[{"start_ms":0,"end_ms":800,"text":"太短了","speaker":"A"}]}'
```

```json
{
  "error": {
    "code": "infeasible_segmentation",
    "message": "不存在满足全部约束的分段方案",
    "last_word_index": 0
  }
}
```

（该词仅 800ms 且无法与前后词组合成 ≥1000ms 的段。）

## 算法说明

两趟动态规划 + 贪心重建，**O(n) 时间、O(n) 空间**：

1. **段可行性枚举**：每段至多 18 个词（每词至少 1 个字符），以任一词为段首时
   可行段末词至多 18 个，且利用字符数、时长随段长单调的性质提前剪枝；
2. **前向 DP**：`fwd[t]` = 恰好覆盖前 t 个词的最小总分；
3. **后向 DP**：`bwd[i]` = 覆盖后缀 `[i,n)` 的最小总分；
4. **贪心重建**：最优总分为 `fwd[n]`。从边界 0 出发，在每个当前边界枚举可行下一段：
   - 若该段直达末尾（成为末段）且总分为最优，则直接结束——它的断点序列是任何
     “继续切分”方案的严格前缀，标准字典序下最小；
   - 否则在所有能以最优总分补全后缀的候选中，取**最小的下一个断点**。
   每步贪心都在保持总分最优的前提下让断点序列的当前元素最小，归纳可得全局
   字典序最小；无任何候选则该前缀不可达。

> 早期版本曾把整条断点序列保存在 DP 状态里，会退化为 O(n²)；当前实现每状态
> 只存一个分数，字典序裁决全部推迟到重建阶段，10 万词约数秒、内存约 60MB。

## 测试

核心算法只依赖 Python 标准库，无需安装任何第三方包即可运行：

```bash
python3 -m unittest discover -s tests -v
```

在容器内（含 fastapi/httpx）会额外执行 HTTP 接口层测试：

```bash
docker compose run --rm api python -m unittest discover -s tests -v
```

## 项目结构

```
app/
  __init__.py
  main.py          # FastAPI 路由、异常处理（422 统一错误体）
  schemas.py       # 请求/响应 Pydantic 模型
  segmentation.py  # 输入校验 + 动态规划核心（纯标准库）
  srt.py           # SRT 渲染
  errors.py        # DomainError
tests/             # unittest：核心算法、暴力对拍、HTTP 接口层
  test_segmentation.py
  test_differential.py
  test_api.py
Dockerfile
docker-compose.yml
requirements.txt
```
