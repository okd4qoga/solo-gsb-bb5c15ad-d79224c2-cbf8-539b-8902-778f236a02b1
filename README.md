# 播客字幕分段 API（Podcast Subtitle Segmentation API）

把播客转写后得到的、按时间排列的**词序列**切分为适合做字幕的**段序列**。
使用 Python + FastAPI，核心分段由**动态规划**在所有可行分段中求总分最小解，并返回 JSON 与 SRT 两种结果。

- 框架：FastAPI + Pydantic v2
- 算法：O(n·L) 动态规划（L 为单段最大词数，受时长/字数上限约束）
- 端口：**8000**

## 一条命令安装依赖并启动

### 方式一：Docker Compose（推荐，无需本机 Python 环境）

```bash
docker compose up --build
```

服务启动后监听 **http://localhost:8000**。

### 方式二：本机 Python 3.11+（单条命令完成安装 + 启动）

```bash
pip install -r requirements.txt && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 访问地址与交互式文档

| 用途 | 地址 |
| --- | --- |
| 健康检查 | http://localhost:8000/health |
| 分段接口 | `POST http://localhost:8000/segments` |
| Swagger UI（可直接发请求） | http://localhost:8000/docs |
| OpenAPI 描述 | http://localhost:8000/openapi.json |

## 请求示例

```bash
curl -s -X POST http://localhost:8000/segments \
  -H 'Content-Type: application/json' \
  -d '{
    "words": [
      {"start_ms": 0,    "end_ms": 1200, "text": "大家好，",   "speaker": "A"},
      {"start_ms": 1200, "end_ms": 3000, "text": "欢迎收听。", "speaker": "A"},
      {"start_ms": 3000, "end_ms": 4200, "text": "今天",       "speaker": "A"},
      {"start_ms": 4200, "end_ms": 5600, "text": "聊聊字幕。", "speaker": "A"},
      {"start_ms": 5600, "end_ms": 7000, "text": "我也来补充", "speaker": "B"}
    ]
  }'
```

### 请求体字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `words` | 数组（至少 1 个） | 按时间升序排列的词 |
| `words[].start_ms` | 非负整数 | 词开始时间（毫秒） |
| `words[].end_ms` | 非负整数 | 词结束时间（毫秒），`end_ms >= start_ms` |
| `words[].text` | 非空字符串 | 词文本 |
| `words[].speaker` | 非空字符串 | 说话人标识 |

### 成功响应（200）

```json
{
  "segments": [
    {
      "index": 1,
      "start_ms": 0,
      "end_ms": 3000,
      "duration_ms": 3000,
      "text": "大家好，欢迎收听。",
      "speaker": "A",
      "char_count": 9,
      "start_word_index": 1,
      "end_word_index": 2,
      "cost": 0
    },
    {
      "index": 2,
      "start_ms": 3000,
      "end_ms": 5600,
      "duration_ms": 2600,
      "text": "今天聊聊字幕。",
      "speaker": "A",
      "char_count": 7,
      "start_word_index": 3,
      "end_word_index": 4,
      "cost": 0
    },
    {
      "index": 3,
      "start_ms": 5600,
      "end_ms": 7000,
      "duration_ms": 1400,
      "text": "我也来补充",
      "speaker": "B",
      "char_count": 5,
      "start_word_index": 5,
      "end_word_index": 5,
      "cost": 1
    }
  ],
  "srt": "1\n00:00:00,000 --> 00:00:03,000\n[A] 大家好，欢迎收听。\n\n2\n00:00:03,000 --> 00:00:05,600\n[A] 今天聊聊字幕。\n\n3\n00:00:05,600 --> 00:00:07,000\n[B] 我也来补充\n",
  "total_score": 1
}
```

SRT 内容展开为：

```srt
1
00:00:00,000 --> 00:00:03,000
[A] 大家好，欢迎收听。

2
00:00:03,000 --> 00:00:05,600
[A] 今天聊聊字幕。

3
00:00:05,600 --> 00:00:07,000
[B] 我也来补充
```

## 分段规则

### 硬约束（必须全部满足，段是连续词的划分）

1. **时间合法性**：词按开始时间升序排列；后一个词的 `start_ms` 不得早于前一个词
   （倒序），也不得早于前一个词的 `end_ms`（重叠）。首尾相接（相等）允许。
   违反返回 `422`，错误码为 `time_out_of_order` / `time_overlap`，并给出 `word_index`。
2. **换人必须断段**：一段只能包含同一个 `speaker`；相邻词说话人不同处强制为断点。
3. **字数上限**：段内各词文本**直接拼接**（不额外插入空格或标点），长度按
   **Unicode 码点**计数（Python `len`），每段最多 **18** 个字符。
4. **时长窗口**：段时长 = 末词 `end_ms` − 首词 `start_ms`，必须在 **1000～6000 毫秒**
   （含边界）之间。

### 目标函数（动态规划最小化）

对每个**非末段**（最后一段豁免这一条）：

- 末词文本不以句末标点 `。` `！` `？` `!` `?` 结尾：**+2 分**；

对所有段（含末段）：

- 段时长不足 **2000 毫秒**（严格小于）：**+1 分**。

总分最小者胜出；**总分相同时，选择断点序列字典序最小的方案**（断点序列为各段
末词的 1 起始序号，如 `(1, 3)` 与 `(2, 3)` 同分则取 `(1, 3)`）。

### 无可行分段（422）

当词序列本身合法、但硬约束导致不存在任何可行划分时，返回 `422`：

```json
{
  "detail": {
    "code": "no_feasible_segmentation",
    "message": "no feasible segmentation exists under the length / duration / speaker constraints",
    "earliest_unreachable": 2
  }
}
```

`earliest_unreachable` 是**最早无法到达的前缀末词序号（1 起始）**：即动态规划中
第一个不存在任何可行分段方案的前缀的末词序号。例如第 2 个词自身时长超过 6000ms
且无法与任何左侧词组成合法段时，该值为 `2`。

### 其他 422

请求体结构错误（字段缺失、空词表、负数时间等）返回：

```json
{ "detail": { "code": "invalid_request", "errors": [ ... Pydantic 校验明细 ... ] } }
```

## 算法说明

`dp[k] = (最小总分, 断点元组)` 表示覆盖前 k 个词的最优方案；`dp[0] = (0, ())`。

对每个 k，倒序枚举段起点 i 构造段 `[i, k]`，段内说话人不一致、时长超过 6000ms
或字数超过 18 时立即剪枝（i 再减小只会更差），时长不足 1000ms 时跳过。
以 `(cost, breaks)` 元组比较做状态转移：先比总分，再比断点元组字典序，
因此同分时自然选出字典序最小的断点序列。

## 本地开发与测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

测试覆盖：时长/字数边界、说话人强制断段、标点与短段打分、同分字典序、
最早不可到达前缀、时间倒序/重叠校验以及 HTTP 层行为（共 26 个用例）。

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 路由与请求/响应模型
│   └── segmenter.py     # 校验、动态规划分段、SRT 渲染（纯函数）
├── tests/
│   ├── test_api.py
│   └── test_segmenter.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
└── README.md
```
