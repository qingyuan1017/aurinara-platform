import io
import zipfile
from pathlib import Path

import duckdb
import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from aurinara_platform.auth import Principal
from aurinara_platform.config import Settings
from aurinara_platform.models import (
    RepositoryFileVersion,
    RepositoryJob,
    RepositoryNode,
    RepositoryPdfAnnotationRevision,
    RepositoryVersionProfile,
    StoredObject,
    Study,
    User,
)
from aurinara_platform.problems import Problem
from aurinara_platform.rich_repository import RichRepositoryService
from aurinara_platform.schemas import PdfAnnotationCreate, PdfAnnotationPatch


class BytesStorage:
    def __init__(self, values=None):
        self.values = values or {}

    def read_bytes(self, key, *, max_bytes=None):
        value = self.values[key]
        if max_bytes is not None and len(value) > max_bytes:
            raise ValueError("too large")
        return value

    def iter_bytes(self, key, *, chunk_size=1024 * 1024, max_bytes=None):
        value = self.read_bytes(key, max_bytes=max_bytes)
        for offset in range(0, len(value), chunk_size):
            yield value[offset : offset + chunk_size]

    def write_bytes(self, key, data, media_type, sha256):
        self.values[key] = data
        return None

    def write_stream(self, key, chunks, media_type, sha256, *, size_bytes=None):
        del media_type, sha256, size_bytes
        self.values[key] = b"".join(chunks)
        return None


class CleanScanner:
    def scan(self, object_id):
        return True


class RepositoryBoundary:
    def __init__(self, node, version, stored):
        self.node = node
        self.version = version
        self.stored = stored

    def resolve_version(self, session, version_id, principal):
        del session, principal
        if version_id != self.version.id:
            raise Problem(404, "missing", "missing")
        return self.node, self.version, self.stored

    def get_node(self, session, node_id, principal, *, write=False):
        del session, principal, write
        if node_id != self.node.id:
            raise Problem(404, "missing", "missing")
        return self.node


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.write(output)
    return output.getvalue()


