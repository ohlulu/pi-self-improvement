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

    def match(self, text: str) -> CueHit | None:
        if not text or self.guarded(text):
            return None
        length = len(text)

        if length <= self.strong_gate:
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

    def extend(self, strong: tuple[str, ...] = (), weak: tuple[str, ...] = (), guards: tuple[str, ...] = ()):
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
