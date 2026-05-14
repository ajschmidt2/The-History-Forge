from __future__ import annotations

from pathlib import Path

import src.research.image_search as image_search


def test_search_image_for_scene_prefers_relevant_metadata(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        image_search,
        "_wikimedia_search",
        lambda query, limit=5: [
            {
                "title": "Generic wartime crowd",
                "page_url": "https://example.com/generic-crowd",
                "image_url": "https://example.com/generic.png",
                "license": "Public Domain",
            }
        ],
    )
    monkeypatch.setattr(
        image_search,
        "_loc_search",
        lambda query, limit=5: [
            {
                "title": "Nancy Wake portrait in occupied France",
                "page_url": "https://example.com/nancy-wake",
                "image_url": "https://example.com/nancy.png",
                "license": "Public Domain",
            }
        ],
    )
    monkeypatch.setattr(image_search, "_download_image_bytes", lambda url: b"raw")
    monkeypatch.setattr(image_search, "_normalize_image_bytes", lambda raw: b"png")
    monkeypatch.setattr(image_search, "_save_image", lambda image_bytes, dest: dest.write_bytes(image_bytes) or True)

    result = image_search.search_image_for_scene(
        scene_title="Nancy Wake resistance leader",
        scene_description="The resistance leader became one of the most wanted women in occupied France.",
        topic="Nancy Wake",
        era="1940s",
        scene_index=1,
        cache_dir=tmp_path,
        providers=("wikimedia", "loc"),
        verification_level="strict",
    )

    assert result is not None
    assert result.provider == "loc"
    assert result.match_score > 0.45
    assert "matched_terms" in result.verification_notes


def test_search_image_for_scene_sort_handles_equal_scores_with_distinct_candidates(monkeypatch, tmp_path: Path):
    candidates = [
        {
            "title": "Bletchley Park codebreakers 1940s",
            "page_url": "https://example.com/a",
            "image_url": "https://example.com/a.png",
            "license": "Public Domain",
        },
        {
            "title": "Bletchley Park codebreakers 1940s",
            "page_url": "https://example.com/b",
            "image_url": "https://example.com/b.png",
            "license": "Public Domain",
        },
    ]
    monkeypatch.setattr(image_search, "_wikimedia_search", lambda query, limit=5: candidates)
    monkeypatch.setattr(image_search, "_download_image_bytes", lambda url: b"raw")
    monkeypatch.setattr(image_search, "_normalize_image_bytes", lambda raw: b"png")
    monkeypatch.setattr(image_search, "_save_image", lambda image_bytes, dest: dest.write_bytes(image_bytes) or True)

    result = image_search.search_image_for_scene(
        scene_title="Bletchley Park codebreakers",
        scene_description="Codebreakers work inside Bletchley Park during the 1940s.",
        topic="Bletchley Park",
        era="1940s",
        scene_index=1,
        cache_dir=tmp_path,
        providers=("wikimedia",),
        verification_level="standard",
    )

    assert result is not None
    assert result.provider == "wikimedia"
