from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import urllib.request
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE_URL = "https://cdn.tse.jus.br/estatistica/sead/odsele"
OFFICES = {
    1: "president",
    3: "governor",
    5: "senator",
    6: "federal_deputy",
    7: "state_deputy",
    8: "state_deputy",
    11: "mayor",
    13: "councillor",
}
MUNICIPAL_OFFICES = {"mayor", "councillor"}
TOTAL_FIELDS = {
    "eligible": "QT_APTOS",
    "turnout": "QT_COMPARECIMENTO",
    "abstention": "QT_ABSTENCOES",
    "totalVotes": "QT_VOTOS",
    "validVotes": "QT_TOTAL_VOTOS_VALIDOS",
    "blankVotes": "QT_VOTOS_BRANCOS",
    "nullVotes": "QT_TOTAL_VOTOS_NULOS",
}


def number(value: str) -> int | None:
    try:
        result = int(value)
    except ValueError:
        return None
    return result if result >= 0 else None


def archive_files(archive: zipfile.ZipFile) -> list[str]:
    files = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    national = [name for name in files if name.endswith("_BRASIL.csv")]
    return national or files


def rows(path: Path) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        for name in archive_files(archive):
            with archive.open(name) as binary:
                reader = csv.DictReader(io.TextIOWrapper(binary, encoding="latin1"), delimiter=";")
                yield from reader


def download(family: str, year: int | None, directory: Path, refresh: bool) -> Path:
    name = family + (f"_{year}" if year else "") + ".zip"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    if target.exists() and not refresh:
        return target
    temporary = target.with_suffix(".download")
    try:
        request = urllib.request.Request(
            f"{BASE_URL}/{family}/{name}", headers={"User-Agent": "BrasilLens/1.0"}
        )
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            temporary.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        with zipfile.ZipFile(temporary) as archive:
            if not archive_files(archive):
                raise ValueError("O arquivo do TSE não contém dados CSV.")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


