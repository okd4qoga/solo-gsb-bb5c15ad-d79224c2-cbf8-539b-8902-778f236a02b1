"""核心分段算法测试。"""

from __future__ import annotations

import pytest

from app.segmenter import (
    InfeasibleError,
    SegmentationError,
    Word,
    build_srt,
    segment_words,
    total_score,
)


def w(start: int, end: int, text: str, speaker: str = "A") -> Word:
    return Word(start_ms=start, end_ms=end, text=text, speaker=speaker)


def texts(segments):
    return [s.text for s in segments]


def boundaries(segments):
    return [s.end_word_index for s in segments]


# ---------- 基础分段 ----------

def test_prefers_fewer_segments_when_cost_lower():
    # 合并为一段（末段豁免标点分、3000ms 不短）成本 0，优于拆成两段（各短段共 2 分）。
    words = [
        w(0, 1500, "你好。"),
        w(1500, 3000, "世界"),
    ]
    segs = segment_words(words)
    assert texts(segs) == ["你好。世界"]
    assert total_score(segs) == 0


def test_punctuation_break_preferred_when_merge_hits_char_limit():
    # 合并将达到 19 字，超过 18 字上限，必须断开。
    words = [
        w(0, 1500, "你好。"),
        w(1500, 3000, "世" * 16),
    ]
    segs = segment_words(words)
    assert texts(segs) == ["你好。", "世" * 16]
    # 第一段标点终结但只有 1500ms：1 分；末段免标点、1500ms：1 分。
    assert total_score(segs) == 2


def test_single_word_segment_must_meet_duration_bounds():
    # 单词 900ms，不足 1000ms，且没有前文可合并 -> 不可行。
    words = [w(0, 900, "你好")]
    with pytest.raises(InfeasibleError) as exc:
        segment_words(words)
    assert exc.value.earliest_unreachable == 1


def test_max_duration_violation_is_infeasible():
    # 单词 6500ms，超过 6000ms 上限 -> 不可行。
    words = [w(0, 6500, "啊")]
    with pytest.raises(InfeasibleError) as exc:
        segment_words(words)
    assert exc.value.earliest_unreachable == 1


def test_duration_boundaries_are_inclusive():
    # 恰好 1000ms 的单词合法；恰好 6000ms 也合法。
    segs = segment_words([w(0, 1000, "好。")])
    assert len(segs) == 1
    segs = segment_words([w(0, 6000, "好。")])
    assert len(segs) == 1


# ---------- 字符数 ----------

def test_char_limit_forces_break():
    words = [
        w(0, 1200, "一二三四五六"),
        w(1200, 2400, "七八九十一二"),  # 合并 12 字，仍可
        w(2400, 3600, "三四五六"),    # 合并将达 16... 再加下一词超限
        w(3600, 4800, "七八九十一二三四五六"),  # 16 字，自身合法
    ]
    segs = segment_words(words)
    assert all(s.char_count <= 18 for s in segs)


def test_single_word_over_char_limit_is_infeasible():
    words = [w(0, 2000, "超" * 19)]
    with pytest.raises(InfeasibleError) as exc:
        segment_words(words)
    assert exc.value.earliest_unreachable == 1


def test_chars_of_inner_words_are_counted_when_duration_too_short_to_split():
    # 回归测试：词 3（400ms）单独时长不足 1000ms，DP 倒序扩展时曾因
    # duration < MIN 提前 continue 而漏加其字符数，导致 19 字的段被误判合法。
    words = [
        w(0, 3000, "好"),
        w(3000, 4000, "你好"),
        w(4000, 4400, "你好"),       # 400ms，无法独立成段，但字符必须计入
        w(4400, 5600, "x" * 8),
        w(5600, 6800, "x" * 7),
        w(6800, 7600, "嗯！"),
    ]
    # 前缀 1、前缀 2 可行；包含词 3 的任何段都必须带上词 4+ 才能补足 1000ms。
    assert len(segment_words(words[:1])) == 1
    assert len(segment_words(words[:2])) == 1
    segs = segment_words(words)
    # 修复前 bug 会让非法的 19 字段 [3..6] 混入结果（成本还更低）；
    # 修复后最优解为 [1,2,3] | [4,5,6]：各 5/17 字、4400/3200ms，总分 0。
    assert boundaries(segs) == [3, 6]
    assert all(s.char_count <= 18 for s in segs)
    assert all(1000 <= s.duration_ms <= 6000 for s in segs)


