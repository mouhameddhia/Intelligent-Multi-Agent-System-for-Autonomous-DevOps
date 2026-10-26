"""Text helpers for the blog engine."""
import re


def slugify(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "-".join(words)


def truncate_words(text, limit):
    """Keep at most `limit` words, adding '...' when the text was cut."""
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + "..."


def is_palindrome(text):
    letters = [c for c in text.lower() if c.isalnum()]
    return letters == letters[::-1]


def word_frequencies(text, top=3):
    """The `top` most frequent words, ties broken alphabetically."""
    counts = {}
    for word in re.findall(r"[a-z']+", text.lower()):
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:top]
