"""Correction cue packs (REQ-010, DEC-008).

A cue pack is one language's way of saying "you got that wrong". Each pack owns
its own cues, its own length gates and its own negative guards, because the
mechanics differ: English needs word boundaries, Chinese has no word delimiters
and needs substring matching with guards to stay precise.

Bilingual detection is the point of this rewrite, not a nicety. Roughly 90% of
the author's user messages contain CJK, and an English-only cue set finds
essentially nothing in them.

Two ideas keep precision up:

- **Strength.** A strong cue ("that's wrong", 不對) means correction wherever it
  appears in a normal-length message. A weak cue ("instead", 應該) means it only
  in a short, reactive message — in a long one it is usually just prose or, worse,
  a pasted document.
- **Guards.** A pack may list phrases that make the whole message ineligible.
  沒錯 ("that's right") contains 錯 ("wrong") and would otherwise read as a
  correction.

A third idea keeps *recall* up for one class of correction the first two miss:

- **Topics.** Some corrections are about how the agent writes, not what it did:
  「冗詞贅字太多」, "too wordy". None of the strong cues above appear in them,
  and they are often phrased as questions (「可以簡短一點嗎？」), so the
  interrogative guard would drop the ones that do carry a weak cue. A topic is
  a regex set that names the complaint; a hit is as strong as a strong cue and
  carries the topic name so routing can group it across projects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STRONG = "strong"
WEAK = "weak"


@dataclass(frozen=True)
class CueHit:
    pack: str
    cue: str
    strength: str
    #: Set when a topic cue matched — names what the user objected to.
    topic: str | None = None


@dataclass(frozen=True)
class TopicCues:
    """Regexes naming one kind of complaint, e.g. `verbosity`.

    Regexes rather than substrings because the ambiguous words need context:
    太長 ("too long") is a layout complaint about a kitty tab, a timeout or a
    branch just as often as it is about prose, so it only counts next to a
    prose noun or a request to shorten.
    """

    name: str
    patterns: tuple[str, ...]
    #: Clauses blanked out before `patterns` run. 「要完整，但不囉唆」 and
    #: 「但不是要你寫的落落長」 qualify a request; they do not correct an answer.
    qualifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CuePack:
    """One language's correction cues.

    `word_boundary` controls matching: English cues are wrapped in `\\b` so that
    "instead" does not fire inside "insteadof"; CJK has no word delimiters, so
    those packs match on substrings.
    """

    name: str
    strong: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()
    strong_gate: int = 2000
    weak_gate: int = 400
    guards: tuple[str, ...] = ()
    word_boundary: bool = True
    line_anchored_weak: tuple[str, ...] = ()
    #: Regexes marking the message as a question. Applied to **weak cues only**:
    #: 為什麼你 and "why did you" are strong cues that are themselves questions,
    #: so a blanket guard would delete two rows of the DEC-008 table.
    interrogatives: tuple[str, ...] = ()
    #: Checked before strong cues, within the strong gate, never interrogative-guarded.
    topics: tuple[TopicCues, ...] = ()

    _compiled: dict = field(default_factory=dict, compare=False, repr=False)
    _interrogative: list = field(default_factory=list, compare=False, repr=False)

    def _patterns(self, cues: tuple[str, ...], anchored: bool = False) -> list[re.Pattern]:
        key = (cues, anchored)
        if key not in self._compiled:
            flags = re.IGNORECASE | (re.MULTILINE if anchored else 0)
            prefix = "^\\s*" if anchored else (r"\b" if self.word_boundary else "")
            suffix = "" if anchored else (r"\b" if self.word_boundary else "")
            self._compiled[key] = [
                re.compile(prefix + re.escape(cue) + suffix, flags) for cue in cues
            ]
        return self._compiled[key]

    def guarded(self, text: str) -> bool:
        """True when a guard phrase makes the whole message ineligible."""
        return any(pattern.search(text) for pattern in self._patterns(self.guards))

    def interrogative(self, text: str) -> bool:
        """True when the message asks rather than corrects.

        Measured on the real corpus: 16 of 42 weak-cue hits were questions such as
        「應該完成了？」 ("is it done?"). The same word in a declarative sentence
        — 「應該要是斜斜的」 ("it should be slanted") — is a correction, so what
        separates them is the sentence mood, not the cue.
        """
        if self.interrogatives and not self._interrogative:
            self._interrogative.extend(
                re.compile(pattern, re.IGNORECASE | re.MULTILINE) for pattern in self.interrogatives
            )
        return any(pattern.search(text) for pattern in self._interrogative)

    def _topic_patterns(self, topic: TopicCues) -> list[re.Pattern]:
        key = ("topic", topic.name, topic.patterns)
        if key not in self._compiled:
            self._compiled[key] = [
                re.compile(pattern, re.IGNORECASE | re.MULTILINE) for pattern in topic.patterns
            ]
        return self._compiled[key]

    def _without_qualifiers(self, topic: TopicCues, text: str) -> str:
        key = ("qualifiers", topic.name, topic.qualifiers)
        if key not in self._compiled:
            self._compiled[key] = [re.compile(pattern, re.MULTILINE) for pattern in topic.qualifiers]
        for pattern in self._compiled[key]:
            text = pattern.sub(" ", text)
        return text

    def match(self, text: str) -> CueHit | None:
        if not text or self.guarded(text):
            return None
        length = len(text)

        if length <= self.strong_gate:
            for topic in self.topics:
                stripped = self._without_qualifiers(topic, text)
                for cue, pattern in zip(topic.patterns, self._topic_patterns(topic)):
                    if pattern.search(stripped):
                        return CueHit(pack=self.name, cue=cue, strength=STRONG, topic=topic.name)
            for cue, pattern in zip(self.strong, self._patterns(self.strong)):
                if pattern.search(text):
                    return CueHit(pack=self.name, cue=cue, strength=STRONG)

        if length <= self.weak_gate and not self.interrogative(text):
            for cue, pattern in zip(self.weak, self._patterns(self.weak)):
                if pattern.search(text):
                    return CueHit(pack=self.name, cue=cue, strength=WEAK)
            for cue, pattern in zip(
                self.line_anchored_weak, self._patterns(self.line_anchored_weak, anchored=True)
            ):
                if pattern.search(text):
                    return CueHit(pack=self.name, cue=cue, strength=WEAK)
        return None

    def extend(
        self,
        strong: tuple[str, ...] = (),
        weak: tuple[str, ...] = (),
        guards: tuple[str, ...] = (),
        topics: dict | None = None,
    ):
        return CuePack(
            name=self.name,
            strong=self.strong + tuple(strong),
            weak=self.weak + tuple(weak),
            strong_gate=self.strong_gate,
            weak_gate=self.weak_gate,
            guards=self.guards + tuple(guards),
            word_boundary=self.word_boundary,
            line_anchored_weak=self.line_anchored_weak,
            interrogatives=self.interrogatives,
            topics=_merge_topics(self.topics, topics or {}),
        )


def _merge_topics(base: tuple[TopicCues, ...], extra: dict) -> tuple[TopicCues, ...]:
    """Config `topics` extends a built-in topic's patterns or adds a new topic."""
    merged = {topic.name: topic for topic in base}
    for name, patterns in extra.items():
        current = merged.get(name, TopicCues(name=name, patterns=()))
        merged[name] = TopicCues(
            name=name, patterns=current.patterns + tuple(patterns), qualifiers=current.qualifiers
        )
    return tuple(merged.values())


