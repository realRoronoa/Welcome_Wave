"""An uploaded document's text, page by page: PDF, Word or plain text, within limits that refuse a file before it is unpacked.
The Librarian indexes what this returns; the Keeper (a flagged document's excerpt) and the document view read it too."""
from __future__ import annotations

import io
import zipfile

from app.ports import TooLarge

MAX_PDF_PAGES = 2000
MAX_DOCX_UNPACKED_MB = 100
MAX_DOCX_RATIO = 200  # unpacked size over packed size: past this a Word file is a decompression bomb, not a document


def extract_pages(data: bytes, mime: str) -> list[str]:
    """The text of an uploaded document, page by page. TooLarge for a PDF over MAX_PDF_PAGES pages or a Word file that would
    unpack to more than MAX_DOCX_UNPACKED_MB (or MAX_DOCX_RATIO times its size): neither is read at all."""
    if mime == "application/pdf":
        from pypdf import PdfReader

        pages = PdfReader(io.BytesIO(data)).pages
        if len(pages) > MAX_PDF_PAGES:
            raise TooLarge(f"The PDF has {len(pages):,} pages; documents of up to {MAX_PDF_PAGES:,} pages are read. Split it and upload the parts.")
        return [p.extract_text() or "" for p in pages]
    if mime.endswith("wordprocessingml.document"):
        import docx  # python-docx

        _check_unpacked(data)
        return ["\n\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)]
    return data.decode("utf-8", errors="replace").split("\f")


def _check_unpacked(data: bytes) -> None:
    """A .docx is a zip: refuse one whose parts would unpack to far more than any document needs, before anything unpacks."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
    except zipfile.BadZipFile:
        return  # python-docx says what is wrong with it
    unpacked, packed = sum(i.file_size for i in infos), sum(i.compress_size for i in infos)
    if unpacked > MAX_DOCX_UNPACKED_MB * 1024 * 1024 or unpacked > MAX_DOCX_RATIO * max(packed, 1024):
        raise TooLarge(f"The Word file would unpack to {unpacked / 1048576:,.0f} MB; files of up to {MAX_DOCX_UNPACKED_MB} MB "
                       "unpacked are read. Save it again, or as a PDF, and upload that.")
