from app.llm.prompts.image_prompt import SYSTEM_PROMPT


def test_post_image_prompt_has_wide_editorial_default_and_wish_priority():
    text = SYSTEM_PROMPT.lower()
    assert "16:9" in SYSTEM_PROMPT
    assert "4k" in text
    assert "8k" in text
    assert "banner blindness" in text
    assert "explicit user wishes" in text