# ---------- 说话人 ----------

def test_speaker_change_forces_break():
    words = [
        w(0, 1500, "你好", "A"),
        w(1500, 3000, "你好", "B"),
    ]
    segs = segment_words(words)
    assert boundaries(segs) == [1, 2]
    assert [s.speaker for s in segs] == ["A", "B"]


def test_speaker_alternation_can_be_infeasible_on_duration():
    # 说话人每 500ms 切换，每个单段最多 500ms 且无法跨人合并 -> 不可行。
    words = [
        w(0, 500, "甲", "A"),
        w(500, 1000, "乙", "B"),
    ]
    with pytest.raises(InfeasibleError) as exc:
        segment_words(words)
    # 前缀词 1 就无法到达。
    assert exc.value.earliest_unreachable == 1


def test_speaker_reappearing_after_other_speaker_is_still_reachable():
    # A, B, A：末词 A 向左枚举时不能在遇到 B 后就停止，否则会漏掉把词 3
    # 与词 2（B）组成合法段的机会，错误地报告“不可行”。
    words = [
        w(0, 2000, "第一句。", "A"),
        w(2000, 3000, "插话", "B"),
        w(3000, 5000, "第二句。", "A"),
    ]
    segs = segment_words(words)
    # 最优：[1]（0 分）|[2,3]：段内 B,A 不允许，所以词 2、3 必须断开；
    # 实际为 [1] | [2] | [3]——词 2 恰好 1000ms 可独立成段。
    assert [s.speaker for s in segs] == ["A", "B", "A"]
    assert boundaries(segs) == [1, 2, 3]


def test_speaker_reappearing_aba_with_undividable_middle():
    # B 只有 800ms 无法独立成段；末词 A 必须能与 B 配对（段内允许 B≠A 才怪），
    # 此例无解，但“最早不可到达”应正确定位到词 2，而不是因枚举提前 break 误判。
    words = [
        w(0, 2000, "第一句。", "A"),
        w(2000, 2800, "插话", "B"),
        w(2800, 4800, "第二句。", "A"),
    ]
    with pytest.raises(InfeasibleError) as exc:
        segment_words(words)
    assert exc.value.earliest_unreachable == 2


# ---------- 打分与字典序 ----------

def test_punctuation_is_a_cost_signal_not_a_forced_break():
    # 标点只影响打分，不强制断段。
    # 合并为一段成本 0；拆成两段（两段都以标点结尾、各 2000ms）成本也是 0，
    # 同分按断点序列字典序取 (1,2) < (2,)，即优先在句号后断开。
    words = [
        w(0, 2000, "好。"),
        w(2000, 4000, "很好。"),
    ]
    segs = segment_words(words)
    assert texts(segs) == ["好。", "很好。"]
    assert total_score(segs) == 0


def test_nonfinal_segment_without_punctuation_pays_two():
    words = [
        w(0, 2000, "你好"),
        w(2000, 4000, "再见"),
        w(4000, 6001, "朋友"),  # 整体 6001ms 超上限，至少两段
    ]
    segs = segment_words(words)
    # [1]|[2,3]：第一段无标点非末段 2 分；末段豁免 => 2
    assert total_score(segs) == 2
    assert boundaries(segs) == [1, 3]


def test_short_segment_cost():
    words = [w(0, 1500, "好。")]
    segs = segment_words(words)
    # 末段免标点分，但 1500ms < 2000ms 记 1 分。
    assert segs[0].cost == 1


