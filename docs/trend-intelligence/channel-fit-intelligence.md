# Trend Intelligence: Channel-Fit Intelligence Layer

## Brand profile configuration

All channel-fit weighting is centralized in:

- `src/trend_intelligence/brand_profile.py`

The `HISTORY_CROSSROADS_BRAND_PROFILE` object controls:

- weighted brand preferences
- baseline brand alignment score
- long-form documentary bonus
- overall topic score weights
- reserved weight for channel-performance lift

To edit behavior, update this single config module.

## Included weighted preferences

The History Crossroads profile now includes weighted preference buckets for:

1. ancient mysteries
2. wartime hero stories
3. forgotten individuals
4. bizarre/unbelievable true stories
5. long-form documentary suitability

Each preference has editable keywords and a weight.

## Future channel-performance integration path

A non-required integration path is now scaffolded via `ChannelPerformanceSnapshot`.

### Current behavior

- No storage dependency is required.
- Pipeline currently passes `channel_performance=None`.
- Scoring remains deterministic and backwards-compatible.

### Future behavior (recommended)

1. Persist per-topic and per-video outcomes (CTR, retention, watch time, returning viewers).
2. Build a repository method that fetches aggregate performance by normalized topic tags.
3. Construct `ChannelPerformanceSnapshot` inside `TrendIntelligencePipelineService._build_topic_result`.
4. Pass the snapshot into `scoreBrandAlignment(...)`.
5. Tune `channel_performance_weight` in `brand_profile.py` after validating on historical runs.

This enables channel-fit intelligence to move from static preference matching to learned channel-specific performance alignment.
