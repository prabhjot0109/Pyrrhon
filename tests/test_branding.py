from pyrrhon import __version__
from pyrrhon.branding import banner


def test_banner_names_the_product_and_version():
    text = banner()
    assert "P Y R R H O N" in text
    assert __version__ in text
    assert all(len(line) <= 60 for line in text.splitlines())
    assert text.isascii()  # Windows terminals; TUI pane safety
