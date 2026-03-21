from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile


def _decode_text_file(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_docx_text(path: Path) -> str:
    with ZipFile(path) as archive:
        try:
            xml_data = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("DOCX is missing word/document.xml.") from exc
    tree = ElementTree.fromstring(xml_data)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in tree.findall(".//w:p", namespace):
        parts = [
            node.text
            for node in paragraph.findall(".//w:t", namespace)
            if node.text
        ]
        joined = "".join(parts).strip()
        if joined:
            paragraphs.append(joined)
    return "\n\n".join(paragraphs).strip()


def extract_script_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return _decode_text_file(path.read_bytes()).strip()
    if suffix == ".docx":
        return _extract_docx_text(path)
    raise ValueError(f"Unsupported script format: {path.suffix}")

