from app.bloxhub_source import BloxhubSource
from app.source_overrides import parser_for_source


def test_bloxhub_links_use_dedicated_event_parser():
    parser = parser_for_source(
        "https://bloxhub.org/rent-office-space/?utm_source=google&utm_medium=cpc",
        "user",
    )

    assert isinstance(parser, BloxhubSource)
