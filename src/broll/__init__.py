"""B-roll video provider package for The History Forge.

Provides search, download, and assignment of free stock video clips
from Pexels and Pixabay to complement AI-generated content.
"""

from .models import BrollResult
from .service import (
    assign_broll_to_scene,
    download_broll_asset,
    generate_broll_query_for_scene,
    search_broll_for_scene,
)

__all__ = [
    "BrollResult",
    "assign_broll_to_scene",
    "download_broll_asset",
    "generate_broll_query_for_scene",
    "search_broll_for_scene",
]
