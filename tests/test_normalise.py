"""Normalisation tests.

Round-trip assertions: for each account, the token form the domain yields must
match the name form the sources actually return. These are the exact cases that
broke before — accents deleted rather than folded.
"""

import pytest

from sources._normalise import (
    EXACT, FUZZY, NONE, brand_token, classify_match, fold_accents, matches,
    normalise_name, squash, tokens,
)


def test_fold_accents_folds_rather_than_deletes():
    assert fold_accents("Société Générale") == "societe generale"
    assert fold_accents("Régie de L'Eau Bordeaux Métropole") == "regie de l'eau bordeaux metropole"
    assert "socit" not in fold_accents("Société")


def test_normalise_strips_legal_forms_and_punctuation():
    assert normalise_name("CORINTHIA HOTELS (UK) LIMITED") == "corinthia hotels"
    assert normalise_name("SOCIÉTÉ GÉNÉRALE S.A.") == "societe generale"
    assert normalise_name("HILTON & CO ACCOUNTANTS LTD") == "hilton accountants"


def test_ampersand_and_hyphen_normalise_identically():
    assert normalise_name("Millennium & Copthorne") == normalise_name("Millennium-Copthorne")
    assert normalise_name("Millennium & Copthorne") == "millennium copthorne"
    assert tokens("Millennium & Copthorne") == ["millennium", "copthorne"]


@pytest.mark.parametrize("account,domain,headline", [
    ("Société Générale", "gmontpellier@societegenerale.com",
     "Société Générale va supprimer 1 800 emplois d'ici 2027"),
    ("Régie de L'Eau Bordeaux Métropole", "ldanet@régie.com",
     "La Régie de L'Eau Bordeaux Métropole publie ses comptes"),
    ("Corinthia Hotels", "mfrench@corinthia.com",
     "Orion completes acquisition of majority stake in Corinthia Hotel Lisbon"),
    ("Aman", "lkong@aman.com",
     "Aman secured a $500 million investment from Shinsegae"),
    ("Millennium & Copthorne", "x@millenniumhotels.co.uk",
     "Millennium & Copthorne board change"),
])
def test_round_trip_domain_token_matches_real_headline(account, domain, headline):
    token = brand_token(domain)
    assert classify_match(headline, account, token) in (EXACT, FUZZY)


def test_societe_generale_token_round_trip():
    token = brand_token("gmontpellier@societegenerale.com")
    assert token == "societegenerale"
    assert squash("Société Générale") == token
    assert matches("La Société Générale cède son activité", token)


def test_accented_domain_yields_folded_token():
    assert brand_token("ldanet@régie.com") == "regie"
    assert matches("Régie de L'Eau Bordeaux Métropole", "regie")


def test_partial_name_is_fuzzy_not_none():
    """'Millennium Hotels' vs a Millennium & Copthorne story: related, not exact."""
    assert classify_match("Millennium & Copthorne board change", "Millennium Hotels") == FUZZY


def test_unrelated_text_is_none():
    assert classify_match("Essex fish and chip shop shortlisted", "Corinthia Hotels", "corinthia") == NONE
    assert classify_match("Taiwan Semiconductor Trading Up 2.5%", "Hilton", "hilton") == NONE


def test_empty_text_is_none():
    assert classify_match("", "Corinthia Hotels", "corinthia") == NONE


def test_domain_token_alone_is_enough_for_exact():
    assert classify_match("Corinthia sells big stake in Lisbon", None, "corinthia") == EXACT
