from src.research.web_research import Source, _extract_search_results, summarize_sources


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


def test_extract_search_results_supports_duckduckgo_lite_and_redirect_links() -> None:
    html = (
        '<a class="result-link" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">'
        "Example Result"
        "</a>"
        '<td class="result-snippet">A short summary snippet.</td>'
    )

    results = _extract_search_results(html, max_results=6)

    assert len(results) == 1
    assert results[0].title == "Example Result"
    assert results[0].url == "https://example.com/page"
    assert results[0].snippet == "A short summary snippet."
