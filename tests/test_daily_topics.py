from datetime import date

from src.topics.daily_topics import generate_daily_topic, load_used_topics, save_used_topic


def test_used_topics_roundtrip(tmp_path):
    used_path = tmp_path / "used.json"
    assert load_used_topics(used_path) == set()

    save_used_topic("The Dancing Plague", run_date=date(2026, 1, 1), path=used_path)
    loaded = load_used_topics(used_path)
    assert "the dancing plague" in loaded


def test_generate_daily_topic_falls_back_to_non_duplicate(monkeypatch):
    monkeypatch.setattr("src.topics.daily_topics._generate_topic_with_openai", lambda: "")
    used = {"the dancing plague of 1518 that terrified an entire city"}
    topic = generate_daily_topic(used_topics=used)
    assert topic
    assert topic.lower() not in used
