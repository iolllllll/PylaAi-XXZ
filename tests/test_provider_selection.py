import unittest
from unittest.mock import patch

from detect import _build_providers
from utils import DefaultEasyOCR


class ProviderSelectionTests(unittest.TestCase):
    @patch("detect.ort.get_available_providers", return_value=[
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ])
    def test_auto_prefers_directml_before_cuda_on_windows(self, *_):
        providers = _build_providers("auto")
        self.assertEqual(providers[0], "DmlExecutionProvider")

    @patch("detect.ort.get_available_providers", return_value=[
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ])
    def test_explicit_cuda_still_selects_cuda(self, *_):
        providers = _build_providers("cuda")
        self.assertEqual(providers[0][0], "CUDAExecutionProvider")

    @patch("easyocr.Reader")
    def test_easyocr_is_forced_to_cpu(self, mock_reader):
        DefaultEasyOCR()
        self.assertFalse(mock_reader.call_args.kwargs["gpu"])


if __name__ == "__main__":
    unittest.main()
