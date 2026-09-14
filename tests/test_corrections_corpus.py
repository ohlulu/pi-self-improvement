"""Bilingual correction corpus (REQ-010, DEC-008).

Two directions matter equally. Missing a correction loses the signal this whole
rewrite exists for — roughly 90% of the author's user messages are CJK, and an
English-only cue set finds almost nothing in them. Flagging an instruction, a
pasted document or an agreement as a correction fills the review packet with
noise until nobody reads it.

The false positives here are not hypothetical: they are the ones the upstream
tool's own notes and the feasibility investigation called out.
"""

import unittest

from pi_self_improvement import cues, detect
from pi_self_improvement.model import AssistantTurn, SessionSummary, UserMessage
from pi_self_improvement.redact import Redactor

#: Short, reactive messages that are unambiguously corrections.
CORRECTIONS_ZH = [
    "不對，我是說要用 demo-cli 的 release 子指令。",
    "不是這樣，重來一次。",
    "你搞錯了，那個檔案不需要動。",
    "我的意思是先跑測試再改。",
    "為什麼你要直接改 main？",
    "你應該先問我再動 schema。",
    "記住，這個專案不用 npm。",
]

CORRECTIONS_EN = [
    "That's wrong, the flag is --release not --version.",
    "Not what I asked. I wanted the summary only.",
    "You missed the changelog step.",
    "Never do a force push on this repo.",
    "Stop doing the manual edit, use the generator.",
    "You should have run the tests first.",
    "Why did you change the schema?",
    "I meant the staging config, not production.",
]

#: Short reactive messages carrying only a weak cue.
WEAK_CORRECTIONS = [
    ("Use the packaging service instead.", "en"),
    ("Don't do it by hand.", "en"),
    ("應該用 release 子指令。", "zh-Hant"),
    ("改成 staging 設定。", "zh-Hant"),
    ("不要動那個檔案。", "zh-Hant"),
]

#: Must never be flagged.
NOT_CORRECTIONS = [
    ("沒錯，就這樣做。", "AC-018 agreement guard"),
    ("沒錯，不對的地方我自己改。", "guard wins over a strong cue in the same message"),
    ("這樣不錯，繼續。", "praise"),
    ("沒問題，麻煩了。", "assent"),
    ("還不錯，再加一個測試。", "praise plus a request"),
    ("幫我把 release notes 加上去。", "a plain instruction"),
    ("Please add a release notes section to the guide.", "a plain instruction"),
    ("Can you check whether the build passes?", "a question"),
    ("Thanks, that works.", "acknowledgement"),
]

#: A pasted document. Long, contains "instead", and is not a correction (AC-017).
PASTED_DOCUMENT = (
    "Here is the vendor migration note I was sent, for reference only:\n\n"
    "The legacy packaging path is frozen from this release onward. Teams that still call the "
    "bundled archive helper should use the workspace packaging service instead, because the helper "
    "resolves paths relative to the invoking shell and that behaviour will not be preserved. "
    "Downstream consumers are expected to pin the published artifact hash rather than the branch "
    "name, and any pipeline that reads the generated manifest should treat missing optional fields "
    "as empty rather than failing the run. A compatibility shim will remain available for two more "
    "minor versions, after which the entry point is removed entirely."
)


