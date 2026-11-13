import ast
import unittest
from io import BytesIO
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageDraw
from pathlib import Path
import re
import unicodedata
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parents[1]

class SmartVisionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server_legacy.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.app = (ROOT / "app.html").read_text(encoding="utf-8")

    def test_combined_verify_is_initialized_before_conditional_pipeline(self):
        marker = "combined_verify = None"
        self.assertIn(marker, self.source)
        marker_pos = self.source.index(marker)
        pipeline_pos = self.source.index("if canonical:", marker_pos)
        self.assertLess(marker_pos, pipeline_pos)

    def test_local_ocr_supports_portuguese_and_more_catalog_candidates(self):
        self.assertIn("languages:['por','eng']", self.app)
        self.assertIn("languages:['eng']", self.app)
        self.assertIn("T.createWorker(attempt.languages,1", self.app)
        self.assertIn("if(queries.length>=9)break;", self.app)

    def test_ocr_retries_multiple_cdns_and_language_fallbacks(self):
        self.assertIn("https://cdn.jsdelivr.net/npm", self.app)
        self.assertIn("https://unpkg.com", self.app)
        self.assertIn("cimeCreateBrowserOcrWorker(T)", self.app)
        self.assertIn("145000", self.app)

    def test_ocr_uses_full_image_contrast_and_three_title_regions(self):
        self.assertIn("imagem com contraste", self.app)
        self.assertIn("título superior", self.app)
        self.assertIn("título central", self.app)
        self.assertIn("título inferior", self.app)

    def test_streaming_brand_is_removed_without_dropping_title_text(self):
        self.assertIn(".replace(/\\b(netflix|crunchyroll)\\b/gi,' ')", self.app)
        self.assertNotIn("|netflix|crunchyroll|", self.app)

    def test_multiline_ocr_combines_three_lines(self):
        self.assertIn("rawLines[i]+' '+rawLines[i+1]+' '+rawLines[i+2]", self.app)

    def test_image_reader_checks_personal_history_aliases(self):
        self.assertIn("personal_matches.extend(search_user_recognition_catalog(_personal_query,6))", self.source)
        self.assertIn('"Biblioteca + Histórico pessoal"', self.source)

    def test_backend_parses_as_python(self):
        ast.parse((ROOT / "server_v5.py").read_text(encoding="utf-8"))
        ast.parse((ROOT / "api" / "cime.py").read_text(encoding="utf-8"))
        ast.parse((ROOT / "api" / "index.py").read_text(encoding="utf-8"))

    def test_browser_ocr_reports_languages_and_server_failure_details(self):
        self.assertIn("Preparando o leitor '+attempt.languages.join('+')+'…", self.app)
        self.assertIn("up.upload_error?' O envio ao servidor também falhou:", self.app)
        self.assertIn("up.upload_error?' · Falha no envio ao servidor:", self.app)

    def test_ocr_preprocessing_scales_and_returns_binary_grayscale(self):
        names = {"_otsu_threshold", "preparar_imagem_para_ocr"}
        nodes = [node for node in self.tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {
            "Image": Image, "ImageOps": ImageOps, "ImageEnhance": ImageEnhance,
            "ImageFilter": ImageFilter, "TypeError": TypeError, "ValueError": ValueError
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "server_legacy.py", "exec"), namespace)
        source = Image.new("RGB", (320, 180), (18, 18, 18))
        ImageDraw.Draw(source).text((20, 60), "FORCA DE FOGO", fill=(255, 255, 255))
        prepared = namespace["preparar_imagem_para_ocr"](source)
        self.assertEqual(prepared.mode, "L")
        self.assertEqual(prepared.size, (1280, 720))
        self.assertLessEqual(len(set(prepared.getdata())), 2)
        self.assertEqual(source.size, (320, 180))
        prepared.close()
        source.close()

    def test_streaming_ui_terms_are_removed_before_fuzzy_title_matching(self):
        wanted = {
            "_strip_streaming_ui_terms", "normalizar_texto", "_compact",
            "_edit_distance", "_fuzzy_alias_match", "canonical_title_from_text"
        }
        nodes = []
        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                nodes.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "TITLE_ALIASES"
                for target in node.targets
            ):
                nodes.append(node)
        namespace = {
            "re": re, "unicodedata": unicodedata,
            "_fuzz_ratio": lambda a, b: SequenceMatcher(None, a, b).ratio() * 100
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "server_legacy.py", "exec"), namespace)
        identify = namespace["canonical_title_from_text"]
        samples = [
            "Força de Fogo",
            "Forca de Fogo",
            "Forca de Fog0",
            "Crunchyroll | Novidades | Força de Fogo | Transmissão simultânea | Mais detalhes | Áudio | Legendas | ESC"
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(identify(sample), "Fire Force")

    def test_ocr_candidate_quality_prioritizes_larger_title_boxes(self):
        node = next(
            item for item in self.tree.body
            if isinstance(item, ast.FunctionDef) and item.name == "_candidate_quality"
        )
        namespace = {
            "re": re,
            "_strip_streaming_ui_terms": lambda value: value,
            "normalizar_texto": lambda value: re.sub(r"[^a-z0-9 ]+", " ", str(value).lower()).strip(),
            "parece_lixo": lambda value: False,
            "_looks_like_sentence": lambda value: False,
            "TITLE_SIGNAL_WORDS": set(),
            "_context_penalty": lambda words: 0
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), "server_legacy.py", "exec"), namespace)
        quality = namespace["_candidate_quality"]
        large = quality("fire force", 80, 56, None, 0.45)
        small = quality("fire force", 80, 18, None, 0.45)
        self.assertGreater(large, small)

    def test_ocr_pipeline_uses_adaptive_preprocessing_and_large_box_ranking(self):
        self.assertIn("preparar_imagem_para_ocr(image)", self.source)
        self.assertIn("assembled_ranked=sorted(", self.source)
        self.assertIn("grouped_lines,key=_line_priority", self.source)
        self.assertIn("STREAMING_UI_TERMS", self.source)

    def test_uploaded_image_inspection_reads_dimensions_channels_and_exif(self):
        function = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "inspect_uploaded_image"
        )
        namespace = {"Image": Image, "ImageOps": __import__("PIL.ImageOps", fromlist=["ImageOps"]), "BytesIO": BytesIO, "ValueError": ValueError}
        isolated = ast.Module(body=[function], type_ignores=[])
        exec(compile(isolated, "server_legacy.py", "exec"), namespace)
        inspect_image = namespace["inspect_uploaded_image"]

        png = BytesIO()
        Image.new("RGB", (320, 180), "white").save(png, format="PNG")
        info = inspect_image(png.getvalue())
        self.assertEqual(info["format"], "PNG")
        self.assertEqual(info["mode"], "RGB")
        self.assertEqual(info["width"], 320)
        self.assertEqual(info["height"], 180)
        self.assertEqual(info["channels"], 3)
        self.assertEqual(info["shape_hwc"], [180, 320, 3])
        self.assertGreater(info["bytes"], 0)

        jpeg = BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (80, 40), "white").save(jpeg, format="JPEG", exif=exif)
        rotated = inspect_image(jpeg.getvalue())
        self.assertEqual((rotated["width"], rotated["height"]), (40, 80))
        self.assertTrue(rotated["orientation_corrected"])

        with self.assertRaisesRegex(ValueError, "imagem válida"):
            inspect_image(b"not an image")

    def test_upload_response_exposes_pillow_image_diagnostics(self):
        self.assertIn("image_info = inspect_uploaded_image(body)", self.source)
        self.assertIn('"image_info":image_info', self.source)
        self.assertIn('"diagnostics":{"image":image_info,', self.source)

    def test_image_endpoint_has_diagnostics_and_result_contract(self):
        self.assertIn('"success": True', self.source)
        self.assertIn('"timing":', self.source)
        self.assertIn('"diagnostics":', self.source)
        self.assertIn('"season_number":season_number', self.source)

if __name__ == "__main__":
    unittest.main()
