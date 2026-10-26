from text_tools import is_palindrome, slugify, truncate_words, word_frequencies


def test_slugify():
    assert slugify("Hello, World! 2024") == "hello-world-2024"


def test_truncate_words_keeps_short_text():
    assert truncate_words("one two three", 3) == "one two three"


def test_truncate_words_cuts_long_text():
    assert truncate_words("one two three four", 2) == "one two..."


def test_is_palindrome():
    assert is_palindrome("A man, a plan, a canal: Panama")
    assert not is_palindrome("PipeGuard")


def test_word_frequencies_ranking():
    text = "the cat and the hat and the bat"
    assert word_frequencies(text) == [("the", 3), ("and", 2), ("bat", 1)]


def test_word_frequencies_top():
    assert word_frequencies("b a b", top=1) == [("b", 2)]
