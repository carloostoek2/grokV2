"""Integration tests for JsonGenerationRefsRepository (item 3, task 5).

Anonymized fixtures (dummy prompts, FAKE_FILE_ID) with the real shape of grok's
generation_refs.json records.
"""

from __future__ import annotations

import json
import time

import pytest

from grokbot.repositories.generation_refs_repo import (
    GENERATION_REF_TTL_SEC,
    JsonGenerationRefsRepository,
)


def load(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_save_with_kie_task_writes_exact_shape(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    repo.save(111, 222, kie_task_id="task-abc", kie_index=2, provider="kie",
              kind="image", prompt="prompt de prueba #1", now=1000.0)
    rec = load(path)["111:222"]
    assert rec == {
        "provider": "kie",
        "kind": "image",
        "prompt": "prompt de prueba #1",
        "created_at": 1000.0,
        "kie_task_id": "task-abc",
        "kie_index": 2,
    }


def test_save_without_task_and_regen_is_noop(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    repo.save(1, 2, kie_task_id="task")  # create the file
    before = path.read_text(encoding="utf-8")
    repo.save(1, 3)  # no task, no regen -> no-op
    assert path.read_text(encoding="utf-8") == before
    # file is not created when absent
    path2 = tmp_path / "absent.json"
    repo2 = JsonGenerationRefsRepository(path2)
    repo2.save(9, 9)
    assert not path2.exists()


def test_save_with_only_regen_omits_kie_keys(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    regen = {"source_file_id": "FAKE_FILE_ID"}
    repo.save(1, 2, regen=regen, now=1000.0)
    rec = load(path)["1:2"]
    assert rec["regen"] == regen
    assert "kie_task_id" not in rec
    assert "kie_index" not in rec
    assert rec["provider"] == "kie"


def test_save_with_owner_uid_writes_top_level_field(tmp_path):
    """R8: owner_uid top-level (fuera del regen opaco) y round-trip por get()."""
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    regen = {"source_file_id": "FAKE_FILE_ID", "mode": "edit"}
    repo.save(30, 5, regen=regen, owner_uid=222222222)  # now real para que get() no prunee
    rec = load(path)["30:5"]
    assert rec["owner_uid"] == 222222222
    assert "owner_uid" not in rec["regen"]  # nunca dentro del payload opaco
    assert repo.get(30, 5)["owner_uid"] == 222222222


def test_save_without_owner_uid_omits_field(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    repo.save(31, 5, regen={"mode": "text"}, now=1000.0)
    assert "owner_uid" not in load(path)["31:5"]


def test_prompt_truncated_to_500(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    repo.save(1, 2, kie_task_id="task", prompt="x" * 600, now=1000.0)
    rec = load(path)["1:2"]
    assert len(rec["prompt"]) == 500


def test_kie_index_clamped(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    repo.save(1, 2, kie_task_id="task", kie_index=99, now=1000.0)
    assert load(path)["1:2"]["kie_index"] == 5
    repo.save(1, 3, kie_task_id="task", kie_index=-3, now=1000.0)
    assert load(path)["1:3"]["kie_index"] == 0


def test_get_existing_and_missing(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    repo.save(1, 2, kie_task_id="task")  # now defaults to real time.time()
    rec = repo.get(1, 2)
    assert rec is not None
    assert rec["kie_task_id"] == "task"
    assert repo.get(1, 999) is None


def test_get_prunes_and_persists_only_when_requested_key_exists(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    now = time.time()
    repo.save(10, 1, kie_task_id="live", now=now)  # live key "10:1"
    data = load(path)
    data["10:1"]["_extra"] = "keep"  # live-record extra key must survive the prune dump
    data["10:2"] = {"provider": "kie", "kind": "image", "prompt": "", "created_at": now - (GENERATION_REF_TTL_SEC + 1)}
    write(path, data)
    rec = repo.get(10, 1)
    assert rec is not None and rec["kie_task_id"] == "live"
    after = load(path)
    assert "10:2" not in after  # expired key pruned + persisted
    assert "10:1" in after
    assert after["10:1"]["_extra"] == "keep"  # live record extras not lost
    assert repo.get(10, 2) is None


def test_get_expired_key_returns_none_without_writing(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    now = time.time()
    repo.save(10, 1, kie_task_id="live", now=now)
    data = load(path)
    data["10:9"] = {"provider": "kie", "created_at": now - (GENERATION_REF_TTL_SEC + 1)}
    write(path, data)
    before = path.read_text(encoding="utf-8")
    assert repo.get(10, 9) is None
    assert path.read_text(encoding="utf-8") == before


def test_extra_keys_in_live_record_preserved_across_other_save(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    now = time.time()
    repo.save(10, 1, kie_task_id="live", now=now)
    data = load(path)
    data["10:1"]["_extra"] = "keep"
    write(path, data)
    repo.save(11, 1, kie_task_id="other", now=now)  # full-dict dump
    assert load(path)["10:1"]["_extra"] == "keep"
    assert "11:1" in load(path)


def test_save_prunes_expired_records(tmp_path):
    """Parity: the TTL prune also runs on save, dropping expired records of other keys."""
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    now = time.time()
    repo.save(10, 1, kie_task_id="live", now=now)
    data = load(path)
    data["10:9"] = {"provider": "kie", "kind": "image", "prompt": "", "created_at": now - (GENERATION_REF_TTL_SEC + 1)}
    write(path, data)
    # a later save must prune the expired record while keeping the live one
    repo.save(11, 1, kie_task_id="other", now=now)
    after = load(path)
    assert "10:9" not in after  # expired pruned by save
    assert "10:1" in after  # live record preserved
    assert "11:1" in after  # new record persisted
    assert after["10:1"]["kie_task_id"] == "live"


def test_regen_opaque_round_trip(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    regen = {
        "source_file_id": "FAKE_FILE_ID",
        "kie_source_ref": {"task_id": "t", "index": 3},
        "nested": [1, 2, {"a": "b"}],
    }
    repo.save(20, 5, regen=regen)  # real now so get() finds a live record
    assert repo.get(20, 5)["regen"] == regen
    assert load(path)["20:5"]["regen"] == regen


def test_regen_opaque_round_trip_integrate_mode(tmp_path):
    """R4 Item 3: el ref opaco guarda la marca integrate_mode sin re-modelarla."""
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    regen = {
        "model_key": "grok",
        "imagine_provider": "xai",
        "imagine_variant": "quality",
        "mode": "edit",
        "prompt": "hazla sonreir",
        "source_file_id": "FAKE_FILE_ID",
        "integrate_mode": True,
    }
    repo.save(40, 5, regen=regen)  # real now so get() finds a live record
    assert repo.get(40, 5)["regen"] == regen
    assert load(path)["40:5"]["regen"] == regen
    # A10: el ref del integrate NUNCA lleva path/file_id de la referencia.
    assert "integrate_ref_path" not in regen


def test_non_dict_top_level_treated_as_empty_and_corrupt_raises(tmp_path):
    path = tmp_path / "generation_refs.json"
    path.write_text("[]", encoding="utf-8")
    repo = JsonGenerationRefsRepository(path)
    repo.save(1, 2, kie_task_id="task", now=1000.0)  # writes a valid dict
    assert load(path)["1:2"]["kie_task_id"] == "task"
    path.write_text("{invalid", encoding="utf-8")
    repo2 = JsonGenerationRefsRepository(path)
    with pytest.raises(json.JSONDecodeError):
        repo2.save(3, 4, kie_task_id="task", now=1000.0)


def test_dump_flags_ensure_ascii_default_true(tmp_path):
    path = tmp_path / "generation_refs.json"
    repo = JsonGenerationRefsRepository(path)
    repo.save(1, 2, kie_task_id="task", prompt="prompt de prueba #1 á", now=1000.0)
    text = path.read_text(encoding="utf-8")
    assert "\\u00e1" in text  # escaped non-ASCII (ensure_ascii default True, D9)
    assert "á" not in text