class TestCuePacks(unittest.TestCase):
    def test_strong_chinese_cues_fire_on_short_messages(self):
        """AC-016."""
        for text in CORRECTIONS_ZH:
            with self.subTest(text=text):
                hit = cues.find_cue(text)
                self.assertIsNotNone(hit, f"missed a correction: {text}")
                self.assertEqual(hit.pack, "zh-Hant")
                self.assertEqual(hit.strength, cues.STRONG)

    def test_strong_english_cues_fire(self):
        for text in CORRECTIONS_EN:
            with self.subTest(text=text):
                hit = cues.find_cue(text)
                self.assertIsNotNone(hit, f"missed a correction: {text}")
                self.assertEqual(hit.strength, cues.STRONG)

    def test_weak_cues_fire_on_short_reactive_messages(self):
        for text, pack in WEAK_CORRECTIONS:
            with self.subTest(text=text):
                hit = cues.find_cue(text)
                self.assertIsNotNone(hit, f"missed a weak correction: {text}")
                self.assertEqual(hit.pack, pack)
                self.assertEqual(hit.strength, cues.WEAK)

    def test_agreement_and_instructions_are_never_corrections(self):
        """AC-018 and friends."""
        for text, why in NOT_CORRECTIONS:
            with self.subTest(why=why):
                self.assertIsNone(cues.find_cue(text), f"false positive ({why}): {text}")

    def test_a_pasted_document_is_not_a_correction(self):
        """AC-017: past the weak gate, "instead" is just a word in a document."""
        self.assertGreater(len(PASTED_DOCUMENT), cues.EN.weak_gate)
        self.assertIsNone(cues.find_cue(PASTED_DOCUMENT))

    def test_a_strong_cue_still_fires_in_a_longer_message(self):
        text = "I read through the whole plan and it looks reasonable. " * 5 + "That's wrong though."
        self.assertLess(len(text), cues.EN.strong_gate)
        self.assertIsNotNone(cues.find_cue(text))

    def test_a_weak_cue_alone_does_not_fire_in_a_long_message(self):
        text = "We should look at the packaging service instead of the helper. " * 12
        self.assertGreater(len(text), cues.EN.weak_gate)
        self.assertIsNone(cues.find_cue(text))

    def test_chinese_gates_are_tighter_than_english_ones(self):
        """CJK carries about twice the meaning per character."""
        self.assertLess(cues.ZH_HANT.strong_gate, cues.EN.strong_gate)
        self.assertLess(cues.ZH_HANT.weak_gate, cues.EN.weak_gate)

    def test_english_cues_respect_word_boundaries(self):
        self.assertIsNone(cues.find_cue("the insteadof helper is deprecated"))

    def test_a_guard_in_either_language_vetoes_the_message(self):
        """A bilingual user writes 沒錯 inside an English sentence."""
        self.assertIsNone(cues.find_cue("沒錯 — although you should have used the other flag."))

    def test_actually_only_counts_at_the_start_of_a_line(self):
        self.assertIsNotNone(cues.find_cue("Actually, use the other flag."))
        self.assertIsNone(cues.find_cue("I am not actually sure this matters."))


class TestQuestionsAreNotCorrections(unittest.TestCase):
    """Calibrated against the real corpus, where 16 of 42 weak-cue hits were
    questions. Every message here is a synthetic equivalent of a shape observed
    there — same structure, invented content.

    The distinction is sentence mood, not vocabulary: 「應該完成了？」 asks about
    state, 「應該要是斜斜的」 corrects the result.
    """

    QUESTIONS = [
        "應該完成了？",
        "這樣改成免費下載會有問題嗎？",
        "應該不會再 crash 了吧？",
        "不要動那個檔案比較好呢？",
        "想問一下，這邊應該怎麼處理",
        "請問這裡應該用哪一個方法",
        "Should we use the packaging service instead?",
        "Do not merge yet, or should have I waited?",
    ]

    def test_a_weak_cue_inside_a_question_is_not_a_correction(self):
        for text in self.QUESTIONS:
            with self.subTest(text=text):
                self.assertIsNone(cues.find_cue(text), f"question flagged as correction: {text}")

    def test_a_strong_cue_still_fires_inside_a_question(self):
        """為什麼你 and "why did you" are strong cues that are themselves questions.

        A blanket interrogative guard would delete two rows of the DEC-008 table.
        """
        for text in ("為什麼你直接改了 schema？", "Why did you change the schema?"):
            with self.subTest(text=text):
                hit = cues.find_cue(text)
                self.assertIsNotNone(hit, f"strong cue lost to the question guard: {text}")
                self.assertEqual(hit.strength, cues.STRONG)

    def test_a_declarative_with_an_internal_question_mark_still_fires(self):
        """「…應該要是圓角的？現在是直角」 states a problem and then the evidence."""
        hit = cues.find_cue("按鈕應該要是圓角的？現在是直角")

        self.assertIsNotNone(hit)
        self.assertEqual(hit.strength, cues.WEAK)

    def test_short_declarative_corrections_survive_the_guard(self):
        for text in ("星座我不要有 emoji", "section header 粗細通通改成 1", "預設那條橫線我不要"):
            with self.subTest(text=text):
                self.assertIsNotNone(cues.find_cue(text), f"lost a real correction: {text}")


