from __future__ import annotations

from ngpvan_mcp.evals.record import main as record_main
from ngpvan_mcp.evals.replay import main as replay_main


def test_replay_shows_the_story_and_no_unmasked_value_in_the_model_column(tmp_path, capsys):
    out = tmp_path / "rec.json"
    assert record_main(["--provider", "scripted", "--out", str(out)]) == 0
    capsys.readouterr()
    assert replay_main([str(out), "--speed", "0", "--width", "140"]) == 0
    text = capsys.readouterr().out
    for expected in ("what VAN returned", "what the model saw", "write blocked", "dry run: nothing sent",
                     "audit log", "scripted plan, not a model", "Who's on tomorrow's shift?"):
        assert expected in text
    # The raw column shows the fake number; the model column only yes/no flags.
    assert "9705550103" in text and "has_cell_phone" in text


def test_replay_rejects_unknown_ids(tmp_path, capsys):
    out = tmp_path / "rec.json"
    record_main(["--provider", "scripted", "--out", str(out)])
    assert replay_main([str(out), "--ids", "nope", "--speed", "0"]) == 2