VERBOSITY = "verbosity"

#: Prose nouns that disambiguate 太長 / "too long" from layout, time and git.
_ZH_PROSE = "回覆|文案|描述|說明|段|句子|comment|註解|規則|description|body|summary|摘要|報告|文件"
_ZH_SHORTEN = "短一點|簡短|精簡|簡潔|簡化|剪短|挑重點|講重點|說重點|有必要嗎|多餘|不用這麼|不需要這麼"
_EN_PROSE = (
    "reply|response|answer|description|comment|summary|message|paragraph|copy|prose|"
    "explanation|section|body|docstring|commit message|pr description"
)
_EN_SHORTEN = "shorter|shorten|concise|brief|trim|cut it|condense|tighten|tl;?dr"

VERBOSITY_ZH = TopicCues(
    name=VERBOSITY,
    # A 但不… / 但別… clause runs to the next punctuation mark.
    qualifiers=(r"但(?:不|別)[^，。！？；）)\n]*",),
    patterns=(
        # 冗於 is an observed typo for 冗餘. 冗餘 itself is left out of this list:
        # in a code discussion it means a redundant field, not redundant prose.
        r"冗[長詞句言贅於]",
        r"冗[餘余]的?(?:內容|文字|句子|段落|說明|描述|註解)",
        r"(?:內容|文字|句子|段落|說明|描述|註解).{0,6}?冗[餘余]",
        r"贅[字詞述言]|累贅",
        "囉唆",
        "囉嗦",
        "啰嗦",
        "廢話",
        "鋪陳",
        "長篇大論",
        "落落長",
        "嘮叨",
        "拖泥帶水",
        "廢字",
        # 多餘 only about a unit of prose: 「shot2 太多餘」 is a screenshot.
        r"(?:句|段|行|註解|說明|描述|提示|comment).{0,10}?多餘",
        "太長一段",
        "簡短一點",
        r"不夠(?:白話|簡潔|精簡)",
        r"保持(?:簡潔|精簡)",
        r"精簡一(?:下|點)",
        r"不(?:用|需要|必)(?:這麼|那麼)長",
        r"太(?:多|複雜)的?(?:字|描述|說明|換行)",
        rf"太長.{{0,40}}?(?:{_ZH_SHORTEN})",
        rf"(?:{_ZH_PROSE}).{{0,20}}?太長",
    ),
)

