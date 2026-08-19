from app.max.handlers.image_gen import _clear, _flow, _set
from app.max.state import clear_state, get_state, set_state


def teardown_function():
    for user_id in (1, 2, 10, 20):
        clear_state(user_id)


def test_state_data_is_available_at_top_level():
    set_state(1, "image:ask_prompt", {"mode": "own", "aspect": "1:1"})
    state = get_state(1)
    assert state == {
        "action": "image:ask_prompt",
        "mode": "own",
        "aspect": "1:1",
    }


def test_image_set_accepts_extra_flow_fields():
    _set(2, "image:ask_aspect", mode="from_post", post_text="Черновик")
    assert _flow(2) == {
        "action": "image:ask_aspect",
        "mode": "from_post",
        "post_text": "Черновик",
    }


def test_image_clear_removes_flow():
    _set(10, "image:ask_source")
    assert _flow(10) is not None
    _clear(10)
    assert _flow(10) is None


def test_states_are_isolated_by_user():
    _set(10, "image:ask_prompt", mode="own")
    _set(20, "image:ask_prompt", mode="from_post")
    assert _flow(10)["mode"] == "own"
    assert _flow(20)["mode"] == "from_post"
