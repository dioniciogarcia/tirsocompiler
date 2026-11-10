"""Android package identifiers (e.g. com.tirso.supermario64)."""
import re
import unicodedata

JAVA_KEYWORDS = set("""abstract assert boolean break byte case catch char class const continue default do
double else enum extends final finally float for goto if implements import instanceof int interface long
native new package private protected public return short static strictfp super switch synchronized this
throw throws transient try void volatile while true false null""".split())

SEGMENT = re.compile(r"^[a-z][a-z0-9_]*$")


def slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    if not text or not text[0].isalpha():
        text = "game" + text
    return text[:40]


def default_for(name: str) -> str:
    return "com.tirso." + slug(name)


def validate(pkg: str):
    """Returns None when valid, else a plain-language reason."""
    if not pkg:
        return "Enter a package ID, for example com.tirso.mygame."
    if len(pkg) > 150:
        return "Package ID is too long."
    parts = pkg.split(".")
    if len(parts) < 2:
        return "Use at least two parts separated by dots, like com.mygame."
    for p in parts:
        if not p:
            return "Remove the empty part (two dots in a row, or a dot at the end)."
        if not SEGMENT.match(p):
            return "'%s' must start with a lowercase letter and use only a–z, 0–9 and _." % p
        if p in JAVA_KEYWORDS:
            return "'%s' is a reserved word in Android and can't be used." % p
    return None
