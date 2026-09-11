# 2-back 序列生成的單元測試
#
# 執行方式（在 stroop_project 資料夾下）：
#   python -m unittest test_twoback_sequence -v

import random
import unittest

import twoback_sequence as tb

# 每條規則都用大量隨機序列驗證，而非只測一組，
# 因為序列每次施測都重新生成，必須確保「任何一次」都合規。
REPEATS = 500


class TestLetters(unittest.TestCase):
    """規則 1：字母集合"""

    def test_letter_set_is_the_twelve_specified(self):
        self.assertEqual(
            tb.LETTERS,
            ["B", "F", "H", "J", "K", "L", "M", "N", "R", "S", "T", "X"],
        )

    def test_no_vowels(self):
        for vowel in "AEIOU":
            self.assertNotIn(vowel, tb.LETTERS)

    def test_only_allowed_letters_appear(self):
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            for trial in sequence:
                self.assertIn(trial["letter"], tb.LETTERS)


class TestFormalSequence(unittest.TestCase):
    """正式測驗序列：30 題、目標題 9 題"""

    def test_length_is_30(self):
        for _ in range(REPEATS):
            self.assertEqual(len(tb.generate_formal_sequence()), 30)

    def test_target_count_is_exactly_9(self):
        # 規則 2：目標題恰好 9 題
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            targets = sum(1 for trial in sequence if trial["is_target"])
            self.assertEqual(targets, 9)

    def test_target_flag_matches_actual_letters(self):
        # is_target 必須真的代表「與前兩題字母相同」
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            for i, trial in enumerate(sequence):
                if i < 2:
                    continue
                matches = trial["letter"] == sequence[i - 2]["letter"]
                self.assertEqual(trial["is_target"], matches)

    def test_first_two_trials_are_never_targets(self):
        # 規則 3
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            self.assertFalse(sequence[0]["is_target"])
            self.assertFalse(sequence[1]["is_target"])

    def test_non_target_differs_from_previous_trial(self):
        # 規則 4：不設誘答題，此處檢查 1-back 誘答
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            for i in range(1, len(sequence)):
                if not sequence[i]["is_target"]:
                    self.assertNotEqual(
                        sequence[i]["letter"], sequence[i - 1]["letter"]
                    )

    def test_non_target_differs_from_two_trials_back(self):
        # 規則 4：非目標題不可與前兩題相同（否則它就是目標題）
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            for i in range(2, len(sequence)):
                if not sequence[i]["is_target"]:
                    self.assertNotEqual(
                        sequence[i]["letter"], sequence[i - 2]["letter"]
                    )

    def test_non_target_differs_from_three_trials_back(self):
        # 規則 4：不設誘答題，此處檢查 3-back 誘答
        # 送審文件「2-back 工作記憶測驗說明」明文寫「不設誘答題」，
        # 對 2-back 而言誘答題即 n±1，也就是 1-back 與 3-back 兩種。
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            for i in range(3, len(sequence)):
                if not sequence[i]["is_target"]:
                    self.assertNotEqual(
                        sequence[i]["letter"], sequence[i - 3]["letter"]
                    )

    def test_no_immediate_repeats_anywhere(self):
        # 連目標題也不應該與前一題相同（否則畫面會出現連續兩個一樣的字母）
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            for i in range(1, len(sequence)):
                self.assertNotEqual(
                    sequence[i]["letter"], sequence[i - 1]["letter"]
                )

    def test_sequence_is_not_fixed(self):
        # 規則 5：每次施測重新生成，不可固定序列
        sequences = set()
        for _ in range(50):
            sequence = tb.generate_formal_sequence()
            sequences.add("".join(trial["letter"] for trial in sequence))
        self.assertGreater(len(sequences), 40)

    def test_target_positions_are_not_fixed(self):
        # 目標題位置本身也必須每次不同
        position_sets = set()
        for _ in range(50):
            sequence = tb.generate_formal_sequence()
            positions = tuple(
                i for i, trial in enumerate(sequence) if trial["is_target"]
            )
            position_sets.add(positions)
        self.assertGreater(len(position_sets), 40)


