"""The one place text is normalised.

Every comparison in this codebase runs both sides through these functions. When
normalisation lived in two modules they drifted, accents were deleted instead of
folded, and 'Société Générale' stopped matching 'societegenerale' — which would
have silently thrown away 74 of 75 French articles.

NFKD decomposition separates a letter from its accent; dropping only the
combining marks folds é to e rather than deleting the character.
"""

import re
import unicodedata

EXACT = "exact"
FUZZY = "fuzzy"
NONE = "none"

# Legal form and other words that carry no identifying information.
NOISE_WORDS = {
    "limited", "ltd", "plc", "llp", "lp", "cic", "company", "co", "holdings",
    "holding", "group", "groupe", "uk", "gb", "international", "intl", "the",
    "and", "incorporated", "inc", "corporation", "corp", "sa", "se", "nv", "bv",
    "gmbh", "ag", "spa", "srl",
}

GENERIC_DOMAIN_LABELS = {
    "co", "com", "org", "net", "gov", "edu", "ltd", "plc", "group", "holdings",
    "hotels", "hotel", "mail", "email", "www",
}


def fold_accents(text):
    """'Société Générale' -> 'societe generale'. Folds, never deletes."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def normalise_name(name):
    """'CORINTHIA HOTELS (UK) LIMITED' -> 'corinthia hotels'.

    Ampersands and hyphens become separators, so 'Millennium & Copthorne' and
    'Millennium-Copthorne' normalise identically.
    """
    text = re.sub(r"[^a-z0-9\s]", " ", fold_accents(name))
    # Single characters are fragments of split legal forms — S.A., N.V., S.p.A.
    return " ".join(w for w in text.split() if len(w) > 1 and w not in NOISE_WORDS)


def tokens(name):
    return [w for w in normalise_name(name).split() if w]


def squash(text):
    """Letters and digits only, accents folded first."""
    return re.sub(r"[^a-z0-9]", "", fold_accents(text))


def brand_token(domain):
    """'mfrench@corinthia.com' -> 'corinthia'. None when the domain says nothing."""
    if not domain:
        return None
    host = fold_accents(domain.split("@")[-1].strip()).removeprefix("www.")
    labels = [l for l in host.split(".") if l and l not in GENERIC_DOMAIN_LABELS]
    return labels[0] if labels else None


def matches(text, token):
    """Boolean form of the squashed comparison."""
    return bool(token) and squash(token) in squash(text)


def classify_match(text, account=None, domain_token=None):
    """Tag how strongly a piece of text names this account.

    Returned as an annotation, never used to discard: a normalisation bug should
    degrade confidence in a verdict, not silently delete the evidence behind it.
    """
    haystack = squash(text)
    if not haystack:
        return NONE

    if domain_token and squash(domain_token) in haystack:
        return EXACT

    if account:
        if squash(account) and squash(account) in haystack:
            return EXACT

        account_tokens = tokens(account)
        if account_tokens:
            text_tokens = set(tokens(text))
            hits = sum(1 for t in account_tokens if t in text_tokens)
            if hits == len(account_tokens):
                return EXACT
            if hits:
                return FUZZY

    return NONE
