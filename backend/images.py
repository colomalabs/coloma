"""Image downscaling for proxied requests and PDF-to-image rendering."""

import asyncio
import base64
import io
import json
import re

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pdf2image import convert_from_bytes
from PIL import Image
from pydantic import BaseModel


PDF_MIN_DPI = 50
PDF_MAX_DPI = 600
PDF_MIN_QUALITY = 1
PDF_MAX_QUALITY = 100
# Pillow's default JPEG quality is 75, which would degrade a downscaled image
# beyond what the resize itself implies.
JPEG_RESAVE_QUALITY = 90
DATA_URL_IMAGE_PATTERN = re.compile(r"^data:image/(?P<subtype>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$", re.DOTALL)

router = APIRouter()


class PdfRenderResponse(BaseModel):
    images: list[str] = []


def downscale_base64_image(data_url: str, max_megapixels: float) -> str:
    match = DATA_URL_IMAGE_PATTERN.match(data_url)
    if not match:
        return data_url
    try:
        raw = base64.b64decode(match.group("data"))
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            max_pixels = max_megapixels * 1_000_000
            if width * height <= max_pixels:
                return data_url
            scale = (max_pixels / (width * height)) ** 0.5
            new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
            save_format = image.format or "PNG"
            resized = image.resize(new_size, Image.LANCZOS)
            buffer = io.BytesIO()
            save_kwargs = {"quality": JPEG_RESAVE_QUALITY, "optimize": True} if save_format.upper() == "JPEG" else {}
            resized.save(buffer, format=save_format, **save_kwargs)
    except Exception:
        return data_url
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/{match.group('subtype')};base64,{encoded}"


def downscale_request_images(payload: object, max_megapixels: float) -> bool:
    changed = False
    if isinstance(payload, dict):
        image_url = payload.get("image_url")
        if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
            downscaled = downscale_base64_image(image_url["url"], max_megapixels)
            if downscaled != image_url["url"]:
                image_url["url"] = downscaled
                changed = True
        for value in payload.values():
            if downscale_request_images(value, max_megapixels):
                changed = True
    elif isinstance(payload, list):
        for item in payload:
            if downscale_request_images(item, max_megapixels):
                changed = True
    return changed


def optimize_request_body(body: bytes, max_megapixels: float) -> bytes:
    if max_megapixels <= 0 or not body:
        return body
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if downscale_request_images(payload, max_megapixels):
        return json.dumps(payload).encode("utf-8")
    return body


def render_pdf_pages(pdf_bytes: bytes, dpi: int, black_and_white: bool, quality: int) -> list[str]:
    pages = convert_from_bytes(pdf_bytes, dpi=dpi, thread_count=2)
    images: list[str] = []
    for page in pages:
        if black_and_white:
            page = page.convert("L")
        buffer = io.BytesIO()
        page.save(buffer, format="JPEG", quality=quality, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        images.append(f"data:image/jpeg;base64,{encoded}")
    return images


@router.post("/api/pdf/render", response_model=PdfRenderResponse)
async def render_pdf(
    file: UploadFile = File(...),
    dpi: int = Form(300),
    black_and_white: bool = Form(True),
    quality: int = Form(80),
) -> PdfRenderResponse:
    pdf_bytes = await file.read()
    bounded_dpi = max(PDF_MIN_DPI, min(dpi, PDF_MAX_DPI))
    bounded_quality = max(PDF_MIN_QUALITY, min(quality, PDF_MAX_QUALITY))
    try:
        images = await asyncio.to_thread(render_pdf_pages, pdf_bytes, bounded_dpi, black_and_white, bounded_quality)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not render PDF: {exc}") from exc
    return PdfRenderResponse(images=images)
