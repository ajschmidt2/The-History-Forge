from src.research.web_research import Source, summarize_sources


def test_summarize_sources_includes_citations_and_source_list() -> None:
    sources = [
        Source(title="Alpha", url="https://example.com/a", snippet="In 1914 a major event began in Europe."),
        Source(title="Beta", url="https://example.com/b", snippet="By 1918 the conflict had reshaped borders."),
    ]

    brief = summarize_sources("World War I", sources)

    assert "## Key Facts" in brief
    assert "[1]" in brief
    assert "[2]" in brief
    assert "## Sources" in brief
    assert "[1] Alpha — https://example.com/a" in brief
    assert "[2] Beta — https://example.com/b" in brief