def test_lexicographically_smallest_breaks_on_tie():
    # 3 词，各 2000ms，整体跨度 6001ms 超过 6000ms 上限，无法合成一段：
    #   切分 [1]|[2,3]  ：[1] 无标点非末段 2 分；[2,3] 末段免标点 => 2
    #   切分 [1,2]|[3]  ：[1,2] 无标点非末段 2 分；[3] 末段免标点 => 2
    # 同分，断点序列 (1,3) < (2,3)，应选前者。
    words = [
        w(0, 2000, "你好"),
        w(2000, 4000, "世界"),
        w(4000, 6001, "再见"),
    ]
    segs = segment_words(words)
    assert boundaries(segs) == [1, 3]
    assert total_score(segs) == 2


def test_earlier_break_wins_via_lexicographic_order():
    # 词 1 以句号结尾：
    #   一段 [1,2,3]：末段豁免标点分，6000ms => 0，断点 (3,)
    #   [1]|[2,3]  ：第一段标点终结 0 分，末段豁免 => 0，断点 (1,3)
    # 同分，断点序列 (1,3) < (3,)，选更早断的方案。
    words = [
        w(0, 2000, "你好。"),
        w(2000, 4000, "世界"),
        w(4000, 6000, "再见"),
    ]
    segs = segment_words(words)
    assert boundaries(segs) == [1, 3]
    assert total_score(segs) == 0


# ---------- 不可行与最早不可到达前缀 ----------

def test_earliest_unreachable_prefix():
    # 词 1：0..3000，词 2：3000..9500（6500ms，超上限）
    # 词 1 可独立成段（以标点结尾与否都行，3000ms 合法）。
    # 词 2 单独 6500ms 超限；[1,2] 9500ms 超限 -> 前缀 2 不可到达。
    words = [
        w(0, 3000, "开场。"),
        w(3000, 9500, "长篇大论"),
    ]
    with pytest.raises(InfeasibleError) as exc:
        segment_words(words)
    assert exc.value.earliest_unreachable == 2


# ---------- 时间校验 ----------

def test_time_out_of_order_rejected():
    words = [
        w(1000, 2000, "甲"),
        w(900, 1500, "乙"),
    ]
    with pytest.raises(SegmentationError) as exc:
        segment_words(words)
    assert exc.value.code == "time_out_of_order"
    assert exc.value.word_index == 2


def test_time_overlap_rejected():
    words = [
        w(0, 1500, "甲"),
        w(1000, 2500, "乙"),
    ]
    with pytest.raises(SegmentationError) as exc:
        segment_words(words)
    assert exc.value.code == "time_overlap"
    assert exc.value.word_index == 2


def test_touching_words_are_allowed():
    # 相邻词首尾相接（后词 start == 前词 end）不算重叠，不应报错。
    words = [
        w(0, 1000, "甲"),
        w(1000, 3000, "乙。"),
    ]
    segs = segment_words(words)
    assert segs[0].start_ms == 0 and segs[-1].end_ms == 3000


def test_word_ending_before_start_rejected():
    with pytest.raises(SegmentationError) as exc:
        segment_words([w(2000, 1000, "甲")])
    assert exc.value.code == "invalid_time_range"
    assert exc.value.word_index == 1


# ---------- SRT ----------

def test_build_srt_format():
    words = [
        w(0, 2000, "你好。", "A"),
        w(2000, 4000, "再见", "B"),
    ]
    segs = segment_words(words)
    srt = build_srt(segs)
    lines = srt.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:02,000"
    assert lines[2] == "[A] 你好。"
    assert lines[4] == "2"
    assert lines[5] == "00:00:02,000 --> 00:00:04,000"
    assert lines[6] == "[B] 再见"


# ---------- Unicode 计数 ----------

def test_unicode_code_points_counted():
    # emoji 与组合字符按 Python 码点（len）计数；5 个 emoji = 5 码点。
    words = [w(0, 2000, "😀" * 5)]
    segs = segment_words(words)
    assert segs[0].char_count == 5
