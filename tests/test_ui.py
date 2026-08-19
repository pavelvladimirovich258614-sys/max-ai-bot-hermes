"""Tests for the MAX UI helpers: rich headers, markdown cleaning, chunking."""
import pytest

from app.max.ui import MAX_MESSAGE_LIMIT, chunk_text, clean_for_max, header


def test_header_basic():
    out = header("🔍", "research", ["Введи тему.", "Ещё строка."])
    assert out == "🔍 RESEARCH\n\nВведи тему.\nЕщё строка."


def test_header_uppercases_title():
    assert header("📅", "Контент-план") == "📅 КОНТЕНТ-ПЛАН"


def test_header_no_lines():
    assert header("🏠", "меню") == "🏠 МЕНЮ"


@pytest.mark.parametrize("raw,expected", [
    ("# Заголовок", "📌 Заголовок"),
    ("## Подзаг", "📌 Подзаг"),
    ("**жирный**", "жирный"),
    ("__курсив__", "курсив"),
    ("*один*", "один"),
    ("_один_", "один"),
    ("`код`", "код"),
    ("* пункт", "• пункт"),
    ("- пункт", "• пункт"),
    ("+ пункт", "• пункт"),
    ("> цитата", "цитата"),
    ("---", "────"),
    ("[текст](http://x.ru)", "текст (http://x.ru)"),
])
def test_clean_for_max_single(raw, expected):
    assert clean_for_max(raw) == expected


def test_clean_for_max_preserves_structure():
    md = (
        "# Заголовок\n\n"
        "Обычный абзац с **жирным** и `кодом`.\n\n"
        "* первый\n"
        "* второй\n\n"
        "> важное замечание\n\n"
        "---"
    )
    out = clean_for_max(md)
    assert "#" not in out
    assert "*" not in out
    assert "`" not in out
    assert out.startswith("📌 Заголовок")
    assert "• первый" in out
    assert "• второй" in out
    assert "важное замечание" in out
    assert "────" in out
    assert "жирным" in out and "кодом" in out


def test_clean_for_max_empty():
    assert clean_for_max("") == ""
    assert clean_for_max(None) == ""


def test_chunk_text_short_stays_single():
    text = "короткое сообщение"
    assert chunk_text(text) == [text]


def test_chunk_text_splits_on_newline():
    text = "a" * 100 + "\n" + "b" * 100
    chunks = chunk_text(text, limit=150)
    assert len(chunks) == 2
    assert chunks[0].endswith("a" * 100)
    assert chunks[1] == "b" * 100


def test_chunk_text_hard_split_overlong_line():
    big = "x" * (MAX_MESSAGE_LIMIT + 500)
    chunks = chunk_text(big)
    assert len(chunks) == 2
    assert all(len(c) <= MAX_MESSAGE_LIMIT for c in chunks)


def test_chunk_text_all_chunks_under_limit():
    text = ("строка " * 50 + "\n") * 200
    chunks = chunk_text(text)
    assert chunks
    assert all(len(c) <= MAX_MESSAGE_LIMIT for c in chunks)
    # reassembling on newlines recovers the original
    assert "\n".join(chunks) == text
