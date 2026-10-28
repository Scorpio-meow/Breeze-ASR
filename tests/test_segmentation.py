from app.segmentation import Word, split_sentences


def _chars(text: str, start: float, step: float) -> list[Word]:
    return [
        Word(text=ch, start=round(start + i * step, 3), end=round(start + (i + 1) * step, 3))
        for i, ch in enumerate(text)
    ]


def test_splits_at_sentence_punctuation():
    words = _chars("今天天氣很好", 0.0, 0.2) + [Word("。", 1.2, 1.25)] + _chars("我們去公園吧", 1.5, 0.2)
    segments = split_sentences(words, "今天天氣很好。我們去公園吧", max_seconds=10, min_seconds=0.5, audio_seconds=5)
    assert [(s.text, s.start, s.end) for s in segments] == [("今天天氣很好。", 0.0, 1.25), ("我們去公園吧", 1.5, 2.7)]


def test_returns_none_when_words_do_not_match_reference():
    words = _chars("今天天氣", 0.0, 0.2)
    assert split_sentences(words, "明天下雨", max_seconds=10, min_seconds=1, audio_seconds=5) is None


def test_reference_whitespace_is_ignored_when_matching():
    words = _chars("你好", 0.0, 0.3)
    segments = split_sentences(words, "你 好", max_seconds=10, min_seconds=0.1, audio_seconds=5)
    assert [s.text for s in segments] == ["你好"]


def test_short_piece_is_merged_into_neighbour():
    words = [Word("嗯", 0.0, 0.2), Word("。", 0.2, 0.25)] + _chars("今天天氣很好", 0.4, 0.2)
    segments = split_sentences(words, "嗯。今天天氣很好", max_seconds=10, min_seconds=0.5, audio_seconds=5)
    assert [s.text for s in segments] == ["嗯。今天天氣很好"]


def test_long_piece_is_split_under_max_seconds():
    text = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸"
    segments = split_sentences(_chars(text, 0.0, 1.0), text, max_seconds=8, min_seconds=1, audio_seconds=30)
    assert "".join(s.text for s in segments) == text
    assert all(s.end - s.start <= 8 for s in segments)
    assert len(segments) >= 3


def test_english_words_keep_spaces():
    words = [Word(" hello", 0.0, 0.4), Word(" world", 0.45, 0.9)]
    segments = split_sentences(words, " hello world", max_seconds=10, min_seconds=0.1, audio_seconds=5)
    assert [s.text for s in segments] == ["hello world"]


def test_times_are_clamped_to_audio_length():
    segments = split_sentences(_chars("你好", 0.0, 1.0), "你好", max_seconds=10, min_seconds=0.1, audio_seconds=1.5)
    assert segments[-1].end == 1.5


def test_empty_reference_gives_no_segments():
    assert split_sentences([], "", max_seconds=10, min_seconds=1, audio_seconds=5) == []
