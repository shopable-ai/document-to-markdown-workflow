from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from adapters import pdf_resource_slots as slots


@pytest.fixture(autouse=True)
def isolated_slot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(slots.SLOT_ROOT_ENV, str(tmp_path / "slots"))
    for name in slots.SLOT_ENV_VARS.values():
        monkeypatch.delenv(name, raising=False)


def test_machine_policy_has_one_independent_slot_per_heavy_resource():
    assert slots.resource_slot_capacity(slots.LOCAL_PDF_SLOT) == 1
    assert slots.resource_slot_capacity(slots.VISUAL_MODEL_SLOT) == 1
    assert slots.resource_policy_summary() == {
        "local_pdf_capacity": 1,
        "visual_model_capacity": 1,
        "policy": "fixed-singleton-slots",
    }


def test_independent_slots_can_be_held_at_the_same_time():
    with slots.acquire_local_pdf_slot(purpose="local-test") as local:
        with slots.acquire_visual_model_slot(purpose="visual-test") as visual:
            assert local.slot == slots.LOCAL_PDF_SLOT
            assert visual.slot == slots.VISUAL_MODEL_SLOT
            assert local.inherited is False
            assert visual.inherited is False


def test_same_slot_rejects_an_unrelated_thread_nonblocking():
    result: list[str] = []

    def contend() -> None:
        try:
            with slots.acquire_visual_model_slot(
                purpose="thread-contender", blocking=False
            ):
                result.append("acquired")
        except slots.ResourceSlotBusy:
            result.append("busy")

    with slots.acquire_visual_model_slot(purpose="owner"):
        thread = threading.Thread(target=contend)
        thread.start()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert result == ["busy"]


def test_same_slot_blocking_contender_waits_until_owner_releases():
    started = threading.Event()
    acquired = threading.Event()

    def contend() -> None:
        started.set()
        with slots.acquire_local_pdf_slot(purpose="queued-contender"):
            acquired.set()

    with slots.acquire_local_pdf_slot(purpose="owner"):
        thread = threading.Thread(target=contend)
        thread.start()
        assert started.wait(timeout=2)
        assert not acquired.wait(timeout=0.1)

    assert acquired.wait(timeout=2)
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_same_thread_nested_acquisition_is_inherited_and_restores_environment():
    env_name = slots.SLOT_ENV_VARS[slots.LOCAL_PDF_SLOT]
    assert env_name not in os.environ

    with slots.acquire_local_pdf_slot(purpose="outer") as outer:
        owner_value = os.environ[env_name]
        with slots.acquire_local_pdf_slot(purpose="inner", blocking=False) as inner:
            assert inner.inherited is True
            assert inner.token == outer.token
            assert os.environ[env_name] == owner_value

    assert env_name not in os.environ


def test_child_process_inherits_parent_slot_without_self_deadlock():
    root = Path(__file__).resolve().parents[1]
    code = """
from adapters import pdf_resource_slots as slots
with slots.acquire_local_pdf_slot(purpose='child', blocking=False) as lease:
    print('inherited=' + str(lease.inherited).lower())
"""
    with slots.acquire_local_pdf_slot(purpose="parent"):
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "inherited=true"