VERBOSITY_EN = TopicCues(
    name=VERBOSITY,
    patterns=(
        r"\b(?:too|so|very|overly|way too) (?:verbose|wordy|long-winded)\b",
        r"\b(?:wordy|long-winded)\b",
        r"\bwall of text\b",
        r"\brambling\b",
        r"\btoo much detail\b",
        r"\btoo much (?:text|prose|fluff|filler)\b",
        # "make it shorter" is left out on purpose: like 短一點 it is as often
        # about a UI element or an injected block as about the agent's prose.
        r"\b(?:be|keep it) (?:more )?(?:concise|brief|short|terse)\b",
        r"\bshorten (?:it|this|that)\b",
        rf"\btoo long\b.{{0,40}}?\b(?:{_EN_SHORTEN})\b",
        rf"\b(?:{_EN_PROSE})\b.{{0,20}}?\btoo long\b",
    ),
)


EN = CuePack(
    name="en",
    strong=(
        "that's wrong",
        "thats wrong",
        "not what i asked",
        "you missed",
        "never do",
        "stop doing",
        "you should have",
        "why did you",
        "i meant",
        "remember this",
    ),
    weak=("instead", "don't do", "do not", "should have"),
    line_anchored_weak=("actually",),
    interrogatives=(r"\?\s*$",),
    topics=(VERBOSITY_EN,),
    strong_gate=2000,
    weak_gate=400,
    word_boundary=True,
)

ZH_HANT = CuePack(
    name="zh-Hant",
    strong=("不對", "不是這樣", "你搞錯", "我是說", "我的意思是", "為什麼你", "你應該先", "記住"),
    weak=("應該", "改成", "不要", "直接", "重來"),
    # 沒錯 contains 錯; without these, agreement reads as correction.
    guards=("沒錯", "不錯", "沒問題", "還不錯"),
    interrogatives=(r"[?？]\s*$", r"嗎[?？]?\s*$", r"呢[?？]?\s*$", r"吧[?？]\s*$", r"^\s*(?:想問|請問)"),
    # Chinese carries roughly twice the information per character, so the gates
    # are tighter than the English ones for the same amount of meaning.
    topics=(VERBOSITY_ZH,),
    strong_gate=1000,
    weak_gate=150,
    word_boundary=False,
)

BUILTIN_PACKS = (EN, ZH_HANT)


def build_packs(overrides: dict | None = None) -> tuple[CuePack, ...]:
    """Apply config `cue_packs` (enable/disable/extend) to the built-in packs."""
    overrides = overrides or {}
    packs: list[CuePack] = []
    for pack in BUILTIN_PACKS:
        settings = overrides.get(pack.name, {})
        if settings.get("enabled") is False:
            continue
        packs.append(
            pack.extend(
                strong=tuple(settings.get("strong", ())),
                weak=tuple(settings.get("weak", ())),
                guards=tuple(settings.get("guards", ())),
                topics=settings.get("topics"),
            )
        )
    for name, settings in overrides.items():
        if any(pack.name == name for pack in BUILTIN_PACKS) or settings.get("enabled") is False:
            continue
        packs.append(
            CuePack(
                name=name,
                strong=tuple(settings.get("strong", ())),
                weak=tuple(settings.get("weak", ())),
                guards=tuple(settings.get("guards", ())),
                strong_gate=int(settings.get("strong_gate", 2000)),
                weak_gate=int(settings.get("weak_gate", 400)),
                word_boundary=bool(settings.get("word_boundary", True)),
                topics=_merge_topics((), settings.get("topics") or {}),
            )
        )
    return tuple(packs)


def find_cue(text: str, packs: tuple[CuePack, ...] = BUILTIN_PACKS) -> CueHit | None:
    """First cue hit across `packs`, or None.

    A guard in *any* pack vetoes the message: a bilingual user writes 沒錯 in an
    otherwise English sentence, and that is still agreement.
    """
    if not text:
        return None
    for pack in packs:
        if pack.guarded(text):
            return None
    for pack in packs:
        hit = pack.match(text)
        if hit is not None:
            return hit
    return None
