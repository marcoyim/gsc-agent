"""Query, page, and intent labels driven by the user's config."""

from __future__ import annotations

import re

from gsc_agent.config import AnalysisConfig, PositionBand


def classify_brand(query: str, terms: tuple[str, ...]) -> str:
    if not terms:
        return "brand_unknown"
    folded = query.casefold()
    for term in terms:
        if term.casefold() in folded:
            return "brand"
    return "non_brand"


def classify_intent(query: str, analysis: AnalysisConfig) -> str:
    folded = query.casefold()
    has_service = any(term.casefold() in folded for term in analysis.service_intent_terms)
    has_info = any(term.casefold() in folded for term in analysis.informational_terms)
    if has_service and has_info:
        return "mixed"
    if has_service:
        return "service"
    if has_info:
        return "informational"
    return "unspecified"


def classify_page(url: str, analysis: AnalysisConfig) -> str:
    for pattern in analysis.service_url_patterns:
        if re.search(pattern, url):
            return "service"
    for pattern in analysis.article_url_patterns:
        if re.search(pattern, url):
            return "article"
    return "other"


def matching_band(position: float, bands: tuple[PositionBand, ...]) -> PositionBand | None:
    for band in bands:
        if band.min_position <= position <= band.max_position:
            return band
    return None


def country_matches(country: str, target: str) -> bool:
    return country.casefold() == target.casefold()
