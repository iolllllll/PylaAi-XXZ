import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from runtime_control import PAUSED, RUNNING, write_state
from local_webapp import LocalWebAppServer, webapp_settings


class WebAppTests(unittest.TestCase):
    def test_webapp_settings_can_bind_to_lan(self):
        settings = webapp_settings({
            "webapp_enabled": "yes",
            "webapp_host": "",
            "webapp_port": "9876",
            "webapp_allow_lan": "yes",
        })
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["host"], "0.0.0.0")
        self.assertEqual(settings["port"], 9876)
        self.assertTrue(settings["allow_lan"])

    def test_runtime_and_control_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "runtime.state"
            write_state(state_path, RUNNING)
            server = LocalWebAppServer(
                state_path=state_path,
                status_provider=lambda: {"ips": "12.34", "onnxBackend": "CUDAExecutionProvider"},
                config_loader=lambda: {"webapp_enabled": True, "webapp_host": "127.0.0.1", "webapp_port": 0},
            )
            self.assertTrue(server.start())
            try:
                with urlopen(f"{server.url}/", timeout=3) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("Amethyst Webapp", html)
                self.assertIn('/styles.css', html)

                with urlopen(f"{server.url}/styles.css", timeout=3) as response:
                    css = response.read().decode("utf-8")
                self.assertIn("--hot: #ff4fb8", css)

                with urlopen(f"{server.url}/app.js", timeout=3) as response:
                    js = response.read().decode("utf-8")
                self.assertIn("refreshRuntime", js)

                with urlopen(f"{server.url}/api/runtime", timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["runtimeControl"], RUNNING)
                self.assertEqual(payload["onnxBackend"], "CUDAExecutionProvider")

                request = Request(
                    f"{server.url}/api/control",
                    data=json.dumps({"action": "pause"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["state"], PAUSED)
                self.assertEqual(state_path.read_text(encoding="utf-8"), PAUSED)
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
