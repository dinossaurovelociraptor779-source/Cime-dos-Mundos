import io
import json
import unittest
from unittest.mock import patch

from PIL import Image

import server_legacy as legacy
import server_v5


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400

    def json(self):
        return self.payload


def image_bytes(size, color=(30, 50, 80)):
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ImageRouteRegressionTests(unittest.TestCase):
    def test_trace_moe_is_reserved_for_landscape_screenshots(self):
        self.assertTrue(legacy.is_trace_moe_screenshot_candidate(image_bytes((1280, 720))))
        self.assertTrue(legacy.is_trace_moe_screenshot_candidate(image_bytes((800, 600))))
        self.assertFalse(legacy.is_trace_moe_screenshot_candidate(image_bytes((600, 900))))
        self.assertFalse(legacy.is_trace_moe_screenshot_candidate(image_bytes((800, 800))))
        self.assertFalse(legacy.is_trace_moe_screenshot_candidate(image_bytes((200, 120))))

    def test_invalid_image_is_not_sent_to_scene_search(self):
        self.assertFalse(legacy.is_trace_moe_screenshot_candidate(b"not-an-image"))


class PublicCoverProviderTests(unittest.TestCase):
    def setUp(self):
        self.old_tmdb_token = legacy.TMDB_TOKEN

    def tearDown(self):
        legacy.TMDB_TOKEN = self.old_tmdb_token

    @patch.object(legacy, "translate_synopsis", side_effect=lambda value: value)
    @patch.object(legacy.requests, "get")
    def test_kitsu_returns_anime_poster_without_api_key(self, mocked_get, _translate):
        mocked_get.return_value = FakeResponse({
            "data": [{
                "id": "cowboy-bebop",
                "attributes": {
                    "titles": {"canonical": "Cowboy Bebop", "en": "Cowboy Bebop"},
                    "posterImage": {"large": "https://media.kitsu.io/covers/cowboy-bebop.jpg"},
                    "startDate": "1998-04-03",
                    "episodeCount": 26,
                    "averageRating": "85.5",
                    "episodeLength": 24,
                },
            }]
        })
        result = legacy.kitsu_search("Cowboy Bebop")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "Kitsu")
        self.assertEqual(result[0]["cover"], "https://media.kitsu.io/covers/cowboy-bebop.jpg")
        self.assertEqual(result[0]["episodes"], 26)

    @patch.object(legacy.requests, "post")
    def test_anilist_returns_anime_poster(self, mocked_post):
        mocked_post.return_value = FakeResponse({
            "data": {"Page": {"media": [{
                "id": 1,
                "title": {"romaji": "Cowboy Bebop", "english": "Cowboy Bebop", "native": "カウボーイビバップ"},
                "coverImage": {"large": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/test.jpg"},
                "episodes": 26,
                "format": "TV",
                "averageScore": 86,
                "genres": ["Action", "Sci-Fi"],
            }]}}
        })
        result = legacy.anilist_search("Cowboy Bebop", translate=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "AniList")
        self.assertTrue(result[0]["cover"].endswith("/test.jpg"))

    @patch.object(legacy, "translate_synopsis", side_effect=lambda value: value)
    @patch.object(legacy.requests, "get")
    def test_tvmaze_returns_series_poster(self, mocked_get, _translate):
        mocked_get.return_value = FakeResponse([{
            "show": {
                "id": 169,
                "name": "Breaking Bad",
                "image": {"original": "https://static.tvmaze.com/uploads/images/original/test.jpg"},
                "premiered": "2008-01-20",
                "status": "Ended",
                "rating": {"average": 9.2},
                "genres": ["Drama"],
            }
        }])
        result = legacy.tvmaze_search("Breaking Bad")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "TVMaze")
        self.assertTrue(result[0]["cover"].endswith("/test.jpg"))

    @patch.object(legacy.requests, "get")
    def test_tmdb_returns_movie_poster_when_token_is_configured(self, mocked_get):
        legacy.TMDB_TOKEN = "test-token"
        mocked_get.return_value = FakeResponse({"results": [{
            "id": 42,
            "title": "Your Name.",
            "original_title": "Kimi no Na wa.",
            "release_date": "2016-08-26",
            "poster_path": "/your-name.jpg",
            "vote_average": 8.4,
            "overview": "A body-swapping story.",
        }]})
        result = legacy.tmdb_search("Your Name", "movie")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "TMDB")
        self.assertEqual(result[0]["cover"], "https://image.tmdb.org/t/p/w780/your-name.jpg")

    @patch.object(legacy, "cache_put")
    @patch.object(legacy, "cache_get", return_value=None)
    @patch.object(legacy, "jikan_request")
    def test_jikan_search_returns_anime_poster(self, mocked_jikan, _cache_get, _cache_put):
        mocked_jikan.return_value = {"data": [{
            "mal_id": 32281,
            "title": "Kimi no Na wa.",
            "title_english": "Your Name.",
            "title_japanese": "君の名は。",
            "images": {"jpg": {"large_image_url": "https://cdn.myanimelist.net/images/anime/5/87048l.jpg"}},
            "episodes": 1,
            "score": 8.8,
            "year": 2016,
            "type": "Movie",
        }]}
        results, best = legacy.smart_search_title("Your Name.", category_hint="Anime", fast_mode=False)
        self.assertTrue(results)
        self.assertEqual(best["source"], "Jikan")
        self.assertTrue(best["cover"].endswith("87048l.jpg"))


