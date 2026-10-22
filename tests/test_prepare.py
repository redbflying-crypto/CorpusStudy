"""Phase 8: getting the corpus and index ready on start, and the Docker files' hygiene."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from corpusstudy import prepare as prep
from corpusstudy.config import REPO_ROOT, Settings


@pytest.fixture
def world(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data",
                        imports_dir=tmp_path / "imports", index_dir=tmp_path / "idx")  # fmt: skip
    monkeypatch.setattr(prep, "get_settings", lambda: settings)
    calls = SimpleNamespace(run=0, build=0, imported=[], status="fresh", lock={})

    def fake_run(write_resources=True):
        assert write_resources is False  # never rewrites committed tables or knowledge
        calls.run += 1
        out = settings.data_dir / "processed"
        (out / "commentary").mkdir(parents=True, exist_ok=True)
        for name in ("KJV.jsonl", "LSG1910.jsonl"):
            (out / name).write_text("{}\n")
        for source in prep.COMMENTARY_SOURCES.values():
            if prep.raw_path(source, settings.data_dir / "raw").exists():
                (out / "commentary" / f"{source.id}.jsonl").write_text("{}\n")
        return {}, {}

    def fake_import(source_id, path, raw_dir):
        source = prep.COMMENTARY_SOURCES[source_id]
        target = prep.raw_path(source, raw_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        calls.lock[f"{source.id}/{source.filename}"] = prep.sha256(target)
        calls.imported.append(source_id)
        return target

    def fake_build():
        calls.build += 1
        calls.status = "fresh"
        return {"counts": {"bible": 1}}

    monkeypatch.setattr(prep, "run", fake_run)
    monkeypatch.setattr(prep, "import_file", fake_import)
    monkeypatch.setattr(prep, "load_lock", lambda: dict(calls.lock))
    monkeypatch.setattr(prep, "build_index", fake_build)
    monkeypatch.setattr(prep, "index_status", lambda path=None: calls.status)
    return SimpleNamespace(settings=settings, calls=calls)


def test_first_start_builds_everything_then_nothing(world):
    world.calls.status = "missing"
    assert prep.prepare(log=lambda m: None) == ["built the corpus", "built the index"]
    assert world.calls.run == 1 and world.calls.build == 1
    assert prep.prepare(log=lambda m: None) == []  # second start: up to date
    assert world.calls.run == 1 and world.calls.build == 1


def test_commentaries_are_imported_when_new_or_changed(world):
    imports = world.settings.imports_dir
    imports.mkdir()
    (imports / "mhcc.txt").write_text("Matthew Henry, version 1")
    done = prep.prepare(log=lambda m: None)
    assert world.calls.imported == ["henry-concise"] and "built the corpus" in done
    assert prep.prepare(log=lambda m: None) == []  # unchanged: nothing to do
    (imports / "mhcc.txt").write_text("Matthew Henry, version 2")
    prep.prepare(log=lambda m: None)
    assert world.calls.imported == ["henry-concise", "henry-concise"] and world.calls.run == 2


def test_a_reviewed_concept_makes_the_index_stale(world):
    prep.prepare(log=lambda m: None)
    world.calls.status = "stale"  # e.g. okf verify changed a concept
    assert prep.prepare(log=lambda m: None) == ["built the index"]


def test_failures_are_reported(world, monkeypatch):
    def offline(write_resources=True):
        raise OSError("Temporary failure in name resolution")

    monkeypatch.setattr(prep, "run", offline)
    with pytest.raises(prep.PrepareError, match="could not build the corpus"):
        prep.prepare(log=lambda m: None)

    report = SimpleNamespace(errors=["Ps.119: checksum mismatch"])
    monkeypatch.setattr(prep, "run", lambda write_resources=True: ({"KJV": report}, {}))
    with pytest.raises(prep.PrepareError, match="checksum mismatch"):
        prep.prepare(log=lambda m: None)


# --------------------------------------------------------------------------- Docker files


def test_docker_files_keep_secrets_out_and_the_page_local():
    ignore = (REPO_ROOT / ".dockerignore").read_text().split()
    assert ".env" in ignore and ".env.*" in ignore and "data" in ignore and "imports" in ignore
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "ANTHROPIC_API_KEY" not in dockerfile and "USER app" in dockerfile
    assert '"--prepare"' in dockerfile
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text())
    service = compose["services"]["corpusstudy"]
    assert all(p.startswith("127.0.0.1:") for p in service["ports"])
    assert "./knowledge:/app/knowledge:ro" in service["volumes"]
    assert service["env_file"] == [{"path": ".env", "required": False}]
    assert service["environment"]["CORPUS_DATA_DIR"] == "/app/data"
    assert Path(REPO_ROOT / "imports" / "README.md").exists()
