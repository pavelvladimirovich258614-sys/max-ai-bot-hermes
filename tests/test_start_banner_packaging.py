from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_banner_is_a_real_png():
    banner = ROOT / "assets" / "menu_banner.png"
    assert banner.is_file()
    assert banner.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_dockerfile_copies_start_banner_assets():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY assets ./assets" in dockerfile
