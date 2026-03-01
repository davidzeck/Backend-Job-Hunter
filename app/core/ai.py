"""
OpenAI AI helpers for the ATS analysis and CV tailoring layer.

All OpenAI calls go through this module so we can:
  - Centralise API key management and model selection
  - Enforce input truncation (cost control)
  - Validate LLM JSON responses with fallback defaults
  - Wrap errors into structured, sanitized responses

Functions:
  generate_embedding        — single text → 1536-d vector
  generate_embeddings_batch — batch texts → list of vectors
  extract_keywords_from_jd  — JD → structured keyword dict
  analyze_cv_against_jd     — CV + JD → gap analysis
  tailor_cv_section          — CV + JD → rewritten summary/skills
"""
import json
from typing import Any, Dict, List, Optional

import openai
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Hard cap on input sizes to prevent cost abuse.
# text-embedding-3-small supports ~8191 tokens ≈ 30 000 chars.
# gpt-4o-mini context is 128k tokens but we limit to keep costs bounded.
_MAX_EMBEDDING_CHARS = 25_000
_MAX_JD_CHARS = 15_000
_MAX_CV_CHARS = 30_000


def _client() -> AsyncOpenAI:
    """Lazily create an OpenAI async client (lightweight, re-created per call)."""
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Set it as an environment variable."
        )
    return AsyncOpenAI(api_key=settings.openai_api_key)


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending indicator if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _safe_parse_json(raw: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Parse JSON from LLM response with fallback on malformed output."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        logger.warning("llm_json_not_dict", raw_type=type(parsed).__name__)
        return fallback
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("llm_json_parse_error", error=str(exc), raw_head=raw[:200])
        return fallback


# ── Embeddings ────────────────────────────────────────────────────────────────


async def generate_embedding(text: str) -> List[float]:
    """
    Generate a single embedding vector for the given text.
    Returns a 1536-d float list (text-embedding-3-small).
    """
    text = _truncate(text.strip(), _MAX_EMBEDDING_CHARS)
    try:
        client = _client()
        response = await client.embeddings.create(
            model=settings.openai_embedding_model,
            input=text,
        )
        return response.data[0].embedding
    except openai.RateLimitError:
        logger.error("openai_rate_limit", endpoint="embeddings")
        raise
    except openai.APIError as exc:
        logger.error("openai_api_error", endpoint="embeddings", error=str(exc))
        raise RuntimeError("Embedding generation failed") from exc


async def generate_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Batch-embed multiple texts in a single API call.
    OpenAI supports up to 2048 inputs per request.
    Returns list of vectors in the same order as input.
    """
    if not texts:
        return []
    truncated = [_truncate(t.strip(), _MAX_EMBEDDING_CHARS) for t in texts]
    try:
        client = _client()
        response = await client.embeddings.create(
            model=settings.openai_embedding_model,
            input=truncated,
        )
        # Response items are guaranteed same order as input
        return [item.embedding for item in response.data]
    except openai.RateLimitError:
        logger.error("openai_rate_limit", endpoint="embeddings_batch", count=len(texts))
        raise
    except openai.APIError as exc:
        logger.error("openai_api_error", endpoint="embeddings_batch", error=str(exc))
        raise RuntimeError("Batch embedding generation failed") from exc


# ── JD Keyword Extraction ─────────────────────────────────────────────────────


async def extract_keywords_from_jd(job_description: str) -> Dict[str, Any]:
    """
    Extract structured keywords from a job description using gpt-4o-mini.

    Returns:
        {
            "required_skills": ["Python", "FastAPI", ...],
            "preferred_skills": ["Docker", "AWS", ...],
            "experience_level": "mid",
            "key_responsibilities": ["Build APIs", ...]
        }
    """
    jd = _truncate(job_description.strip(), _MAX_JD_CHARS)
    fallback = {
        "required_skills": [],
        "preferred_skills": [],
        "experience_level": "unknown",
        "key_responsibilities": [],
    }

    try:
        client = _client()
        response = await client.chat.completions.create(
            model=settings.openai_chat_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an ATS keyword extraction engine. "
                        "Extract skills, technologies, and requirements from the job description. "
                        "Return ONLY a JSON object with these exact keys:\n"
                        '  "required_skills": array of strings (hard requirements),\n'
                        '  "preferred_skills": array of strings (nice-to-haves),\n'
                        '  "experience_level": string ("entry", "mid", "senior", or "lead"),\n'
                        '  "key_responsibilities": array of short strings (max 5).\n'
                        "Do not include any markdown, code fences, or explanation."
                    ),
                },
                {"role": "user", "content": jd},
            ],
            temperature=0.0,
            max_tokens=settings.openai_max_tokens_analysis,
        )
        raw = response.choices[0].message.content or ""
        result = _safe_parse_json(raw, fallback)
        # Enforce list types
        for key in ("required_skills", "preferred_skills", "key_responsibilities"):
            if not isinstance(result.get(key), list):
                result[key] = fallback[key]
        return result

    except openai.RateLimitError:
        logger.error("openai_rate_limit", endpoint="extract_keywords")
        raise
    except openai.APIError as exc:
        logger.error("openai_api_error", endpoint="extract_keywords", error=str(exc))
        raise RuntimeError("Keyword extraction failed") from exc


# ── CV Analysis ───────────────────────────────────────────────────────────────


async def analyze_cv_against_jd(
    cv_text: str,
    job_description: str,
    jd_keywords: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Perform ATS gap analysis: compare CV text against JD keywords.

    Returns:
        {
            "match_score": 0.72,
            "present_keywords": ["Python", "FastAPI"],
            "missing_keywords": ["Docker", "Kubernetes"],
            "suggested_additions": ["Add Docker experience...", ...]
        }
    """
    cv = _truncate(cv_text.strip(), _MAX_CV_CHARS)
    jd = _truncate(job_description.strip(), _MAX_JD_CHARS)
    fallback = {
        "match_score": 0.0,
        "present_keywords": [],
        "missing_keywords": [],
        "suggested_additions": [],
    }

    try:
        client = _client()
        response = await client.chat.completions.create(
            model=settings.openai_chat_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an ATS resume analysis engine. "
                        "Compare the CV against the job description and its extracted keywords. "
                        "Score the match from 0.0 to 1.0 based on keyword overlap and relevance. "
                        "Return ONLY a JSON object with these exact keys:\n"
                        '  "match_score": float between 0.0 and 1.0,\n'
                        '  "present_keywords": array of skills found in BOTH the CV and JD,\n'
                        '  "missing_keywords": array of JD skills ABSENT from the CV,\n'
                        '  "suggested_additions": array of max 5 actionable suggestions '
                        "to improve the CV for this role.\n"
                        "Do not include any markdown, code fences, or explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"## Job Description Keywords\n{json.dumps(jd_keywords)}\n\n"
                        f"## Job Description\n{jd}\n\n"
                        f"## CV Text\n{cv}"
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=settings.openai_max_tokens_analysis,
        )
        raw = response.choices[0].message.content or ""
        result = _safe_parse_json(raw, fallback)

        # Validate and clamp
        score = result.get("match_score", 0.0)
        if not isinstance(score, (int, float)):
            score = 0.0
        result["match_score"] = max(0.0, min(1.0, float(score)))

        for key in ("present_keywords", "missing_keywords"):
            if not isinstance(result.get(key), list):
                result[key] = []

        # Cap suggestions to 5
        suggestions = result.get("suggested_additions", [])
        if not isinstance(suggestions, list):
            suggestions = []
        result["suggested_additions"] = suggestions[:5]

        return result

    except openai.RateLimitError:
        logger.error("openai_rate_limit", endpoint="analyze_cv")
        raise
    except openai.APIError as exc:
        logger.error("openai_api_error", endpoint="analyze_cv", error=str(exc))
        raise RuntimeError("CV analysis failed") from exc


# ── CV Tailoring ──────────────────────────────────────────────────────────────


async def tailor_cv_section(
    cv_text: str,
    job_description: str,
    missing_keywords: List[str],
) -> Dict[str, Any]:
    """
    Rewrite CV summary and skills to better match a JD.
    NEVER fabricates work history or experience.

    Returns:
        {
            "tailored_summary": "...",
            "tailored_skills": ["Skill1", ...],
            "keywords_added": ["Docker", ...],
            "original_summary": "..."
        }
    """
    cv = _truncate(cv_text.strip(), _MAX_CV_CHARS)
    jd = _truncate(job_description.strip(), _MAX_JD_CHARS)
    fallback = {
        "tailored_summary": "",
        "tailored_skills": [],
        "keywords_added": [],
        "original_summary": "",
    }

    try:
        client = _client()
        response = await client.chat.completions.create(
            model=settings.openai_chat_model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional CV writer. Rewrite ONLY the "
                        "summary/profile section and the skills list to better match "
                        "the job description.\n\n"
                        "CRITICAL RULES — violations are unacceptable:\n"
                        "1. NEVER fabricate experience, certifications, or job history.\n"
                        "2. NEVER add skills the candidate does not plausibly have "
                        "based on their CV.\n"
                        "3. Only rephrase existing experience using JD-aligned language.\n"
                        "4. Naturally incorporate missing keywords where truthful.\n"
                        "5. Preserve the candidate's authentic voice and tone.\n\n"
                        "Return ONLY a JSON object with these exact keys:\n"
                        '  "tailored_summary": string (the rewritten summary),\n'
                        '  "tailored_skills": array of strings (the rewritten skills list),\n'
                        '  "keywords_added": array of missing keywords that were incorporated,\n'
                        '  "original_summary": string (the original summary extracted from the CV).\n'
                        "Do not include any markdown, code fences, or explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"## Missing Keywords to Incorporate (if truthful)\n"
                        f"{json.dumps(missing_keywords)}\n\n"
                        f"## Job Description\n{jd}\n\n"
                        f"## Full CV Text\n{cv}"
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=settings.openai_max_tokens_tailor,
        )
        raw = response.choices[0].message.content or ""
        result = _safe_parse_json(raw, fallback)

        # Validate types
        if not isinstance(result.get("tailored_summary"), str):
            result["tailored_summary"] = fallback["tailored_summary"]
        if not isinstance(result.get("original_summary"), str):
            result["original_summary"] = fallback["original_summary"]
        for key in ("tailored_skills", "keywords_added"):
            if not isinstance(result.get(key), list):
                result[key] = []

        return result

    except openai.RateLimitError:
        logger.error("openai_rate_limit", endpoint="tailor_cv")
        raise
    except openai.APIError as exc:
        logger.error("openai_api_error", endpoint="tailor_cv", error=str(exc))
        raise RuntimeError("CV tailoring failed") from exc
