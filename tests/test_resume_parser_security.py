import io
import zipfile

import pytest
from defusedxml.common import EntitiesForbidden

from app.api.profile import _parse_resume_isolated
from app.services.resume_parser import parse_resume


def _docx(document_xml: str, extra_members: int = 0) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        for index in range(extra_members):
            archive.writestr(f"word/extra-{index}.xml", "x")
    return output.getvalue()


def test_docx_parser_extracts_text() -> None:
    document = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>Supply Chain Director</w:t></w:r></w:p>
        <w:p><w:r><w:t>Transformation and procurement</w:t></w:r></w:p>
      </w:body>
    </w:document>"""

    assert parse_resume("docx", _docx(document)) == ("Supply Chain Director\nTransformation and procurement")


def test_docx_parser_rejects_xml_entities() -> None:
    document = """<?xml version="1.0"?>
    <!DOCTYPE data [<!ENTITY payload "expanded">]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>&payload;</w:t></w:r></w:p></w:body>
    </w:document>"""

    with pytest.raises(EntitiesForbidden):
        parse_resume("docx", _docx(document))


def test_docx_parser_rejects_excessive_archive_members() -> None:
    document = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>"""

    with pytest.raises(ValueError, match="too many archive members"):
        parse_resume("docx", _docx(document, extra_members=2_000))


@pytest.mark.asyncio
async def test_isolated_parser_round_trip() -> None:
    document = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Isolated parser works</w:t></w:r></w:p></w:body>
    </w:document>"""

    assert await _parse_resume_isolated("docx", _docx(document)) == "Isolated parser works"
