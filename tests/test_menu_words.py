from app.bot import is_menu_word, reset_pending


def test_menu_words_are_recognised_case_insensitively():
    for text in ["Start", "start", " MENU ", "/menu", "Cancel", "Меню"]:
        assert is_menu_word(text)


def test_normal_text_is_not_a_menu_word():
    for text in ["AI", "architecture", "start-up events", "", None]:
        assert not is_menu_word(text)


def test_reset_pending_clears_half_finished_flows():
    data = {"awaiting_interest_remove": True, "note_for": 5, "keep": 1}
    reset_pending(data)
    assert data == {"keep": 1}