class TestVerbosityTopic(unittest.TestCase):
    """Corrections about how the agent writes, not what it did.

    Every positive here is the shape of a message observed in the author's
    transcripts on two machines — the "your prose is too long" complaint was the
    single most frequent correction the strong/weak packs missed entirely
    (1 of 15 caught, and that one by accident on 我的意思是). The negatives are
    the observed false-positive shapes: 太長 about a kitty tab, a timeout, a
    branch, a UI title, a layout gap, and a request for short copy that is an
    instruction rather than a correction.
    """

    VERBOSITY = [
        "你的回覆會不會太冗長了，可以剪短一點嗎？",
        "整篇廢話太多，我改了一下清爽多了",
        "好像都太長了，我希望短一點，不用鋪陳太多",
        "description 不用這麼長，只要精簡就可以了",
        "comment 2 & 3 都太長了，不像真人",
        "太長了，應該不用這麼複雜的描述吧？你覺得呢？",
        "太長了，太簡單或常識的內容就不用寫了，挑重點",
        "你補了太長一段了吧？有必要嗎",
        "你寫的太過複雜，冗詞贅字一大堆",
        "我覺得不夠白話簡潔，你再修正一版",
        "為什麼你廢話這麼多",
        "感覺可以精簡一下？",
        "我發現你的冗詞很多，一件簡單的事你可以拆成兩三句說明",
        "你剛剛寫的規則太長了，我只要一行就好",
        "有些太冗長了",
        "太長了，可以簡短一點嗎？",
        "你前面太多字眼我看不懂",
        "AGENTS.md 也有點冗長",
        "你也幫我看看有沒有冗餘的內容",
        "這段有點冗於",
        "這句就夠了，搞這麼冗長幹嘛",
        "關於 git hook 落落長寫了一段，我覺得可以直接刪",
        "兩行雷點是否有點多餘？",
        "這樣寫有點累贅",
        "This is rambling, get to the point.",
        "Your reply is way too verbose, keep it short.",
        "This description is too long, can you trim it?",
        "Too wordy. Be concise.",
    ]

    NOT_VERBOSITY = [
        ("kitty tab 可以改成只顯示 folder name 嗎？不然太長了", "a tab title"),
        ("90s 太長了，我已經執行完了", "a timeout"),
        ("太長了吧，同事只花了 4d", "an estimate"),
        ("我覺得 branch -3 太長了，你按照場景描述給我有哪些修改", "a branch"),
        ("title 太長了，會擠壓到 content，調整一下 title", "a UI title"),
        ("當文字內容太長的時候，列表會跑版", "a layout bug"),
        ("幫我想一段簡短的文案", "an instruction for short copy"),
        ("感覺距離其他節點可以短一點點", "a layout gap"),
        ("用條列式，要完整，但不囉唆", "a qualifier on a request"),
        ("類似工具書 step by step 那種（但不要冗長）", "a qualifier on a request"),
        ("用 eli5-html，但不要太冗長", "a qualifier on a request, with 太"),
        ("可以再白話一點解釋嗎？（但不是要你寫的落落長）", "a qualifier with words in between"),
        ("shot2 我覺得太多餘，只有 15s 還要浪費", "a screenshot, not prose"),
        ("不是「冗餘」而是「缺欄」，與本批修法不同形狀", "冗餘 as a redundant field in code"),
        ("這兩個 enum case 是冗餘的，合併吧", "冗餘 as duplicate code"),
        ("本機操作+單人開發，有點多餘", "a process step, not prose"),
        ("run it with the verbose flag", "a CLI flag"),
        ("the timeout is too long, bump it down", "a timeout"),
        ("add padding to the container", "CSS"),
    ]

    def test_verbosity_complaints_carry_the_topic(self):
        for text in self.VERBOSITY:
            with self.subTest(text=text):
                hit = cues.find_cue(text)
                self.assertIsNotNone(hit, f"missed a verbosity correction: {text}")
                self.assertEqual(hit.topic, cues.VERBOSITY)
                self.assertEqual(hit.strength, cues.STRONG)

    def test_too_long_about_something_other_than_prose_is_not_verbosity(self):
        for text, why in self.NOT_VERBOSITY:
            with self.subTest(why=why):
                hit = cues.find_cue(text)
                self.assertTrue(
                    hit is None or hit.topic is None, f"false verbosity ({why}): {text} -> {hit}"
                )

    def test_a_question_does_not_hide_a_verbosity_complaint(self):
        """「可以簡短一點嗎？」 is a correction phrased politely."""
        hit = cues.find_cue("太長了，可以簡短一點嗎？")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.topic, cues.VERBOSITY)

    def test_a_qualifier_clause_only_blanks_itself(self):
        """The complaint before 但 survives; only the 但不… clause is removed."""
        hit = cues.find_cue("你寫得太冗長了，但不要刪掉範例")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.topic, cues.VERBOSITY)

    def test_a_guard_still_vetoes_a_verbosity_complaint(self):
        self.assertIsNone(cues.find_cue("沒錯，不過太長了可以簡短一點"))

    def test_a_topic_hit_wins_over_a_strong_cue(self):
        hit = cues.find_cue("不對，太冗長了")
        self.assertEqual(hit.topic, cues.VERBOSITY)

    def test_a_plain_correction_has_no_topic(self):
        self.assertIsNone(cues.find_cue("不對，我是說要用 release 子指令。").topic)


