import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from image_upload_validation import validated_image_extension


def image_bytes(image_format):
    buffer = BytesIO()
    Image.new("RGB", (3, 3), (20, 40, 60)).save(buffer, format=image_format)
    return buffer.getvalue()


class ImageUploadValidationTests(unittest.TestCase):
    def test_jpeg_extension_comes_from_image_content(self):
        self.assertEqual(validated_image_extension(image_bytes("JPEG")), ".jpg")

    def test_png_extension_comes_from_image_content(self):
        self.assertEqual(validated_image_extension(image_bytes("PNG")), ".png")

    def test_webp_extension_comes_from_image_content(self):
        self.assertEqual(validated_image_extension(image_bytes("WEBP")), ".webp")

    def test_valid_but_unsupported_format_is_rejected(self):
        self.assertIsNone(validated_image_extension(image_bytes("GIF")))

    def test_corrupt_or_empty_content_is_rejected(self):
        self.assertIsNone(validated_image_extension(b"not an image"))
        self.assertIsNone(validated_image_extension(b""))

    def test_upload_route_uses_validated_content_extension(self):
        source = (Path(__file__).resolve().parents[1] / "server_legacy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ext = validated_image_extension(body)", source)


if __name__ == "__main__":
    unittest.main()
