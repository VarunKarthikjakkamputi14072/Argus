"""
LLM integration service for article analysis.

Processes scraped article text through an LLM API and returns structured
JSON containing: summary, extracted entities, and sentiment score.
"""

import json
import httpx
import pybreaker
from typing import Optional

from backend.config import get_settings

settings = get_settings()
llm_breaker = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=60)

SYSTEM_PROMPT = """You are an expert content analyst. Analyze the provided article text and return a JSON object with exactly this structure:

{
  "summary": "A concise 3-sentence summary of the article's main points.",
  "entities": {
    "people": ["list of person names mentioned"],
    "companies": ["list of company/organization names mentioned"]
  },
  "sentiment_score": 0.0
}

Rules:
- summary: Exactly 3 sentences capturing the key information.
- entities.people: Extract all named individuals. Empty list if none found.
- entities.companies: Extract all organizations, companies, institutions. Empty list if none found.
- sentiment_score: A float from -1.0 (very negative) to 1.0 (very positive), where 0.0 is neutral.

Return ONLY valid JSON. No markdown, no explanation."""


class LLMProcessor:
    def __init__(self):
        self.api_url = settings.llm_api_url
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = 60.0

    async def analyze_article(self, text: str, title: Optional[str] = None) -> dict:
        truncated = text[:8000] if len(text) > 8000 else text
        user_content = f"Title: {title}\n\nArticle:\n{truncated}" if title else truncated

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.api_url, json=payload, headers=headers
            )
            response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)

        token_usage = data.get("usage", {})

        return self._validate_and_normalize(result, token_usage)

    @llm_breaker
    def analyze_article_sync(self, text: str, title: Optional[str] = None) -> dict:
        truncated = text[:8000] if len(text) > 8000 else text
        user_content = f"Title: {title}\n\nArticle:\n{truncated}" if title else truncated

        if not self.api_key or self.api_key == "demo-key":
            return self._generate_fallback_analysis(truncated, title)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.api_url, json=payload, headers=headers)
            response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)

        token_usage = data.get("usage", {})

        return self._validate_and_normalize(result, token_usage)

    def safe_analyze(self, text: str, title: Optional[str] = None) -> dict:
        try:
            return self.analyze_article_sync(text, title)
        except pybreaker.CircuitBreakerError:
            return self._generate_fallback_analysis(text, title)
        except Exception:
            return self._generate_fallback_analysis(text, title)

    def _validate_and_normalize(self, result: dict, token_usage: dict) -> dict:
        summary = result.get("summary", "")
        entities = result.get("entities", {})
        sentiment_score = result.get("sentiment_score", 0.0)

        if not isinstance(entities, dict):
            entities = {"people": [], "companies": []}
        entities.setdefault("people", [])
        entities.setdefault("companies", [])

        sentiment_score = max(-1.0, min(1.0, float(sentiment_score)))

        if sentiment_score > 0.3:
            sentiment_label = "positive"
        elif sentiment_score < -0.3:
            sentiment_label = "negative"
        else:
            sentiment_label = "neutral"

        return {
            "summary": summary,
            "entities": entities,
            "sentiment_score": sentiment_score,
            "sentiment_label": sentiment_label,
            "model_used": self.model,
            "token_usage": token_usage,
        }


    def _generate_fallback_analysis(self, text: str, title: Optional[str] = None) -> dict:
        """
        Fallback analysis when no valid API key is configured.
        Uses basic heuristics to produce structured output for demo/testing.
        """
        import re

        sentences = re.split(r'(?<=[.!?])\s+', text[:2000])
        summary_sentences = [s.strip() for s in sentences[:3] if len(s.strip()) > 20]
        summary = " ".join(summary_sentences[:3]) if summary_sentences else "Article content extracted successfully."

        capitalized = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text[:4000])
        names = list(set(capitalized[:10]))

        company_patterns = re.findall(
            r'\b([A-Z][a-z]*(?:\s+[A-Z][a-z]*)*(?:\s+(?:Inc|Corp|LLC|Ltd|Co|Group|Foundation))?)\b',
            text[:4000]
        )
        companies = list(set([c for c in company_patterns if len(c) > 5]))[:5]

        positive_words = len(re.findall(r'\b(good|great|excellent|innovative|success|improve|benefit|advance)\b', text.lower()))
        negative_words = len(re.findall(r'\b(bad|poor|fail|problem|risk|danger|concern|issue|difficult)\b', text.lower()))
        total = positive_words + negative_words
        if total > 0:
            score = round((positive_words - negative_words) / total * 0.8, 3)
        else:
            score = 0.0

        result = {
            "summary": summary,
            "entities": {"people": names[:5], "companies": companies},
            "sentiment_score": score,
        }
        return self._validate_and_normalize(result, {"model": "fallback-heuristic", "note": "demo mode"})


llm_processor = LLMProcessor()