class SeasonCoverRegressionTests(unittest.TestCase):
    @patch.object(legacy, "cache_put")
    @patch.object(legacy, "cache_get", return_value=None)
    @patch.object(legacy, "jikan_request")
    @patch.object(legacy, "anilist_search")
    @patch.object(legacy, "kitsu_search")
    def test_smart_search_falls_back_to_anilist_and_kitsu_when_jikan_has_no_poster(
        self, mocked_kitsu, mocked_anilist, mocked_jikan, _cache_get, _cache_put
    ):
        mocked_jikan.return_value = {
            "data": [{
                "mal_id": 1,
                "title": "Cowboy Bebop",
                "title_english": "Cowboy Bebop",
                "title_japanese": "カウボーイビバップ",
                "images": {},
                "episodes": 26,
                "score": 8.7,
                "year": 1998,
                "type": "TV",
            }]
        }
        mocked_anilist.return_value = [{
            "id": 2,
            "title": "Cowboy Bebop",
            "title_english": "Cowboy Bebop",
            "cover": "https://s4.anilist.co/cowboy-bebop.jpg",
            "category": "Anime",
            "source": "AniList",
        }]
        mocked_kitsu.return_value = [{
            "id": "cowboy-bebop",
            "title": "Cowboy Bebop",
            "title_english": "Cowboy Bebop",
            "cover": "https://media.kitsu.io/cowboy-bebop.jpg",
            "category": "Anime",
            "source": "Kitsu",
        }]

        results, best = legacy.smart_search_title(
            "Cowboy Bebop", category_hint="Anime", fast_mode=True
        )
        sources = {item.get("source") for item in results}
        self.assertIn("AniList", sources)
        self.assertIn("Kitsu", sources)
        self.assertTrue(best.get("cover"))
        mocked_anilist.assert_called_once()
        mocked_kitsu.assert_called_once()

    def test_missing_covers_are_requested_for_each_known_season(self):
        row = {
            "name": "Cime Test Anime",
            "category": "Anime",
            "seasons_json": json.dumps([
                {"season": 1, "name": "Cime Test Anime", "cover": ""},
                {"season": 2, "name": "Cime Test Anime", "cover": ""},
            ]),
        }

        def fake_search(query, aliases=None, category_hint="", diagnostics=None, fast_mode=False):
            season = 2 if "Season 2" in query else 1
            return [], {
                "title": "Cime Test Anime Season " + str(season),
                "source": "Jikan",
                "cover": "https://cdn.myanimelist.net/season-" + str(season) + ".jpg",
                "_match_score": 95,
            }

        with patch.object(legacy, "smart_search_title", side_effect=fake_search):
            seasons, changed = server_v5._refresh_season_covers(
                row,
                {"title": "Cime Test Anime", "source": "Jikan"},
                [],
            )

        self.assertTrue(changed)
        self.assertEqual(seasons[0]["cover"], "https://cdn.myanimelist.net/season-1.jpg")
        self.assertEqual(seasons[1]["cover"], "https://cdn.myanimelist.net/season-2.jpg")


if __name__ == "__main__":
    unittest.main()
