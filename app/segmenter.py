"""字幕分段核心算法。

输入是一组按时间排列的词（word），输出是满足播客字幕约束的分段结果。

分段硬约束：
1. 相邻词的开始时间不允许倒序，也不允许时间重叠（首尾相接可以）。
2. 说话人发生变化时必须断开（同一段只能包含同一个说话人）。
3. 每段拼接后的文本长度不超过 ``MAX_CHARS`` 个 Unicode 字符（码点）。
4. 每段从首词开始到末词结束的时长在 ``MIN_DURATION_MS`` ~ ``MAX_DURATION_MS`` 之间。

目标（动态规划最小化总分）：
* 末词文本不以句末标点（。！？!?）结尾的断段记 2 分（最后一段豁免）。
* 时长不足 ``SHORT_DURATION_MS`` 的段记 1 分。
* 总分相同时选择“断点序列”字典序最小的方案。
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_CHARS = 18
MIN_DURATION_MS = 1000
MAX_DURATION_MS = 6000
SHORT_DURATION_MS = 2000

# 同时兼容中英文的句号 / 问号 / 叹号。
SENTENCE_ENDINGS = frozenset("。！？!?")


class SegmentationError(ValueError):
    """词序列本身不合法（时间倒序 / 重叠）。"""

    def __init__(self, code: str, message: str, word_index: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        # word_index 为 1 起始序号。
        self.word_index = word_index


class InfeasibleError(Exception):
    """词序列合法，但不存在满足全部硬约束的分段方式。"""

    def __init__(self, earliest_unreachable: int):
        super().__init__(f"no feasible segmentation; earliest unreachable prefix ends at word {earliest_unreachable}")
        # 最早无法到达的前缀末词序号（1 起始）。
        self.earliest_unreachable = earliest_unreachable


@dataclass(frozen=True)
class Word:
    start_ms: int
    end_ms: int
    text: str
    speaker: str


@dataclass(frozen=True)
class Segment:
    index: int
    start_ms: int
    end_ms: int
    text: str
    speaker: str
    char_count: int
    duration_ms: int
    start_word_index: int
    end_word_index: int
    cost: int


def validate_words(words: list[Word]) -> None:
    """检查时间单调性：不得倒序、不得重叠；单个词 end 不得早于 start。"""
    prev: Word | None = None
    for i, w in enumerate(words, start=1):
        if w.end_ms < w.start_ms:
            raise SegmentationError(
                "invalid_time_range",
                f"word {i} ends ({w.end_ms}ms) before it starts ({w.start_ms}ms)",
                word_index=i,
            )
        if prev is not None:
            if w.start_ms < prev.start_ms:
                raise SegmentationError(
                    "time_out_of_order",
                    f"word {i} starts at {w.start_ms}ms but previous word starts at {prev.start_ms}ms",
                    word_index=i,
                )
            if w.start_ms < prev.end_ms:
                raise SegmentationError(
                    "time_overlap",
                    f"word {i} starts at {w.start_ms}ms before previous word ends at {prev.end_ms}ms",
                    word_index=i,
                )
        prev = w


def _ends_sentence(text: str) -> bool:
    return bool(text) and text[-1] in SENTENCE_ENDINGS


def _breaks_less(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    """断点序列字典序：自左向右逐位比较，第一个不同位上较小者胜。

    Python 元组的比较规则（公共前缀之后短前缀更小）与这里的需求一致，
    但显式实现可以避免依赖“断点列表长度/前缀关系”这一隐式前提。
    """
    for x, y in zip(a, b):
        if x != y:
            return x < y
    return len(a) < len(b)


def segment_words(words: list[Word]) -> list[Segment]:
    """用动态规划求最优分段。

    约定词序号为 1 起始，段 [i, k] 覆盖词 i..k。

    DP[k] = 到达前缀末词 k 的最优 (总分, 断点元组)，
    断点元组包含所有段末词序号（以 k 结尾）。
    转移：DP[k] = min over 合法段 [i, k] of DP[i-1] + 段代价，
    比较元组 ``(cost, breaks)``：先比总分，同分比断点序列字典序。

    同分时断点序列字典序最小的最优子结构成立：终点同为 k 的两条
    路径，断点列表都以 k 结尾，字典序最小的前缀接同一个后缀段后
    仍然最小，因此每个前缀只需保留一条最优路径。
    """
    if not words:
        raise SegmentationError("empty_words", "word list must not be empty")
    validate_words(words)

    n = len(words)

    # dp[k]：(最小总分, 断点元组)；dp[0] 表示空前缀。
    # None 表示该前缀不可到达。
    dp: list[tuple[int, tuple[int, ...]] | None] = [None] * (n + 1)
    dp[0] = (0, ())

    for k in range(1, n + 1):
        w_k = words[k - 1]
        speaker = w_k.speaker
        # 当前连续同说话人块内（与词 k 同 speaker 的连续区间）的字符累加。
        char_count = 0
        best: tuple[int, tuple[int, ...]] | None = None

        # 倒序扩展候选段 [i, k]，逐条利用硬约束剪枝。
        in_speaker_block = True
        for i in range(k, 0, -1):
            w_i = words[i - 1]

            # 换人处必须断段：段内说话人必须一致。
            # 只能 continue 不能 break：说话人可能在更早位置重新变回当前说话人
            # （如 A,B,A）。一旦遇到不同说话人，当前同说话人连续块即结束，
            # 之后的词既不计入字符数，也不再可能并入段内文本。
            if w_i.speaker != speaker:
                in_speaker_block = False
                continue
            if not in_speaker_block:
                # 已经跨过了别的说话人，无法组成“连续且同说话人”的段；
                # 时长随 i 减小只会更长，遇到后也不再可能合法。
                break

            duration = w_k.end_ms - w_i.start_ms
            # i 继续减小时首词更早，时长只会更长；超过上限后可整体停止。
            if duration > MAX_DURATION_MS:
                break

            # 文本按词序直接拼接（长度按 Unicode 码点计）。
            # 即使当前 [i,k] 时长不足而 continue，更早的 i 仍会把本词包含在
            # 段内，所以字符数必须先累加，不能在时长检查处漏计。
            char_count += len(w_i.text)
            if char_count > MAX_CHARS:
                # 再加入更早的词只会更长，停止扩展。
                break

            # 末词结束 - 首词开始尚不足最短时长，继续向前可能补足。
            if duration < MIN_DURATION_MS:
                continue

            prev_state = dp[i - 1]
            if prev_state is None:
                continue

            prev_cost, prev_breaks = prev_state
            seg_cost = 0
            # 末段（k == n）豁免“未以句末标点结尾”的 2 分。
            if k < n and not _ends_sentence(w_k.text):
                seg_cost += 2
            if duration < SHORT_DURATION_MS:
                seg_cost += 1

            candidate = (prev_cost + seg_cost, prev_breaks + (k,))
            if best is None or candidate[0] < best[0] or (
                candidate[0] == best[0] and _breaks_less(candidate[1], best[1])
            ):
                best = candidate

        dp[k] = best

    if dp[n] is None:
        # 找最早无法到达的前缀末词序号。
        earliest = next(k for k in range(1, n + 1) if dp[k] is None)
        raise InfeasibleError(earliest)

    _, breaks = dp[n]

    segments: list[Segment] = []
    seg_start = 1
    for seg_no, end in enumerate(breaks, start=1):
        start = seg_start
        text = "".join(words[j - 1].text for j in range(start, end + 1))
        first = words[start - 1]
        last = words[end - 1]
        duration = last.end_ms - first.start_ms
        cost = 0
        if end < n and not _ends_sentence(last.text):
            cost += 2
        if duration < SHORT_DURATION_MS:
            cost += 1
        segments.append(
            Segment(
                index=seg_no,
                start_ms=first.start_ms,
                end_ms=last.end_ms,
                text=text,
                speaker=first.speaker,
                char_count=len(text),
                duration_ms=duration,
                start_word_index=start,
                end_word_index=end,
                cost=cost,
            )
        )
        seg_start = end + 1

    return segments


def total_score(segments: list[Segment]) -> int:
    return sum(s.cost for s in segments)


def _format_srt_timestamp(ms: int) -> str:
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, milliseconds = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def build_srt(segments: list[Segment]) -> str:
    """把分段结果渲染成标准 SRT 字幕文本。"""
    lines: list[str] = []
    for seg in segments:
        lines.append(str(seg.index))
        lines.append(f"{_format_srt_timestamp(seg.start_ms)} --> {_format_srt_timestamp(seg.end_ms)}")
        lines.append(f"[{seg.speaker}] {seg.text}")
        lines.append("")
    return "\n".join(lines)
