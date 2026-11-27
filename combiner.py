from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pymupdf
from ofd import OfdInputError, convert_first_page


A4 = pymupdf.paper_rect("a4")
PER_SHEET = 3
VERTICAL_MARGIN = 12
LEFT_MARGIN = 12
RIGHT_MARGIN = 60


class InvalidInvoice(ValueError):
    pass


def fit_in_slot(source: pymupdf.Rect, slot: int) -> pymupdf.Rect:
    slot_height = (A4.height - 2 * VERTICAL_MARGIN) / PER_SHEET
    right = A4.width - RIGHT_MARGIN
    scale = min(slot_height / source.height, (right - LEFT_MARGIN) / source.width)
    top = VERTICAL_MARGIN + slot * slot_height
    return pymupdf.Rect(right - source.width * scale, top, right, top + source.height * scale)


def place_invoice(
    target: pymupdf.Page, slot: int, filename: str, stream: BinaryIO
) -> None:
    extension = Path(filename).suffix.lower()
    if extension not in {".pdf", ".jpg", ".jpeg", ".ofd"}:
        raise InvalidInvoice(f"{filename}：请选择 OFD、PDF 或 JPEG 文件。")

    data = stream.read()
    if not data:
        raise InvalidInvoice(f"{filename}：文件为空。")
    if extension == ".ofd":
        try:
            data = convert_first_page(data)
        except OfdInputError as error:
            raise InvalidInvoice(f"{filename}：{error}") from error

    try:
        if extension in {".jpg", ".jpeg"}:
            if not data.startswith(b"\xff\xd8\xff"):
                raise InvalidInvoice(f"{filename}：文件内容不是 JPEG。")
            with pymupdf.open(stream=data, filetype="jpeg") as image:
                target.insert_image(fit_in_slot(image[0].rect, slot), stream=data)
            return

        with pymupdf.open(stream=data, filetype="pdf") as source:
            if not source.is_pdf:
                raise InvalidInvoice(f"{filename}：文件内容不是 PDF。")
            if source.needs_pass:
                raise InvalidInvoice(f"{filename}：请先解除 PDF 的打开密码。")
            if not source.page_count:
                raise InvalidInvoice(f"{filename}：PDF 中没有页面。")

            first = source[0]
            if first.first_annot or first.first_widget:
                source.select([0])
                source.bake()
                first = source[0]
            placement = fit_in_slot(first.rect, slot)
            rotation = first.rotation
            if rotation:
                first.set_rotation(0)
            if first.get_contents():
                # PDF /Rotate is clockwise; show_pdf_page rotates counterclockwise.
                target.show_pdf_page(placement, source, 0, rotate=-rotation)
    except InvalidInvoice:
        raise
    except (pymupdf.FileDataError, RuntimeError, ValueError) as error:
        raise InvalidInvoice(f"{filename}：无法读取文件，请检查后重试。") from error


def combine(files: Iterable[tuple[str, BinaryIO]]) -> BytesIO:
    with pymupdf.open() as output:
        for index, (filename, stream) in enumerate(files):
            slot = index % PER_SHEET
            if slot == 0:
                page = output.new_page(width=A4.width, height=A4.height)
            place_invoice(page, slot, filename, stream)
        if not output.page_count:
            raise InvalidInvoice("请先选择至少一份发票。")
        result = BytesIO()
        output.save(result, garbage=4, deflate=True, use_objstms=1)
        result.seek(0)
        return result
