import io
import json
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import server_legacy as vision


class GeminiVisionMultiViewTests(unittest.TestCase):
    def _image_bytes(self):
        image = Image.new("RGB", (900, 1200), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()

    def _mock_response(self):
        result = {
            "recognized": True,
            "title": "Wistoria: Wand and Sword",
            "aliases": ["Tsue to Tsurugi no Wistoria"],
            "category": "Anime",
            "confidence": 0.93,
            "season": 2,
            "season_title": "Season 2",
            "arc": "",
            "episode": 5,
            "season_evidence": "S2 EP 05",
            "evidence": "Título e marcação de episódio visíveis",
            "visible_title_text": "Wistoria S2 EP 05",
            "alternatives": [],
        }
        return Mock(
            ok=True,
            json=Mock(return_value={
                "candidates": [{
                    "content": {
                        "parts": [{"text": json.dumps(result, ensure_ascii=False)}]
                    }
                }]
            }),
        )

    def test_fast_mode_sends_one_full_image(self):
        response = self._mock_response()
        with (
            patch.object(vision, "GEMINI_API_KEY", "unit-test-key"),
            patch.object(vision, "_vision_cache_get", return_value=None),
            patch.object(vision, "_vision_cache_put"),
            patch.object(vision.requests, "post", return_value=response) as post,
        ):
            result = vision.gemini_vision_analyze(self._image_bytes(), deep=False)

        self.assertTrue(result["recognized"])
        self.assertEqual(result["title"], "Wistoria: Wand and Sword")
        self.assertEqual(result["vision_engine"], "gemini-multiview-v2")
        parts = post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(sum("inline_data" in part for part in parts), 1)

    def test_deep_mode_sends_full_image_and_two_detail_crops(self):
        response = self._mock_response()
        with (
            patch.object(vision, "GEMINI_API_KEY", "unit-test-key"),
            patch.object(vision, "_vision_cache_get", return_value=None),
            patch.object(vision, "_vision_cache_put"),
            patch.object(vision.requests, "post", return_value=response) as post,
        ):
            result = vision.gemini_vision_analyze(self._image_bytes(), deep=True)

        self.assertTrue(result["recognized"])
        self.assertEqual(result["season"], 2)
        self.assertEqual(result["episode"], 5)
        parts = post.call_args.kwargs["json"]["contents"][0]["parts"]
        self.assertEqual(sum("inline_data" in part for part in parts), 3)
        self.assertEqual(post.call_args.kwargs["timeout"], 14)


if __name__ == "__main__":
    unittest.main()
