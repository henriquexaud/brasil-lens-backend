"""Falhas parciais devem ser visíveis também para quem executa a CLI."""

import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ProviderError
from app.jobs import bootstrap, import_geometries, import_territories
from app.jobs._runner import RunReport


@pytest.mark.parametrize("failed_step", ["import_territories", "import_geometries", None])
async def test_bootstrap_propagates_failure_and_restores_arguments(
    monkeypatch, capsys, failed_step
):
    original = ["bootstrap", "--states", "35", "--skip-municipal-geometries"]
    monkeypatch.setattr(sys, "argv", original)
    territories = AsyncMock(return_value=int(failed_step == "import_territories"))
    geometries = AsyncMock(return_value=int(failed_step == "import_geometries"))
    monkeypatch.setattr(import_territories, "main", territories)
    monkeypatch.setattr(import_geometries, "main", geometries)

    result = await bootstrap.main()

    assert result == int(failed_step is not None)
    assert sys.argv is original
    territories.assert_awaited_once()
    assert geometries.await_count == int(failed_step != "import_territories")
    assert ("Ingestão territorial completa." in capsys.readouterr().out) == (failed_step is None)


@pytest.mark.parametrize("job", [import_territories, import_geometries])
async def test_importer_keeps_partial_report_but_returns_failure(monkeypatch, job):
    report = RunReport()
    session = AsyncMock()

    @asynccontextmanager
    async def job_session(**_kwargs):
        yield session, 1, report

    @asynccontextmanager
    async def http_client():
        yield object()

    monkeypatch.setattr(job, "job_session", job_session)
    monkeypatch.setattr(job, "http_client", http_client)
    monkeypatch.setattr(job, "upsert_dataset", AsyncMock(return_value=1))
    if job is import_geometries:
        monkeypatch.setattr(
            job, "_parse_args", lambda: SimpleNamespace(quality="high", skip_municipalities=True)
        )
        monkeypatch.setattr(job, "_fetch_level", AsyncMock(side_effect=ProviderError("offline")))
        monkeypatch.setattr(job, "_report_vertices", AsyncMock())
    else:
        for name in ("fetch_regions", "fetch_states", "fetch_municipalities"):
            monkeypatch.setattr(job.localidades, name, AsyncMock(return_value=[]))

        async def upsert(_session, _records, *, report):
            report.record_failure("territory:unknown", "parent missing")
            report.failed += 1
            return 0

        monkeypatch.setattr(job, "_upsert_level", upsert)
        monkeypatch.setattr(job, "_link_capitals", AsyncMock())

    assert await job.main() == 1
    assert report.failures and report.failed
