"""Tests for the LLM processor's normalization and heuristic fallback.

No network here: with no API key set, safe_analyze and the fallback path handle
everything, so these run offline.
"""

from backend.services.llm_processor import LLMProcessor


def test_normalize_clamps_sentiment_high():
    proc = LLMProcessor()
    out = proc._validate_and_normalize({"sentiment_score": 5.0}, {})
    assert out["sentiment_score"] == 1.0
    assert out["sentiment_label"] == "positive"


def test_normalize_clamps_sentiment_low():
    proc = LLMProcessor()
    out = proc._validate_and_normalize({"sentiment_score": -9.0}, {})
    assert out["sentiment_score"] == -1.0
    assert out["sentiment_label"] == "negative"


def test_normalize_neutral_band():
    proc = LLMProcessor()
    out = proc._validate_and_normalize({"sentiment_score": 0.1}, {})
    assert out["sentiment_label"] == "neutral"


def test_normalize_fixes_bad_entities():
    proc = LLMProcessor()
    out = proc._validate_and_normalize({"entities": "not a dict"}, {})
    assert out["entities"] == {"people": [], "companies": []}


def test_fallback_extracts_structure():
    proc = LLMProcessor()
    text = (
        "Barack Obama met with Tim Cook at Apple Inc to discuss a great new "
        "initiative. The success of the project will benefit many people."
    )
    out = proc._generate_fallback_analysis(text, title="A Meeting")

    assert out["summary"]
    assert -1.0 <= out["sentiment_score"] <= 1.0
    assert "people" in out["entities"]
    assert "companies" in out["entities"]
    # The heuristic marks itself in token_usage so it's distinguishable downstream.
    assert out["token_usage"]["model"] == "fallback-heuristic"


def test_safe_analyze_uses_fallback_without_key():
    proc = LLMProcessor()
    proc.api_key = ""  # no credentials -> heuristic path
    out = proc.safe_analyze("Some article text about a positive breakthrough.", "Title")
    assert out["token_usage"]["model"] == "fallback-heuristic"
    assert out["sentiment_label"] in {"positive", "negative", "neutral"}