class TestPackConfiguration(unittest.TestCase):
    """REQ-019: packs are extensible and disableable from config."""

    def test_a_pack_can_be_disabled(self):
        packs = cues.build_packs({"zh-Hant": {"enabled": False}})
        self.assertEqual([pack.name for pack in packs], ["en"])
        self.assertIsNone(cues.find_cue("不對，重來。", packs))

    def test_a_pack_can_be_extended(self):
        packs = cues.build_packs({"en": {"strong": ["knock it off"]}})
        self.assertIsNotNone(cues.find_cue("Knock it off, use the generator.", packs))

    def test_defaults_survive_an_extension(self):
        packs = cues.build_packs({"en": {"strong": ["knock it off"]}})
        self.assertIsNotNone(cues.find_cue("That's wrong.", packs))

    def test_a_new_language_pack_can_be_added(self):
        packs = cues.build_packs({"de": {"strong": ["das ist falsch"], "strong_gate": 500}})
        self.assertIsNotNone(cues.find_cue("Das ist falsch, bitte anders.", packs))

    def test_a_topic_can_be_extended(self):
        packs = cues.build_packs({"en": {"topics": {"verbosity": [r"\byapping\b"]}}})
        hit = cues.find_cue("Stop yapping and give me the diff.", packs)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.topic, cues.VERBOSITY)
        self.assertIsNotNone(cues.find_cue("Too wordy.", packs), "builtin topic patterns kept")

    def test_a_new_topic_can_be_added(self):
        packs = cues.build_packs({"en": {"topics": {"tone": [r"\btoo formal\b"]}}})
        self.assertEqual(cues.find_cue("This reads too formal.", packs).topic, "tone")


class TestCorrectionSignals(unittest.TestCase):
    """The detector wiring: a correction needs something to correct."""

    def setUp(self):
        self.redactor = Redactor()

    def session(self, *messages, answered_at=2, cwd="/tmp/pi-fixtures/beta"):
        return SessionSummary(
            path="sessions/--tmp-beta--/s1.jsonl",
            session_id="s1",
            cwd=cwd,
            user_messages=[UserMessage(text=text, line=line) for line, text in messages],
            assistant_turns=[AssistantTurn(text="Done.", line=answered_at)],
        )

    def corrections(self, summary):
        return [
            signal
            for signal in detect.detect_session(summary, redactor=self.redactor)
            if signal.kind == detect.CORRECTION
        ]

    def test_a_correction_after_an_answer_is_recorded(self):
        summary = self.session((3, "不對，我是說要用 release 子指令。"))

        signals = self.corrections(summary)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].detail["pack"], "zh-Hant")
        self.assertEqual(signals[0].evidence.line, 3)
        self.assertEqual(signals[0].subject, "/tmp/pi-fixtures/beta")

    def test_a_verbosity_correction_carries_its_topic(self):
        summary = self.session((3, "太長了，可以簡短一點嗎？"))

        signals = self.corrections(summary)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].detail["topic"], cues.VERBOSITY)

    def test_the_opening_message_is_never_a_correction(self):
        """Nothing has been answered yet, so there is nothing to correct."""
        summary = self.session((1, "不對，我是說要用 release 子指令。"), answered_at=2)

        self.assertEqual(self.corrections(summary), [])

    def test_a_session_with_no_assistant_turn_yields_nothing(self):
        summary = SessionSummary(
            path="s.jsonl",
            user_messages=[UserMessage(text="不對，重來。", line=1)],
        )

        self.assertEqual(self.corrections(summary), [])

    def test_the_excerpt_is_redacted(self):
        secret = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        summary = self.session((3, f"不對，token 應該是 {secret}"))

        self.assertNotIn(secret, self.corrections(summary)[0].evidence.excerpt)

    def test_several_corrections_in_one_session_are_all_recorded(self):
        summary = self.session(
            (3, "不對，用 release 子指令。"),
            (5, "That's wrong, the flag is --release."),
            (7, "你搞錯了，不要動那個檔案。"),
        )

        self.assertEqual(len(self.corrections(summary)), 3)


if __name__ == "__main__":
    unittest.main()
