import unittest
from unittest.mock import patch

import cv2
import numpy as np

from stage_manager import StageManager


class DummyWindowController:
    width_ratio = 1.0
    height_ratio = 1.0

    def __init__(self):
        self.clicks = []
        self.presses = []
        self.keys_released = []
        self.restart_calls = 0
        self.scrcpy_restart_calls = 0

    def click(self, x, y, **kwargs):
        self.clicks.append((x, y, kwargs))

    def press_key(self, key):
        self.presses.append(key)

    def keys_up(self, keys):
        self.keys_released.append(keys)

    def restart_brawl_stars(self):
        self.restart_calls += 1
        return True

    def restart_scrcpy_client(self):
        self.scrcpy_restart_calls += 1
        return True


class DummyTrophyObserver:
    current_trophies = 250
    current_wins = 0
    win_streak = 0

    def change_trophies(self, value):
        self.current_trophies = value


class DummyLobbyAutomation:
    def __init__(self):
        self.lowest_calls = 0

    def select_lowest_trophy_brawler(self):
        self.lowest_calls += 1
        return True


class PostMatchActionTests(unittest.TestCase):
    def make_manager(self, action):
        manager = object.__new__(StageManager)
        manager.post_match_action = action
        manager.window_controller = DummyWindowController()
        return manager

    def test_play_again_only_when_target_not_reached(self):
        manager = self.make_manager("play_again")

        self.assertTrue(manager.should_use_play_again(value=249, target=250))
        self.assertFalse(manager.should_use_play_again(value=250, target=250))

    def test_lobby_mode_never_uses_play_again(self):
        manager = self.make_manager("lobby")

        self.assertFalse(manager.should_use_play_again(value=10, target=250))

    def test_play_again_clicks_result_button(self):
        manager = self.make_manager("play_again")
        manager.window_controller.screenshot = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)

        with patch("stage_manager.extract_text_strings", return_value=[]):
            manager.dismiss_end_screen(use_play_again=True)

        self.assertEqual(manager.window_controller.presses, [])
        self.assertEqual(manager.window_controller.clicks[0][0:2], (1215, 935))
        self.assertIn(list("wasd"), manager.window_controller.keys_released)

    @patch("stage_manager.extract_text_strings", return_value=["exit"])
    def test_play_again_missing_clicks_exit_button(self, *_):
        manager = self.make_manager("play_again")
        manager.window_controller.screenshot = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)

        manager.dismiss_end_screen(use_play_again=True)

        self.assertEqual(manager.window_controller.presses, [])
        self.assertEqual(manager.window_controller.clicks[0][0:2], (1660, 980))
        self.assertIn(list("wasd"), manager.window_controller.keys_released)

    @patch("stage_manager.extract_text_strings", return_value=["exit", "play again"])
    def test_play_again_visible_does_not_exit_even_when_exit_exists(self, *_):
        manager = self.make_manager("play_again")
        manager.window_controller.screenshot = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)

        manager.dismiss_end_screen(use_play_again=True)

        self.assertEqual(manager.window_controller.presses, [])
        self.assertEqual(manager.window_controller.clicks[0][0:2], (1215, 935))
        self.assertIn(list("wasd"), manager.window_controller.keys_released)

    def test_play_again_visual_button_skips_ocr(self):
        manager = self.make_manager("play_again")
        screenshot = np.zeros((1080, 1920, 3), dtype=np.uint8)
        blue_bgr = cv2.cvtColor(
            np.full((1, 1, 3), (108, 210, 220), dtype=np.uint8),
            cv2.COLOR_HSV2BGR,
        )[0, 0]
        blue_rgb = tuple(int(v) for v in blue_bgr[::-1])
        screenshot[850:1000, 1030:1390] = blue_rgb
        screenshot[890:950, 1120:1300] = (255, 255, 255)
        screenshot[960:1000, 1030:1390] = (5, 5, 5)
        manager.window_controller.screenshot = lambda: screenshot

        with patch("stage_manager.extract_text_strings", side_effect=AssertionError("OCR should not run")):
            manager.dismiss_end_screen(use_play_again=True)

        self.assertEqual(manager.window_controller.clicks[0][0:2], (1215, 935))

    def test_exit_visual_button_skips_ocr_when_play_again_missing(self):
        manager = self.make_manager("play_again")
        screenshot = np.zeros((1080, 1920, 3), dtype=np.uint8)
        blue_bgr = cv2.cvtColor(
            np.full((1, 1, 3), (108, 210, 220), dtype=np.uint8),
            cv2.COLOR_HSV2BGR,
        )[0, 0]
        blue_rgb = tuple(int(v) for v in blue_bgr[::-1])
        screenshot[850:1020, 1480:1860] = blue_rgb
        screenshot[900:970, 1580:1740] = (255, 255, 255)
        screenshot[980:1020, 1480:1860] = (5, 5, 5)
        manager.window_controller.screenshot = lambda: screenshot

        with patch("stage_manager.extract_text_strings", side_effect=AssertionError("OCR should not run")):
            manager.dismiss_end_screen(use_play_again=True)

        self.assertEqual(manager.window_controller.clicks[0][0:2], (1660, 980))

    def test_lobby_mode_presses_continue_key(self):
        manager = self.make_manager("lobby")

        manager.dismiss_end_screen(use_play_again=False)

        self.assertEqual(manager.window_controller.clicks, [])
        self.assertEqual(manager.window_controller.presses, ["Q"])
        self.assertIn(list("wasd"), manager.window_controller.keys_released)

    @patch("stage_manager.save_brawler_data")
    def test_target_completion_in_play_again_mode_restarts_and_selects_next(self, *_):
        manager = self.make_manager("play_again")
        manager.brawlers_pick_data = [
            {
                "brawler": "first",
                "push_until": 250,
                "trophies": 250,
                "wins": 0,
                "type": "trophies",
                "automatically_pick": False,
                "win_streak": 0,
                "selection_method": "lowest_trophies",
            },
            {
                "brawler": "second",
                "push_until": 250,
                "trophies": 10,
                "wins": 0,
                "type": "trophies",
                "automatically_pick": True,
                "win_streak": 0,
                "selection_method": "lowest_trophies",
            },
        ]
        manager.Trophy_observer = DummyTrophyObserver()
        manager.Lobby_automation = DummyLobbyAutomation()
        manager.wait_for_lobby_after_reward = lambda max_attempts=45: object()
        manager.stop_after_post_match_rewards = False

        self.assertTrue(manager.restart_and_select_next_after_target(250, "trophies"))

        self.assertEqual(manager.brawlers_pick_data[0]["brawler"], "second")
        self.assertEqual(manager.window_controller.restart_calls, 1)
        self.assertEqual(manager.window_controller.scrcpy_restart_calls, 1)
        self.assertEqual(manager.Lobby_automation.lowest_calls, 1)
        self.assertEqual(manager.window_controller.presses, ["Q"])


if __name__ == "__main__":
    unittest.main()