class TestPracticeSequences(unittest.TestCase):
    """練習（12 題／目標 4 題）與暖身（6 題／目標 2 題）"""

    def test_first_practice_shape(self):
        for _ in range(REPEATS):
            sequence = tb.generate_practice_sequence("first")
            self.assertEqual(len(sequence), 12)
            targets = sum(1 for trial in sequence if trial["is_target"])
            self.assertEqual(targets, 4)

    def test_regular_warmup_shape(self):
        for _ in range(REPEATS):
            sequence = tb.generate_practice_sequence("regular")
            self.assertEqual(len(sequence), 6)
            targets = sum(1 for trial in sequence if trial["is_target"])
            self.assertEqual(targets, 2)

    def test_practice_sequences_follow_the_same_rules(self):
        # 練習與暖身適用同樣的序列規則
        for practice_type, n_targets in (("first", 4), ("regular", 2)):
            for _ in range(REPEATS):
                sequence = tb.generate_practice_sequence(practice_type)
                self.assertEqual(tb.validate_sequence(sequence, n_targets), [])

    def test_unknown_practice_type_is_rejected(self):
        with self.assertRaises(ValueError):
            tb.generate_practice_sequence("warmup")


class TestValidator(unittest.TestCase):
    """驗證函式本身要抓得到違規，否則上面的測試等於沒測"""

    def test_clean_sequence_passes(self):
        for _ in range(REPEATS):
            sequence = tb.generate_formal_sequence()
            self.assertEqual(tb.validate_sequence(sequence, 9), [])

    def test_detects_wrong_target_count(self):
        sequence = tb.generate_formal_sequence()
        self.assertNotEqual(tb.validate_sequence(sequence, 8), [])

    def test_detects_target_in_first_two_trials(self):
        sequence = tb.generate_formal_sequence()
        sequence[1]["is_target"] = True
        self.assertNotEqual(tb.validate_sequence(sequence), [])

    def test_detects_1_back_repeat(self):
        sequence = tb.generate_formal_sequence()
        # 硬把第 5 題改成與第 4 題相同，且標記為非目標題
        sequence[4]["letter"] = sequence[3]["letter"]
        sequence[4]["is_target"] = False
        self.assertNotEqual(tb.validate_sequence(sequence), [])

    def test_detects_unlabelled_2_back_repeat(self):
        sequence = tb.generate_formal_sequence()
        # 第 6 題與第 4 題相同卻標記為非目標題
        sequence[5]["letter"] = sequence[3]["letter"]
        sequence[5]["is_target"] = False
        self.assertNotEqual(tb.validate_sequence(sequence), [])

    def test_detects_3_back_lure(self):
        sequence = tb.generate_formal_sequence()
        # 硬把第 8 題改成與第 5 題相同，且標記為非目標題
        sequence[7]["letter"] = sequence[4]["letter"]
        sequence[7]["is_target"] = False
        self.assertNotEqual(tb.validate_sequence(sequence), [])

    def test_detects_illegal_letter(self):
        sequence = tb.generate_formal_sequence()
        sequence[7]["letter"] = "A"
        self.assertNotEqual(tb.validate_sequence(sequence), [])


class TestReproducibility(unittest.TestCase):
    """可傳入固定亂數種子，方便重現特定序列以便除錯"""

    def test_same_seed_gives_same_sequence(self):
        first = tb.generate_formal_sequence(rng=random.Random(20260911))
        second = tb.generate_formal_sequence(rng=random.Random(20260911))
        self.assertEqual(first, second)

    def test_different_seeds_give_different_sequences(self):
        first = tb.generate_formal_sequence(rng=random.Random(1))
        second = tb.generate_formal_sequence(rng=random.Random(2))
        self.assertNotEqual(first, second)


class TestImpossibleRequests(unittest.TestCase):
    def test_too_many_targets_raises(self):
        # 6 題只有 4 個位置可放目標題（第 3～6 題），要 5 題應直接報錯
        with self.assertRaises(ValueError):
            tb.generate_sequence(6, 5)
