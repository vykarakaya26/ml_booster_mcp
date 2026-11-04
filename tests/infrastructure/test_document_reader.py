import httpx
import pypdfium2 as pdfium
import pytest
import respx

from mlbooster.application.errors import ProviderUnavailable, UnexpectedResponse
from mlbooster.infrastructure.document_reader import PdfDocumentReader
from mlbooster.infrastructure.http_client import SecureHttpClient

URL = "https://www.rekabet.gov.tr/Dosya/karar.pdf"
ROBOTS = "https://www.rekabet.gov.tr/robots.txt"


def _blank_pdf() -> bytes:
    document = pdfium.PdfDocument.new()
    document.new_page(200, 200)
    target = bytearray()

    class Sink:
        def write(self, data):
            target.extend(data)

    document.save(Sink())
    document.close()
    return bytes(target)


@pytest.fixture
def reader():
    return PdfDocumentReader(SecureHttpClient(rps=None, max_retries=0))


@respx.mock
def test_reads_a_pdf_when_robots_allows(reader):
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    respx.get(URL).mock(return_value=httpx.Response(200, content=_blank_pdf()))
    assert reader.read(URL) == [""]


@respx.mock
def test_robots_rules_are_respected(reader):
    respx.get(ROBOTS).mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /Dosya/\n"))
    pdf = respx.get(URL)
    with pytest.raises(ProviderUnavailable, match="robots"):
        reader.read(URL)
    assert not pdf.called


@respx.mock
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(302, headers={"location": "https://login.example/"}), ProviderUnavailable),
        (httpx.Response(403), ProviderUnavailable),
        (httpx.Response(200, text="<html>"), UnexpectedResponse),
        (httpx.Response(200, content=b"%PDF-broken"), UnexpectedResponse),
    ],
)
def test_anything_but_a_public_pdf_stops_the_read(reader, response, error):
    respx.get(ROBOTS).mock(return_value=httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
    respx.get(URL).mock(return_value=response)
    with pytest.raises(error):
        reader.read(URL)
