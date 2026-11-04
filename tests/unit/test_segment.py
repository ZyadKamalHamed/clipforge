import pytest

from clipforge.asr.base import ASRResult, RawWord
from clipforge.asr.segment import build_transcript, compute_pauses, is_filler, mark_fillers, segment
from clipforge.models import Transcript, Word

ELLIPSIS = chr(0x2026)


def timed(text: str, *, step: float = 0.4, length: float = 0.3, pauses: dict[int, float] | None = None):
    """Words from a string: each lasts ``length``, the next starts ``step`` later, plus any extra pause
    after word k given in ``pauses``."""
    out, t = [], 0.0
    for i, token in enumerate(text.split()):
        out.append(Word(i=i, text=token, start=round(t, 6), end=round(t + length, 6)))
        t += step + (pauses or {}).get(i, 0.0)
    return out


def sizes(sentences) -> list[int]:
    return [s.w1 - s.w0 + 1 for s in sentences]


# Sentences


def test_segment_splits_on_punctuation():
    words = timed(f'Hello there. How are you? I said "stop." Fine! Great{ELLIPSIS} ok')
    sentences = segment(words)
    assert [s.text for s in sentences] == [
        "Hello there.",
        "How are you?",
        'I said "stop."',
        "Fine!",
        f"Great{ELLIPSIS}",
        "ok",
    ]
    assert [s.id for s in sentences] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert [(s.w0, s.w1) for s in sentences][:2] == [(0, 1), (2, 4)]
    assert (sentences[0].start, sentences[0].end) == (words[0].start, words[1].end)


def test_segment_keeps_titles_like_dr_inside_a_sentence():
    sentences = segment(timed("I met Dr. Smith and Mr. Jones today. They were nice."))
    assert [s.text for s in sentences] == ["I met Dr. Smith and Mr. Jones today.", "They were nice."]


def test_segment_long_sentence_splits_at_longest_pause():
    # 36 words, one sentence, 0.05 s between words, except 0.85 s after word 11 and 0.35 s after word 25.
    words = timed(" ".join(["word"] * 35 + ["end."]), step=0.3, length=0.25, pauses={11: 0.8, 25: 0.3})
    sentences = segment(words)
    assert sizes(sentences) == [12, 24]
    assert sentences[1].w0 == 12


def test_segment_long_sentence_splits_at_comma():
    # No pause over 0.25 s, so the comma nearest the middle wins: after word 16, not word 5 or 30.
    texts = ["word"] * 36
    texts[5], texts[16], texts[30], texts[35] = "five,", "sixteen,", "thirty,", "end."
    sentences = segment(timed(" ".join(texts)))
    assert sizes(sentences) == [17, 19]
    assert sentences[0].text.endswith("sixteen,")


def test_segment_long_sentence_splits_at_a_comma_right_in_the_middle():
    # Commas after word 17 and word 18 of 36: the first leaves two halves of 18.
    texts = ["word"] * 36
    texts[17], texts[18], texts[35] = "seventeen,", "eighteen,", "end."
    assert sizes(segment(timed(" ".join(texts)))) == [18, 18]


def test_segment_long_sentence_splits_at_the_middle_word():
    sentences = segment(timed(" ".join(["word"] * 39 + ["end."])))
    assert sizes(sentences) == [20, 20]


def test_segment_splits_a_sentence_that_runs_over_twelve_seconds():
    # Only 20 words, but a second apart: 19.9 s is too long, and each half of 9.9 s fits.
    sentences = segment(timed(" ".join(["slow"] * 19 + ["end."]), step=1.0, length=0.9))
    assert sizes(sentences) == [10, 10]


def test_segment_keeps_splitting_until_every_piece_fits():
    sentences = segment(timed(" ".join(["word"] * 99 + ["end."])))
    assert all(size <= 30 for size in sizes(sentences)) and sum(sizes(sentences)) == 100
    assert [s.w0 for s in sentences] == sorted(s.w0 for s in sentences)


