"""Resource-bounded resume parsing worker.

The API launches this module in a subprocess and sends one PDF or DOCX over
stdin. Keeping complex document parsers outside the web process lets the API
kill the worker on timeout and prevents parser memory growth from taking down
the scheduler, Telegram bot, and HTTP server together.
"""

from __future__ import annotations

import io
import sys
import zipfile

from defusedxml import ElementTree as DefusedET
from pdfminer.high_level import extract_text as pdf_extract

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
MAX_DOCX_MEMBERS = 2_000
MAX_DOCX_COMPRESSION_RATIO = 200
MAX_OUTPUT_CHARS = 100_000


def _apply_resource_limits() -> None:
    """Apply Linux hard limits before parsing attacker-controlled bytes."""
    if not sys.platform.startswith("linux"):
        return

    import resource

    memory_bytes = 768 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _parse_pdf(content: bytes) -> str:
    if not content.startswith(b"%PDF"):
        raise ValueError("Invalid PDF signature")
    return pdf_extract(io.BytesIO(content))


def _parse_docx(content: bytes) -> str:
    if not content.startswith(b"PK\x03\x04"):
        raise ValueError("Invalid DOCX signature")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > MAX_DOCX_MEMBERS:
            raise ValueError("DOCX contains too many archive members")
        try:
            info = archive.getinfo("word/document.xml")
        except KeyError as exc:
            raise ValueError("DOCX missing document.xml") from exc
        if info.file_size > MAX_DOCX_XML_BYTES:
            raise ValueError("DOCX document.xml is too large")
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > MAX_DOCX_COMPRESSION_RATIO:
            raise ValueError("DOCX compression ratio is unsafe")
        xml_content = archive.read(info)

    tree = DefusedET.fromstring(xml_content)
    paragraphs: list[str] = []
    paragraph_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
    text_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    output_length = 0
    for paragraph in tree.iter(paragraph_tag):
        text = "".join(node.text for node in paragraph.iter(text_tag) if node.text)
        if not text:
            continue
        remaining = MAX_OUTPUT_CHARS - output_length
        if remaining <= 0:
            break
        paragraphs.append(text[:remaining])
        output_length += min(len(text), remaining) + 1
    return "\n".join(paragraphs)[:MAX_OUTPUT_CHARS]


def parse_resume(kind: str, content: bytes) -> str:
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError("Resume input is too large")
    if kind == "pdf":
        return _parse_pdf(content)[:MAX_OUTPUT_CHARS]
    if kind == "docx":
        return _parse_docx(content)
    raise ValueError("Unsupported parser kind")


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    _apply_resource_limits()
    content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        text = parse_resume(sys.argv[1], content)
    except Exception as exc:  # noqa: BLE001 - CLI boundary must report parser failures safely
        sys.stderr.write(f"{exc.__class__.__name__}: {exc}\n")
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
