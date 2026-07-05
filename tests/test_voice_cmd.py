from types import SimpleNamespace

from pyrrhon.commands.voice_cmd import voice_command


class StubController:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self) -> str:
        self.started = True
        return "Voice: on."

    async def stop(self) -> str:
        self.stopped = True
        return "Voice: off."


async def test_voice_on_starts_the_controller():
    controller = StubController()
    ctx = SimpleNamespace(voice=controller)
    assert "on" in (await voice_command("on", ctx)).lower()
    assert controller.started is True


async def test_voice_off_stops_the_controller():
    controller = StubController()
    ctx = SimpleNamespace(voice=controller)
    assert "off" in (await voice_command("off", ctx)).lower()
    assert controller.stopped is True


async def test_voice_without_controller_degrades_honestly():
    ctx = SimpleNamespace(voice=None)
    out = await voice_command("on", ctx)
    assert "not available" in out.lower()


async def test_voice_bad_args_show_usage():
    ctx = SimpleNamespace(voice=StubController())
    assert "usage" in (await voice_command("sideways", ctx)).lower()