class ElectionStage:
    """A votação completa vive só no disco temporário da ingestão."""

    def __init__(self, path: Path, year: int, territories: set[str]) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.year = year
        self.territories = territories
        self.crosswalk: dict[str, str] = {}
        self.updated_at: datetime | None = None
        self.processed = 0
        self.timestamps: dict[str, datetime] = {}
        self.excluded = Counter[str]()
        self.sources: list[dict[str, Any]] = []
        self.db.executescript("""
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE;
            CREATE TABLE candidates (id TEXT PRIMARY KEY, office TEXT, scope TEXT,
                elected INTEGER, data TEXT);
            CREATE TABLE votes (office TEXT, round INTEGER, code TEXT, id TEXT, votes INTEGER,
                PRIMARY KEY(office,round,code,id)) WITHOUT ROWID;
            CREATE TABLE parties (office TEXT, round INTEGER, code TEXT, party TEXT, votes INTEGER,
                PRIMARY KEY(office,round,code,party)) WITHOUT ROWID;
            CREATE TABLE totals (office TEXT, round INTEGER, code TEXT, eligible INTEGER,
                turnout INTEGER, abstention INTEGER, totalVotes INTEGER, validVotes INTEGER,
                blankVotes INTEGER, nullVotes INTEGER,
                PRIMARY KEY(office,round,code)) WITHOUT ROWID;
        """)

    def ingest(self, directory: Path, refresh: bool = False) -> None:
        mapping = download("municipio_tse_ibge", None, directory, refresh)
        mapping_hash = hashlib.sha256(mapping.read_bytes()).hexdigest()
        self.sources.append(
            {"url": f"{BASE_URL}/municipio_tse_ibge/{mapping.name}", "sha256": mapping_hash}
        )
        for row in rows(mapping):
            self.crosswalk[str(int(row["CD_MUNICIPIO_TSE"]))] = row["CD_MUNICIPIO_IBGE"]
        for family in [
            "detalhe_votacao_munzona",
            "votacao_partido_munzona",
            "votacao_candidato_munzona",
        ]:
            path = download(family, self.year, directory, refresh)
            digest = hashlib.sha256()
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            self.sources.append(
                {"url": f"{BASE_URL}/{family}/{path.name}", "sha256": digest.hexdigest()}
            )
            print(f"Consolidando {path.name}…", flush=True)
            self.load_rows(family, rows(path))
        self.aggregate()

    def dimensions(self, row: dict[str, str]) -> tuple[str, int, str] | None:
        self.processed += 1
        if int(row["ANO_ELEICAO"]) != self.year or row["CD_TIPO_ELEICAO"] != "2":
            self.excluded["otherElection"] += 1
            return None
        if row["SG_UF"] in {"ZZ", "VT"}:
            self.excluded["outsideGeography"] += 1
            return None
        office = OFFICES.get(int(row["CD_CARGO"]))
        if not office:
            return None
        code = self.crosswalk.get(str(int(row["CD_MUNICIPIO"])))
        if code is None or code not in self.territories:
            raise ValueError(
                f"Município do TSE sem correspondência na malha IBGE: {row['CD_MUNICIPIO']}"
            )
        stamp = f"{row['DT_GERACAO']} {row['HH_GERACAO']}"
        generated = self.timestamps.get(stamp)
        if generated is None:
            generated = datetime.strptime(stamp, "%d/%m/%Y %H:%M:%S").replace(
                tzinfo=ZoneInfo("America/Sao_Paulo")
            )
            self.timestamps[stamp] = generated
        if self.updated_at is None or generated > self.updated_at:
            self.updated_at = generated
        return office, int(row["NR_TURNO"]), code

    def load_rows(self, family: str, source: Iterator[dict[str, str]]) -> None:
        batch: list[tuple[Any, ...]] = []
        candidates: dict[str, tuple[Any, ...]] = {}
        if family == "detalhe_votacao_munzona":
            columns = list(TOTAL_FIELDS)
            sql = (
                f"INSERT INTO totals VALUES ({','.join('?' for _ in range(10))}) ON "
                f"CONFLICT(office,round,code) DO UPDATE SET "
                + ",".join(
                    f"{c}=CASE WHEN {c} IS NULL OR excluded.{c} IS NULL THEN NULL ELSE "
                    f"{c}+excluded.{c} END"
                    for c in columns
                )
            )
        else:
            table, key = (
                ("votes", "id") if family == "votacao_candidato_munzona" else ("parties", "party")
            )
            sql = (
                f"INSERT INTO {table} VALUES (?,?,?,?,?) "
                f"ON CONFLICT(office,round,code,{key}) "
                "DO UPDATE SET votes=votes+excluded.votes"
            )
        for row in source:
            dimensions = self.dimensions(row)
            if dimensions is None:
                continue
            office, election_round, code = dimensions
            if family == "detalhe_votacao_munzona":
                batch.append(
                    (*dimensions, *(number(row[field]) for field in TOTAL_FIELDS.values()))
                )
            elif family == "votacao_partido_munzona":
                nominal = number(row["QT_VOTOS_NOMINAIS_VALIDOS"])
                legend = number(row["QT_TOTAL_VOTOS_LEG_VALIDOS"])
                if nominal is not None and legend is not None and nominal + legend > 0:
                    batch.append((*dimensions, row["SG_PARTIDO"], nominal + legend))
            else:
                candidate_id = row["SQ_CANDIDATO"]
                if number(candidate_id) is None:
                    continue
                scope = (
                    "BR"
                    if office == "president"
                    else code
                    if office in MUNICIPAL_OFFICES
                    else code[:2]
                )
                status = row["DS_SIT_TOT_TURNO"]
                elected = election_round if status.startswith("ELEITO") else None
                previous = candidates.get(candidate_id)
                if previous and previous[3]:
                    elected = max(elected or 0, previous[3])
                data = {
                    "id": candidate_id,
                    "name": row["NM_URNA_CANDIDATO"],
                    "number": row["NR_CANDIDATO"],
                    "party": row["SG_PARTIDO"],
                    "partyNumber": row["NR_PARTIDO"],
                    "status": status,
                }
                candidates[candidate_id] = (
                    candidate_id,
                    office,
                    scope,
                    elected,
                    previous[4]
                    if previous and previous[3] and not status.startswith("ELEITO")
                    else json.dumps(data, ensure_ascii=False),
                )
                votes = number(row["QT_VOTOS_NOMINAIS_VALIDOS"])
                if votes is not None and votes > 0:
                    batch.append((*dimensions, candidate_id, votes))
            if len(batch) >= 20000:
                self.db.executemany(sql, batch)
                batch.clear()
                self.save_candidates(candidates)
        self.db.executemany(sql, batch)
        self.save_candidates(candidates)
        self.db.commit()

    def save_candidates(self, candidates: dict[str, tuple[Any, ...]]) -> None:
        self.db.executemany(
            """INSERT INTO candidates VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            elected=COALESCE(MAX(elected,excluded.elected),elected,excluded.elected),
            data=CASE WHEN excluded.elected IS NOT NULL OR elected IS NULL THEN excluded.data
            ELSE data END""",
            candidates.values(),
        )
        candidates.clear()

    def aggregate(self) -> None:
        self.db.execute("DELETE FROM totals WHERE round NOT IN (1,2)")
        # Turnos futuros presentes como linhas vazias não entram no catálogo.
        self.db.execute(
            "DELETE FROM totals WHERE (office,round) IN (SELECT office,round FROM totals "
            "GROUP BY office,round HAVING SUM(totalVotes)=0)"
        )
        for table, key in [("votes", "id"), ("parties", "party")]:
            condition = " AND office NOT IN ('mayor','councillor')" if table == "votes" else ""
            self.db.execute(
                f"INSERT INTO {table} SELECT office,round,substr(code,1,2),{key},SUM(votes) "
                f"FROM {table} WHERE length(code)=7{condition} GROUP BY "
                f"office,round,substr(code,1,2),{key}"
            )
            condition = " AND office='president'" if table == "votes" else ""
            self.db.execute(
                f"INSERT INTO {table} SELECT office,round,'BR',{key},SUM(votes) FROM {table} "
                f"WHERE length(code)=7{condition} GROUP BY office,round,{key}"
            )
        sums = ",".join(f"CASE WHEN COUNT({c})=COUNT(*) THEN SUM({c}) END" for c in TOTAL_FIELDS)
        for expression in ["substr(code,1,2)", "'BR'"]:
            self.db.execute(
                f"INSERT INTO totals SELECT office,round,{expression},{sums} FROM totals WHERE "
                f"length(code)=7 GROUP BY office,round,{expression}"
            )
        for table, key in [("votes", "id"), ("parties", "party")]:
            self.db.execute(
                f"CREATE TABLE top_{table} AS SELECT * FROM (SELECT *, ROW_NUMBER() "
                f"OVER(PARTITION BY office,round,code ORDER BY votes DESC,{key}) AS rank FROM "
                f"{table}) WHERE rank<=2"
            )
            self.db.execute(f"CREATE INDEX ix_top_{table} ON top_{table}(office,round,code)")
        self.db.commit()

    def result_rows(self) -> Iterator[dict[str, Any]]:
        columns = ["office", "round", "territory_code", *TOTAL_FIELDS]
        for values in self.db.execute("SELECT * FROM totals"):
            raw = dict(zip(columns, values, strict=True))
            office, election_round, code = values[:3]
            summary = {field: raw[field] for field in TOTAL_FIELDS}
            leaders = list(
                self.db.execute(
                    "SELECT id,votes FROM top_votes "
                    "WHERE office=? AND round=? AND code=? ORDER BY rank",
                    (office, election_round, code),
                )
            )
            parties = list(
                self.db.execute(
                    "SELECT party,votes FROM top_parties "
                    "WHERE office=? AND round=? AND code=? ORDER BY rank",
                    (office, election_round, code),
                )
            )
            summary["leaders"] = [{"id": candidate, "votes": votes} for candidate, votes in leaders]
            summary["party"] = parties[0][0] if parties else None
            summary["partyVotes"] = parties[0][1] if parties else None
            summary["candidateTie"] = len(leaders) > 1 and leaders[0][1] == leaders[1][1]
            summary["partyTie"] = len(parties) > 1 and parties[0][1] == parties[1][1]
            yield {
                "year": self.year,
                "office": office,
                "round": election_round,
                "territory_code": code,
                "data": summary,
            }
        yield from self.representation_rows()

    def representation_rows(self) -> Iterator[dict[str, Any]]:
        by_scope: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for _candidate_id, office, scope, data in self.db.execute(
            "SELECT id,office,scope,data FROM candidates WHERE elected IS NOT NULL"
        ):
            candidate = json.loads(data)
            by_scope[office, scope].append(candidate)
            if scope != "BR":
                by_scope[office, "BR"].append(candidate)
            if len(scope) == 7:
                by_scope[office, scope[:2]].append(candidate)
        for (office, scope), candidates in by_scope.items():
            counts = Counter(candidate["party"] for candidate in candidates).most_common()
            summary = {
                "representatives": len(candidates),
                "party": counts[0][0],
                "partySeats": counts[0][1],
                "partyTie": len(counts) > 1 and counts[0][1] == counts[1][1],
                "leaders": [{"id": candidates[0]["id"], "votes": None}]
                if len(candidates) == 1
                else [],
            }
            codes = [scope]
            if office not in MUNICIPAL_OFFICES:
                codes += [
                    code
                    for code in sorted(self.territories)
                    if len(code) == 7
                    and (scope == "BR" if office == "president" else code.startswith(scope))
                ]
                if office == "president":
                    codes += [
                        code for code in sorted(self.territories) if len(code) == 2 and code != "BR"
                    ]
            for code in codes:
                yield {
                    "year": self.year,
                    "office": office,
                    "round": 0,
                    "territory_code": code,
                    "data": summary,
                }

    def candidate_rows(self) -> Iterator[dict[str, Any]]:
        sql = (
            "SELECT * FROM candidates WHERE elected IS NOT NULL OR id IN (SELECT id FROM top_votes)"
        )
        for candidate_id, office, scope, elected, data in self.db.execute(sql):
            yield {
                "year": self.year,
                "candidate_id": candidate_id,
                "office": office,
                "scope_code": scope,
                "elected_round": elected,
                "data": json.loads(data),
            }

    def metadata(self) -> dict[str, Any]:
        contests = [
            {
                "office": office,
                "rounds": [
                    row[0]
                    for row in self.db.execute(
                        "SELECT DISTINCT round FROM totals WHERE office=? ORDER BY round", (office,)
                    )
                ],
            }
            for (office,) in self.db.execute("SELECT DISTINCT office FROM totals ORDER BY office")
        ]
        return {
            "year": self.year,
            "status": "partial" if self.year == 2026 else "ok",
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
            "contests": contests,
            "sources": self.sources,
            "excluded": dict(self.excluded),
            "note": (
                "Eleições ordinárias. Votos no exterior e em trânsito sem município da malha "
                "não integram estes resumos. Representantes são os eleitos no pleito, sem "
                "acompanhamento de substituições posteriores. "
            ),
        }
