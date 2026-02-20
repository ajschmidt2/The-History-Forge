from src.script.refine import flag_uncertain_claims


def test_flag_uncertain_claims_appends_notes_section() -> None:
    script = "This empire always won battles. In 1453 exactly 500000 soldiers attacked the city."
    output = flag_uncertain_claims(script, "")

    assert "## Notes to Verify" in output
    assert "Verify" in output
