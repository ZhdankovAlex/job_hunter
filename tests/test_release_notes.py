"""Release notes: parsing git log without running git, and building Markdown."""
from jobhunter import release_notes as rn

F, R = "\x1f", "\x1e"
LOG = (
    F.join(["c2c4277", "2026-11-17", "Web: no-cache for reports",
            "- applications.html was served without Cache-Control:\n  the browser kept a copy\n- a header test\n\n"
            "Co-Authored-By: Claude <noreply@anthropic.com>\n"]) + R + "\n"
    + F.join(["f5fbb57", "2026-11-17", "Http: an unreachable host is skipped",
              "The host did not respond\nfor about an hour.\n\n- Http.UNREACHABLE_AFTER\n"]) + R + "\n"
    + F.join(["5993b16", "2026-11-16", "Typo", ""]) + R + "\n"
)


def test_parse_log_items_text_and_trailers():
    first, second, third = rn.parse_log(LOG)
    assert (first.sha, first.date, first.title) == ("c2c4277", "2026-11-17", "Web: no-cache for reports")
    # an indented line continues the item; Co-Authored-By does not get into the notes
    assert first.items == ["applications.html was served without Cache-Control: the browser kept a copy",
                           "a header test"]
    assert first.text == []
    assert second.text == ["The host did not respond for about an hour."]
    assert second.items == ["Http.UNREACHABLE_AFTER"]
    assert third.text == [] and third.items == []


def test_markdown_groups_by_day_newest_first():
    md = rn.to_markdown(rn.parse_log(LOG))
    assert md.index("## 2026-11-17") < md.index("### Web: no-cache") < md.index("### Http:") < md.index("## 2026-11-16")
    assert md.count("## 2026-11-17") == 1
    assert "Co-Authored-By" not in md


def test_bot_commit_is_skipped():
    log = F.join(["aaa1111", "2026-11-18", rn.BOT_TITLE, ""]) + R + "\n" + LOG
    assert [c.sha for c in rn.parse_log(log)] == ["c2c4277", "f5fbb57", "5993b16"]


def test_reworded_commit_uses_english_message():
    log = F.join(["4afc156", "2026-11-06", "Original title", "- original item\n"]) + R + "\n"
    (change,) = rn.parse_log(log)
    assert change.title == rn.REWORDED["4afc156"].partition("\n")[0]
    assert change.items == []


def test_every_reworded_message_parses():
    for message in rn.REWORDED.values():
        title, _, body = message.partition("\n")
        assert title and not title.startswith("- ")
        rn.parse_body(body)
