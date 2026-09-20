"""The Learning page's outlook: computed from the settings, never written."""

from __future__ import annotations

from lexitrack.models.settings import LearningSettings
from lexitrack.services import schedule_preview


def days(settings: LearningSettings) -> dict[str, tuple[tuple[int, ...], int | None]]:
    return {p.label: (p.asked_on, p.known_on) for p in schedule_preview.paths(settings)}


class TestPaths:
    def test_the_defaults_are_the_documented_days(self) -> None:
        asked, known = days(LearningSettings())["Good every time"]
        assert asked == (1, 3, 14)
        assert known == 14

    def test_easy_gets_there_in_two_answers(self) -> None:
        asked, known = days(LearningSettings())["Easy every time"]
        assert len(asked) == 2
        assert known == asked[-1], "known on the answer that crosses the threshold"

    def test_one_again_costs_days_and_answers(self) -> None:
        plain = days(LearningSettings())["Good every time"]
        slipped = days(LearningSettings())["Good, with one Again"]
        assert len(slipped[0]) > len(plain[0])
        assert slipped[1] > plain[1]

    def test_a_higher_threshold_delays_known(self) -> None:
        strict = days(LearningSettings(mastery_stability_days=120))["Good every time"]
        assert strict[1] is None or strict[1] > days(LearningSettings())["Good every time"][1]

    def test_more_retention_means_more_reviews(self) -> None:
        often = days(LearningSettings(desired_retention=0.97))["Good every time"]
        rarely = days(LearningSettings(desired_retention=0.85))["Good every time"]
        assert len(often[0]) > len(rarely[0])

    def test_a_path_that_never_masters_says_so_instead_of_running_forever(self) -> None:
        """A threshold no answer can reach must stop, not loop."""
        asked, known = days(LearningSettings(mastery_stability_days=100_000))["Good every time"]
        assert known is None
        assert len(asked) <= schedule_preview._MAX_ANSWERS

    def test_nothing_is_saved_or_read(self) -> None:
        """A pure function: two calls with the same settings agree exactly."""
        settings = LearningSettings(mastery_stability_days=30)
        assert days(settings) == days(settings)
