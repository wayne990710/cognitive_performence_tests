# 連續生成 1,000 組正式測驗序列，統計目標題數與目標題位置分布。
#
# 用途：驗證序列生成器在大量施測下不會系統性偏向某些位置
#       （例如目標題總是集中在後半段，會讓受試者猜到規律）。
#
# 執行方式（在 stroop_project 資料夾下）：
#   python twoback_sequence_check.py

import twoback_sequence as tb

N_SEQUENCES = 1000


def main():
    target_counts = {}
    position_counts = {i: 0 for i in range(tb.FORMAL_TRIALS)}
    letter_counts = {letter: 0 for letter in tb.LETTERS}
    violations = []

    for run in range(N_SEQUENCES):
        sequence = tb.generate_formal_sequence()

        problems = tb.validate_sequence(sequence, tb.FORMAL_TARGETS)
        if problems:
            violations.append((run + 1, problems))

        n_targets = sum(1 for trial in sequence if trial["is_target"])
        target_counts[n_targets] = target_counts.get(n_targets, 0) + 1

        for i, trial in enumerate(sequence):
            if trial["is_target"]:
                position_counts[i] += 1
            letter_counts[trial["letter"]] += 1

    print("連續生成 %d 組正式測驗序列（每組 %d 題，目標題應為 %d 題）"
          % (N_SEQUENCES, tb.FORMAL_TRIALS, tb.FORMAL_TARGETS))
    print()

    print("【規則檢查】")
    if violations:
        print("  發現 %d 組違規序列：" % len(violations))
        for run, problems in violations[:10]:
            print("    第 %d 組：%s" % (run, "；".join(problems)))
    else:
        print("  全部 %d 組皆符合全部序列規則" % N_SEQUENCES)
    print()

    print("【每組目標題數分布】")
    for n_targets in sorted(target_counts):
        print("  %2d 題目標：%4d 組" % (n_targets, target_counts[n_targets]))
    print()

    # 目標題只能落在第 3～30 題，共 28 個位置，9 題平均分配後
    # 每個位置的期望次數為 1000 * 9 / 28 ≒ 321.4 次
    eligible_positions = tb.FORMAL_TRIALS - 2
    expected = N_SEQUENCES * tb.FORMAL_TARGETS / eligible_positions

    print("【目標題位置分布】（期望值約 %.1f 次）" % expected)
    for i in range(tb.FORMAL_TRIALS):
        count = position_counts[i]
        if i < 2:
            note = "（規則上不可為目標題）"
        else:
            note = "%+.1f%%" % ((count - expected) / expected * 100)
        bar = "#" * int(count / 10)
        print("  第 %2d 題：%4d 次 %-28s %s" % (i + 1, count, bar, note))
    print()

    observed_eligible = [position_counts[i] for i in range(2, tb.FORMAL_TRIALS)]
    print("  第 3～30 題之中，最少 %d 次、最多 %d 次，極差 %d 次"
          % (min(observed_eligible), max(observed_eligible),
             max(observed_eligible) - min(observed_eligible)))
    print()

    print("【字母使用次數】（總題數 %d）" % (N_SEQUENCES * tb.FORMAL_TRIALS))
    for letter in tb.LETTERS:
        print("  %s：%5d 次" % (letter, letter_counts[letter]))


if __name__ == "__main__":
    main()