def _ooxml(marker: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(marker, "<root/>")
    return output.getvalue()


def test_signature_detection_covers_supported_formats_and_spoofing(tmp_path: Path) -> None:
    kind, media, metadata = RichRepositoryService.detect(_pdf_bytes())
    assert (kind, media, metadata["page_count"]) == ("pdf", "application/pdf", 1)
    assert RichRepositoryService.detect(_ooxml("word/document.xml"))[:2] == (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert RichRepositoryService.detect(_ooxml("xl/workbook.xml"))[0] == "xlsx"
    assert RichRepositoryService.detect(_ooxml("ppt/presentation.xml"))[0] == "pptx"

    parquet_path = tmp_path / "sample.parquet"
    connection = duckdb.connect()
    connection.execute(
        "COPY (SELECT 1 AS id, 'Alice' AS name) TO ? (FORMAT PARQUET)", [str(parquet_path)]
    )
    connection.close()
    kind, _, metadata = RichRepositoryService.detect(parquet_path.read_bytes())
    assert kind == "parquet"
    assert metadata["row_count"] == 1
    assert RichRepositoryService.detect(b"name;age\r\nAlice;42\r\n")[0] == "csv"
    assert RichRepositoryService.detect(b"%PDF-not-a-real-pdf")[0] != "pdf"
    assert RichRepositoryService.detect(b"PK\x03\x04not-a-zip")[0] != "docx"


def test_csv_override_is_recorded() -> None:
    kind, _, metadata = RichRepositoryService.detect(
        "name|city\nJos\xe9|Montr\xe9al\n".encode("windows-1252"),
        {"delimiter": "|", "encoding": "windows-1252", "has_header": True},
    )
    assert kind == "csv"
    assert metadata["csv"] == {
        "delimiter": "|",
        "encoding": "windows-1252",
        "has_header": True,
    }


def test_analysis_jobs_are_idempotent_and_profiles_are_version_scoped(session) -> None:
    actor = User(issuer="issuer", subject="actor")
    session.add(actor)
    session.flush()
    study = Study(code="RICH", display_name="Rich", created_by=actor.id)
    session.add(study)
    session.flush()
    stored = StoredObject(
        study_id=study.id,
        owner_product="platform",
        classification="internal",
        sha256="a" * 64,
        size_bytes=10,
        media_type="text/csv",
        object_key="source",
        status="available",
        created_by=actor.id,
    )
    node = RepositoryNode(
        study_id=study.id,
        kind="file",
        display_name="data.csv",
        normalized_name="data.csv",
        source_product="platform",
        classification="internal",
        required_read_action="platform.repository.read",
        required_write_action="platform.repository.write",
        source_resource_type="repository_upload",
        source_status="available",
        current_version_number=1,
        created_by=actor.id,
        updated_by=actor.id,
    )
    session.add_all([stored, node])
    session.flush()
    version = RepositoryFileVersion(
        node_id=node.id, version_number=1, object_id=stored.id, created_by=actor.id
    )
    session.add(version)
    session.flush()

    from aurinara_platform.repository import RepositoryService

    RepositoryService.queue_analysis(session, version)
    RepositoryService.queue_analysis(session, version)
    session.flush()
    assert session.get(RepositoryVersionProfile, version.id) is not None
    assert len(session.scalars(select(RepositoryJob)).all()) == 1

    storage = BytesStorage({"source": b"id,name\n1,Alice\n2,Bob\n"})
    service = RichRepositoryService(Settings(environment="test"), object(), storage, CleanScanner())
    result = service._analyze(session, version.id)
    profile = session.get(RepositoryVersionProfile, version.id)
    assert result == {"file_kind": "csv", "processing_status": "ready"}
    assert profile is not None
    assert profile.metadata_json["row_count"] == 2
    assert profile.preview_object_id is not None


def test_pdf_annotation_optimistic_concurrency_and_history(session) -> None:
    actor = User(issuer="issuer", subject="actor")
    session.add(actor)
    session.flush()
    study = Study(code="PDF", display_name="PDF", created_by=actor.id)
    session.add(study)
    session.flush()
    stored = StoredObject(
        study_id=study.id,
        owner_product="platform",
        classification="internal",
        sha256="b" * 64,
        size_bytes=20,
        media_type="application/pdf",
        object_key="pdf",
        status="available",
        created_by=actor.id,
    )
    node = RepositoryNode(
        study_id=study.id,
        kind="file",
        display_name="file.pdf",
        normalized_name="file.pdf",
        source_product="platform",
        classification="internal",
        required_read_action="platform.repository.read",
        required_write_action="platform.repository.write",
        source_resource_type="repository_upload",
        source_status="available",
        current_version_number=1,
        created_by=actor.id,
        updated_by=actor.id,
    )
    session.add_all([stored, node])
    session.flush()
    version = RepositoryFileVersion(
        node_id=node.id, version_number=1, object_id=stored.id, created_by=actor.id
    )
    session.add(version)
    session.flush()
    session.add(
        RepositoryVersionProfile(
            version_id=version.id,
            file_kind="pdf",
            detected_media_type="application/pdf",
            processing_status="ready",
        )
    )
    session.flush()
    boundary = RepositoryBoundary(node, version, stored)
    service = RichRepositoryService(
        Settings(environment="test"), boundary, BytesStorage(), CleanScanner()
    )
    principal = Principal("issuer", "actor")
    annotation = service.create_annotation(
        session,
        version.id,
        PdfAnnotationCreate(
            page_number=1,
            geometry={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.1},
            comment="Review",
            style={
                "font_family": "Times-Roman",
                "font_size": 14,
                "text_color": "#102030",
                "fill_color": "#fef08a",
                "border_color": "#ca8a04",
                "opacity": 0.45,
            },
        ),
        principal,
        actor,
    )
    updated = service.patch_annotation(
        session,
        annotation.id,
        PdfAnnotationPatch(
            comment="Resolved",
            style={
                "font_family": "Courier",
                "font_size": 12,
                "text_color": "#111827",
                "fill_color": "#bfdbfe",
                "border_color": "#2563eb",
                "opacity": 0.25,
            },
        ),
        1,
        principal,
        actor,
    )
    assert updated.revision == 2
    assert updated.style["font_family"] == "Courier"
    assert updated.style["fill_color"] == "#bfdbfe"
    with pytest.raises(Problem) as conflict:
        service.patch_annotation(
            session, annotation.id, PdfAnnotationPatch(comment="Stale"), 1, principal, actor
        )
    assert conflict.value.status_code == 412
    assert [item.action for item in session.scalars(select(RepositoryPdfAnnotationRevision))] == [
        "created",
        "updated",
    ]
    latest = session.scalars(
        select(RepositoryPdfAnnotationRevision).order_by(
            RepositoryPdfAnnotationRevision.revision.desc()
        )
    ).first()
    assert latest is not None
    assert latest.snapshot["style"]["font_size"] == 12


def test_onlyoffice_download_host_is_allowlisted(tmp_path: Path) -> None:
    service = RichRepositoryService(
        Settings(environment="test"), object(), BytesStorage(), CleanScanner()
    )
    with pytest.raises(Problem) as rejected:
        service._fetch_onlyoffice(
            "https://attacker.example/document.docx", tmp_path / "office-output"
        )
    assert rejected.value.status_code == 422
