import io
import os
import sys
import tempfile
from pathlib import Path

# make the api/ package roots importable and force a throwaway sqlite DB
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmpdir = tempfile.mkdtemp(prefix="lda-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["LLM_ENABLED"] = "0"

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@pytest.fixture()
def client():
    from main import app
    from models.db import Base, engine

    Base.metadata.drop_all(engine)  # fresh DB per test
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def make_pdf(lines: list[str]) -> bytes:
    """Build a one-page text PDF (has a real text layer)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 22
    c.save()
    return buf.getvalue()


@pytest.fixture()
def invoice_pdf() -> bytes:
    return make_pdf([
        "INVOICE",
        "Invoice No: INV-2025-00042",
        "Invoice date: 15.03.2025",
        "Supplier: Muster Logistik GmbH",
        "USt-ID: DE123456789",
        "Total amount: EUR 1.234,56",
        "IBAN: DE89 3704 0044 0532 0130 00",
        "VAT 19%",
    ])
