from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from PIL import Image, ImageOps

from marketplace_agent.config import Content
from marketplace_agent.models import Listing

_REQUIRED_COLUMNS = {"id", "title", "description", "price", "city", "photo_1", "photo_2"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


@dataclass(frozen=True, slots=True)
class WorkbookContent:
    listings: list[Listing]
    cities: set[str]
    responses: dict[str, str]


def _headers(row: tuple[Any, ...]) -> dict[str, int]:
    return {str(cell.value).strip(): index for index, cell in enumerate(row) if cell.value}


def _text(row: tuple[Any, ...], headers: dict[str, int], name: str) -> str:
    index = headers.get(name)
    if index is None or index >= len(row) or row[index].value is None:
        return ""
    return str(row[index].value).strip()


def load_content(config: Content) -> WorkbookContent:
    if not config.workbook.is_file():
        raise ValueError(f"content workbook does not exist: {config.workbook}")
    workbook = load_workbook(config.workbook, read_only=True, data_only=True)
    try:
        if config.listings_sheet not in workbook.sheetnames:
            raise ValueError(f"missing workbook sheet: {config.listings_sheet}")
        sheet = workbook[config.listings_sheet]
        iterator = sheet.iter_rows()
        try:
            headers = _headers(next(iterator))
        except StopIteration as exc:
            raise ValueError("listings sheet is empty") from exc
        missing = _REQUIRED_COLUMNS - headers.keys()
        if missing:
            raise ValueError(f"missing listing columns: {', '.join(sorted(missing))}")

        cities = _load_cities(workbook, config.cities_sheet)
        responses = _load_responses(workbook, config.responses_sheet)
        listings: list[Listing] = []
        seen: set[str] = set()
        for row_number, row in enumerate(iterator, start=2):
            listing_id = _text(row, headers, "id")
            if not listing_id:
                continue
            if listing_id in seen:
                raise ValueError(f"duplicate listing id on row {row_number}: {listing_id}")
            seen.add(listing_id)
            title = _text(row, headers, "title")
            description = _text(row, headers, "description")
            city = _text(row, headers, "city")
            if not title or not description or not city:
                raise ValueError(f"title, description and city are required on row {row_number}")
            if cities and city not in cities:
                raise ValueError(f"city on row {row_number} is not in the cities sheet: {city}")
            try:
                price = Decimal(_text(row, headers, "price"))
            except InvalidOperation as exc:
                raise ValueError(f"invalid price on row {row_number}") from exc
            if not price.is_finite() or price <= 0:
                raise ValueError(f"price must be positive on row {row_number}")
            photos = tuple(
                _photo_path(config.workbook.parent, _text(row, headers, f"photo_{number}"))
                for number in (1, 2)
            )
            category = _text(row, headers, "category") or config.default_category
            listings.append(
                Listing(
                    id=listing_id,
                    title=title,
                    description=description,
                    price=price,
                    city=city,
                    category=category,
                    photos=photos,  # type: ignore[arg-type]
                )
            )
        if not listings:
            raise ValueError("listings sheet contains no listings")
        return WorkbookContent(listings=listings, cities=cities, responses=responses)
    finally:
        workbook.close()


def _photo_path(base: Path, value: str) -> Path:
    if not value:
        raise ValueError("both photo_1 and photo_2 are required")
    path = Path(value)
    path = path if path.is_absolute() else base / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"listing photo does not exist: {path}")
    return path


def _load_cities(workbook: Any, name: str) -> set[str]:
    if name not in workbook.sheetnames:
        return set()
    rows = workbook[name].iter_rows(values_only=True)
    try:
        headers = [str(value).strip() if value else "" for value in next(rows)]
    except StopIteration:
        return set()
    if "city" not in headers:
        raise ValueError("cities sheet needs a city column")
    index = headers.index("city")
    return {
        str(row[index]).strip()
        for row in rows
        if index < len(row) and row[index] is not None and str(row[index]).strip()
    }


def _load_responses(workbook: Any, name: str) -> dict[str, str]:
    if name not in workbook.sheetnames:
        return {}
    rows = workbook[name].iter_rows(values_only=True)
    try:
        headers = [str(value).strip() if value else "" for value in next(rows)]
    except StopIteration:
        return {}
    if not {"key", "text"}.issubset(headers):
        raise ValueError("responses sheet needs key and text columns")
    key_index, text_index = headers.index("key"), headers.index("text")
    responses: dict[str, str] = {}
    for row in rows:
        key = str(row[key_index]).strip() if key_index < len(row) and row[key_index] else ""
        text = str(row[text_index]).strip() if text_index < len(row) and row[text_index] else ""
        if not key and not text:
            continue
        if not key or not text or key in responses:
            raise ValueError("response keys must be non-empty and unique, with non-empty text")
        responses[key] = text
    return responses


def strip_image_metadata(source: Path, output: Path) -> tuple[int, int]:
    source, output = source.resolve(), output.resolve()
    if source == output:
        raise ValueError("source and output image directories must be different")
    if not source.is_dir():
        raise ValueError(f"image source directory does not exist: {source}")
    output.mkdir(parents=True, exist_ok=True)
    converted = skipped = 0
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        target = (output / path.relative_to(source)).with_suffix(".jpg")
        if target.exists():
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            clean = Image.new("RGB", image.size)
            clean.paste(image)
            temporary = target.with_suffix(".tmp")
            clean.save(temporary, format="JPEG", quality=90, optimize=True)
            temporary.replace(target)
        converted += 1
    return converted, skipped
