"""Validate uploaded image bytes and derive a safe, truthful file extension."""
from io import BytesIO

from PIL import Image

_ALLOWED_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}


def validated_image_extension(image_bytes):
    """Return an extension matching a valid supported image, otherwise None."""
    if not isinstance(image_bytes, (bytes, bytearray, memoryview)) or not image_bytes:
        return None

    try:
        with Image.open(BytesIO(bytes(image_bytes))) as image:
            image_format = str(image.format or "").upper()
            if image_format not in _ALLOWED_FORMATS:
                return None
            image.verify()
        return _ALLOWED_FORMATS[image_format]
    except Exception:
        # Invalid or truncated uploads must be rejected by the caller.
        return None