def test_one_very_long_word_stays_whole():
    [sentence] = segment([Word(i=0, text="Hello.", start=0.0, end=20.0)])
    assert (sentence.w0, sentence.w1, sentence.end) == (0, 0, 20.0)


def test_segment_without_punctuation_uses_pauses():
    words = timed("so this works really well thank you all", pauses={1: 0.4, 4: 0.6})
    sentences = segment(words)
    assert [s.text for s in sentences] == ["so this works really well", "thank you all"]


def test_segment_caps_a_sentence_at_thirty_words():
    # Fast speech, so 31 words take 9.25 s and only the word count can split them.
    assert sizes(segment(timed(" ".join(["word"] * 29 + ["end."]), step=0.3, length=0.25))) == [30]
    assert sizes(segment(timed(" ".join(["word"] * 30 + ["end."]), step=0.3, length=0.25))) == [15, 16]


def test_segment_without_punctuation_still_caps_length():
    sentences = segment(timed(" ".join(["word"] * 45)))
    assert sizes(sentences) == [22, 23]  # the middle word, 23rd of 45, starts the second piece


def test_segment_detects_punctuation_that_is_too_rare_to_trust():
    # One full stop in 61 words is under one per 60, so pauses decide instead.
    text = " ".join(["a"] * 9 + ["stop."] + ["b"] * 51)
    words = timed(text, pauses={30: 0.7})
    assert sizes(segment(words, max_words=100, max_s=100)) == [31, 30]
    assert sizes(segment(words, has_punct=True, max_words=100, max_s=100)) == [10, 51]


def test_segment_of_nothing():
    assert segment([]) == []


def test_segment_marks_the_rest_of_a_split_sentence_as_continuing():
    # 100 words of one sentence split into four pieces of 25; the three after the first carry it on
    sentences = segment(timed(" ".join(["word"] * 99 + ["end.", "Next", "one."])))
    assert sizes(sentences) == [25, 25, 25, 25, 2]
    assert [s.cont for s in sentences] == [False, True, True, True, False]


def test_segment_marks_english_sentences_that_carry_on_the_one_before():
    text = (
        "I had an epiphany. That I don't think anyone is lazy. Which surprised me. Because I was. "
        "Except on Sundays. That would be the plan. Which one is it? That said, fine. That was it."
    )
    marked = [True, True, True, True, False, False, False, False]  # after the first, which never is
    assert [s.cont for s in segment(timed(text), english=True)] == [False, *marked]
    assert [s.cont for s in segment(timed(text))] == [False] * 9  # only English is read this way
    contracted = f"So. That it{chr(0x2019)}s fine. That we're done. That's it. That, I think, is wrong."
    assert [s.cont for s in segment(timed(contracted), english=True)] == [False, True, True, False, False]
    assert [s.cont for s in segment(timed("Which is great. Fine."), english=True)] == [False, False]


# Fillers and pauses


def test_fillers_marked():
    words = mark_fillers(timed("Um, so like, uh you know Uh Ummm Hmm. umbrella"))
    assert [w.text for w in words if w.filler] == ["Um,", "uh", "Uh", "Ummm", "Hmm."]
    assert is_filler("um,") and is_filler("Uh") and is_filler("Erm") and is_filler("ah")
    assert not is_filler("like") and not is_filler("you") and not is_filler("Uh-huh")


def test_an_acronym_is_not_a_filler():
    # "They rushed him to the ER." keeps its last word.
    assert not is_filler("ER.") and not is_filler("UH") and not is_filler("UM,")
    assert is_filler("Er,") and is_filler("I") is False


def test_fillers_are_marked_only_in_english():
    # Plain "um" and "er" are words elsewhere: Portuguese "um carro", German "um acht", Danish "det er".
    portuguese = timed("Eu tenho um carro e er")
    assert not any(w.filler for w in mark_fillers(portuguese, "pt-BR"))
    assert not any(w.filler for w in mark_fillers(timed("Um acht Uhr er kommt"), "de"))
    assert [w.text for w in mark_fillers(portuguese, "en-GB") if w.filler] == ["um", "er"]


