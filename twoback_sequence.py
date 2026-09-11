# 2-back 工作記憶測驗：刺激序列生成
#
# 序列規則（研究規格，不可自行更動）：
#   1. 只使用 12 個子音字母，已排除母音與形狀相近的字母
#   2. 正式測驗 30 題，目標題（與前兩題字母相同）恰好 9 題
#   3. 第 1、2 題不可為目標題
#   4. 不設誘答題：非目標題不可與前一題相同（1-back 誘答）、
#      不可與前三題相同（3-back 誘答），也不可與前兩題相同（那會變成目標題）
#   5. 目標題位置隨機，每次施測重新生成

import random

# 排除母音 A/E/I/O/U 與形狀相近的字母後所使用的 12 個字母
LETTERS = ["B", "F", "H", "J", "K", "L", "M", "N", "R", "S", "T", "X"]

FORMAL_TRIALS = 30
FORMAL_TARGETS = 9

# 第一次施測：12 題練習（目標題 4 題）
FIRST_PRACTICE_TRIALS = 12
FIRST_PRACTICE_TARGETS = 4

# 一般施測：6 題暖身（目標題 2 題）
REGULAR_WARMUP_TRIALS = 6
REGULAR_WARMUP_TARGETS = 2


def generate_sequence(n_trials, n_targets, rng=None):
    """生成一組 2-back 序列。

    回傳 list，每個元素為 {"letter": "B", "is_target": True/False}，
    順序即為施測順序（list 的第 0 個元素是第 1 題）。

    rng 可傳入 random.Random 實例以固定亂數（測試用）；預設使用全域亂數。
    """
    if rng is None:
        rng = random

    # 第 1、2 題不可為目標題，因此目標題只能落在第 3 題（索引 2）之後
    available_positions = list(range(2, n_trials))
    if n_targets > len(available_positions):
        raise ValueError(
            "目標題數 %d 超過可用位置數 %d（第 1、2 題不可為目標題）"
            % (n_targets, len(available_positions))
        )

    target_positions = set(rng.sample(available_positions, n_targets))

    sequence = []
    for i in range(n_trials):
        if i in target_positions:
            # 目標題：與前兩題相同
            letter = sequence[i - 2]["letter"]
            is_target = True
        else:
            # 非目標題：不可與前一題（1-back 誘答）、前三題（3-back 誘答）相同，
            # 也不可與前兩題相同（那會變成目標題）
            excluded = set()
            if i >= 1:
                excluded.add(sequence[i - 1]["letter"])
            if i >= 2:
                excluded.add(sequence[i - 2]["letter"])
            if i >= 3:
                excluded.add(sequence[i - 3]["letter"])

            candidates = [letter for letter in LETTERS if letter not in excluded]
            letter = rng.choice(candidates)
            is_target = False

        sequence.append({"letter": letter, "is_target": is_target})

    return sequence


def generate_formal_sequence(rng=None):
    """正式測驗序列：30 題，目標題 9 題。"""
    return generate_sequence(FORMAL_TRIALS, FORMAL_TARGETS, rng=rng)


def generate_practice_sequence(practice_type, rng=None):
    """練習／暖身序列。

    practice_type = "first"   → 第一次施測，12 題練習（目標題 4 題）
    practice_type = "regular" → 一般施測，6 題暖身（目標題 2 題）
    """
    if practice_type == "first":
        return generate_sequence(
            FIRST_PRACTICE_TRIALS, FIRST_PRACTICE_TARGETS, rng=rng
        )
    elif practice_type == "regular":
        return generate_sequence(
            REGULAR_WARMUP_TRIALS, REGULAR_WARMUP_TARGETS, rng=rng
        )
    else:
        raise ValueError('practice_type 必須是 "first" 或 "regular"')


def validate_sequence(sequence, expected_targets=None):
    """檢查序列是否符合五條規則，回傳違規描述的 list（空 list 表示完全合規）。

    提供給單元測試與序列分布檢查腳本使用，也可在施測前做最後一道把關。
    """
    problems = []

    for i, trial in enumerate(sequence):
        # 規則 1：字母必須在允許的 12 個字母內
        if trial["letter"] not in LETTERS:
            problems.append("第 %d 題使用了不允許的字母 %s" % (i + 1, trial["letter"]))

        # 規則 3：第 1、2 題不可為目標題
        if i < 2 and trial["is_target"]:
            problems.append("第 %d 題被標記為目標題，但前兩題不可為目標題" % (i + 1))

        # is_target 標記必須與字母實際關係相符
        if i >= 2:
            actually_matches = trial["letter"] == sequence[i - 2]["letter"]
            if trial["is_target"] and not actually_matches:
                problems.append("第 %d 題標記為目標題，但字母與前兩題不同" % (i + 1))
            if not trial["is_target"] and actually_matches:
                problems.append("第 %d 題標記為非目標題，但字母與前兩題相同" % (i + 1))

        # 規則 4：不設誘答題
        if not trial["is_target"] and i >= 1:
            if trial["letter"] == sequence[i - 1]["letter"]:
                problems.append("第 %d 題與前一題字母相同（1-back 誘答）" % (i + 1))
        if not trial["is_target"] and i >= 3:
            if trial["letter"] == sequence[i - 3]["letter"]:
                problems.append("第 %d 題與前三題字母相同（3-back 誘答）" % (i + 1))

    # 規則 2：目標題數必須正確
    if expected_targets is not None:
        actual_targets = sum(1 for trial in sequence if trial["is_target"])
        if actual_targets != expected_targets:
            problems.append(
                "目標題共 %d 題，應為 %d 題" % (actual_targets, expected_targets)
            )

    return problems
