from scribe import notes

SUMMARY = """\
# Q4 planning with **Acme**

**Participants:**
- **Steve** (microphone)
- **Sarah** (greeted by name)

## Summary
The call agreed the launch date. Tom will
check the budget.

## Action items
| Owner | Action | Due | Notes |
|---|---|---|---|
| Steve | Send the deck to Sarah | Friday | Include the _new_ pricing |
| Tom | Check the budget | | |
| Unassigned | Book the venue | 1 Nov | |

## Suggested follow-up
Here is a message Steve could send:

> Hi both, thanks for today.
>
> We agreed the **launch date**. I'll send the deck by Friday.

## Open questions and risks
None.
"""


def test_title_and_section():
    assert notes.title(SUMMARY) == "Q4 planning with Acme"
    assert notes.section(SUMMARY, "Summary") == (
        "The call agreed the launch date. Tom will\ncheck the budget.")
    assert notes.section(SUMMARY, "Missing") == ""


def test_follow_up_message_uses_the_quote_without_markdown():
    assert notes.follow_up_message(SUMMARY) == (
        "Hi both, thanks for today.\n\nWe agreed the launch date. I'll send the deck by Friday.")


def test_follow_up_message_without_a_quote():
    md = "## Suggested follow-up\nThanks **all**. Deck to follow.\n"
    assert notes.follow_up_message(md) == "Thanks all. Deck to follow."
    assert notes.follow_up_message("# Title\n") == ""


def test_action_items_as_plain_list():
    assert notes.action_items_text(SUMMARY) == (
        "- Steve: Send the deck to Sarah (due Friday). Include the new pricing\n"
        "- Tom: Check the budget\n"
        "- Unassigned: Book the venue (due 1 Nov)")


def test_action_items_without_a_table_fall_back_to_the_text():
    md = "## Action items\nNone recorded.\n"
    assert notes.action_items(md) == []
    assert notes.action_items_text(md) == "None recorded."


def test_render_styles_headings_bullets_tables_and_quotes():
    runs = notes.render(SUMMARY)
    text = "".join(t for t, _ in runs)
    assert text.startswith("Q4 planning with Acme\n")
    assert "|" not in text and "**" not in text and "> " not in text
    # Wrapped paragraph lines are joined.
    assert "Tom will check the budget." in text

    def tags_of(fragment):
        return next(tags for t, tags in runs if t == fragment)

    assert tags_of("Q4 planning with Acme") == ("h1",)
    assert tags_of("Summary") == ("h2",)
    assert tags_of("Steve") == ("bullet", "bold")
    assert tags_of("Send the deck to Sarah") == ("item",)
    assert ("Unassigned", ("item", "bold")) in runs
    assert tags_of("new") == ("meta", "italic")
    assert "Due: Friday   Notes: Include the " in text
    assert "launch date" in text and tags_of("launch date") == ("quote", "bold")