def test_fillers_with_an_unknown_language_follow_the_words():
    english = timed("So um I think that the uh plan is good and you know it")
    assert [w.text for w in mark_fillers(english, None) if w.filler] == ["um", "uh"]
    portuguese = timed("Eu tenho um carro novo e ele faz um barulho estranho quando chove")
    assert not any(w.filler for w in mark_fillers(portuguese, None))


def test_pause_after():
    words = compute_pauses(
        [
            Word(i=0, text="a", start=0.0, end=0.5),
            Word(i=1, text="b", start=0.75, end=1.0),
            Word(i=2, text="c", start=0.9, end=1.5),  # starts before b ends: no negative pause
        ]
    )
    assert [w.pause_after for w in words] == pytest.approx([0.25, 0.0, 0.0])


# The transcript


def asr(*words: tuple[str, float, float]) -> ASRResult:
    return ASRResult(
        engine="parakeet",
        engine_version="test",
        language="en",
        words=[RawWord(text=t, start=s, end=e, conf=0.9) for t, s, e in words],
    )


def test_build_transcript():
    result = asr(("Hi", 0.0, 0.4), ("there.", 0.5, 1.0), ("", 1.0, 1.1), ("Um,", 2.0, 2.3), ("go!", 2.5, 3.0))
    transcript = build_transcript(result, 10.0)
    assert [w.text for w in transcript.words] == ["Hi", "there.", "Um,", "go!"]  # the empty word is gone
    assert [w.i for w in transcript.words] == [0, 1, 2, 3]
    assert [w.filler for w in transcript.words] == [False, False, True, False]
    assert transcript.words[1].pause_after == pytest.approx(1.0)
    assert [(s.id, s.text) for s in transcript.sentences] == [("S1", "Hi there."), ("S2", "Um, go!")]
    assert {s.source for s in transcript.sentences} == {"asr"}
    assert (transcript.engine, transcript.language, transcript.duration) == ("parakeet", "en", 10.0)


def test_build_transcript_orders_words_and_keeps_them_inside_the_video():
    result = asr(("later.", 5.0, 5.5), ("First", -0.2, 0.3), ("last.", 9.8, 10.4))
    transcript = build_transcript(result, 10.0)
    assert [(w.text, w.start, w.end) for w in transcript.words] == [
        ("First", 0.0, 0.3),
        ("later.", 5.0, 5.5),
        ("last.", 9.8, 10.0),
    ]


def test_build_transcript_marks_caption_sentences():
    result = asr(("One.", 0.0, 0.5), ("two", 3.0, 3.4), ("three.", 3.5, 4.0), ("Four.", 6.0, 6.5))
    transcript = build_transcript(result, 10.0, caption_ranges=[(2.0, 5.0)])
    assert [s.source for s in transcript.sentences] == ["asr", "captions", "asr"]


def test_build_transcript_marks_continuing_sentences_in_english_only():
    result = asr(("It", 0.0, 0.3), ("worked.", 0.4, 0.8), ("Which", 1.0, 1.3), ("helped.", 1.4, 1.8))
    assert [s.cont for s in build_transcript(result, 5.0).sentences] == [False, True]
    german = result.model_copy(update={"language": "de"})
    assert [s.cont for s in build_transcript(german, 5.0).sentences] == [False, False]
    unknown = result.model_copy(update={"language": None})  # the words decide, as they do for fillers
    assert [s.cont for s in build_transcript(unknown, 5.0).sentences] == [False, True]


def test_transcript_lookups_and_round_trip():
    result = asr(("One.", 0.0, 0.5), ("Two.", 1.0, 1.5), ("Three.", 2.0, 2.5))
    transcript = build_transcript(result, 3.0)
    assert transcript.index("S2") == 1
    assert transcript.sentence("S3").text == "Three."
    assert transcript.text(0, 1) == "One. Two."
    with pytest.raises(KeyError):
        transcript.index("S9")
    with pytest.raises(KeyError):
        transcript.sentence("S01")
    assert Transcript.model_validate_json(transcript.model_dump_json()) == transcript
