from pyrrhon.voice.playback import PlaybackTracker


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_nothing_played_before_playback_starts():
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=FakeClock())
    tracker.speech_queued("alpha beta gamma delta")
    assert tracker.played_text() == ""


def test_duration_estimate_cuts_at_a_word_boundary():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta gamma delta")  # len 22
    tracker.playback_started()
    clock.now = 1.2  # 12 chars elapsed → inside "gamma" → cut back to "alpha beta"
    assert tracker.played_text() == "alpha beta"


def test_estimate_beyond_text_returns_everything():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("short answer")
    tracker.playback_started()
    clock.now = 60.0
    assert tracker.played_text() == "short answer"


def test_finished_playback_returns_full_text_regardless_of_clock():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta")
    tracker.playback_started()
    tracker.playback_finished()
    clock.now = 0.1
    assert tracker.played_text() == "alpha beta"


def test_word_timestamps_beat_the_estimate_when_available():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta gamma delta")
    tracker.playback_started()
    tracker.word_played("alpha")
    tracker.word_played("beta")
    tracker.word_played("gamma")
    clock.now = 0.1  # estimate would say almost nothing — timestamps win
    assert tracker.played_text() == "alpha beta gamma"


def test_multiple_speech_chunks_are_joined():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta")
    tracker.speech_queued("gamma delta")
    tracker.playback_started()
    clock.now = 1.2
    assert tracker.played_text() == "alpha beta"


def test_reset_clears_everything():
    clock = FakeClock(0.0)
    tracker = PlaybackTracker(chars_per_sec=10.0, clock=clock)
    tracker.speech_queued("alpha beta")
    tracker.playback_started()
    tracker.word_played("alpha")
    tracker.reset()
    assert tracker.played_text() == ""
