import pytest

from jwbot.lock import LockBusy, file_lock


def test_lock_is_exclusive(tmp_path):
    path = tmp_path / "run.lock"
    with file_lock(path):
        assert path.exists()
        with pytest.raises(LockBusy):
            with file_lock(path):
                pass
    assert not path.exists()


def test_lock_released_on_exception(tmp_path):
    path = tmp_path / "run.lock"
    with pytest.raises(ValueError):
        with file_lock(path):
            raise ValueError("boom")
    assert not path.exists()


def test_stale_lock_is_reclaimed(tmp_path):
    import os
    import time

    path = tmp_path / "run.lock"
    path.write_text("999999 0\n")
    old = time.time() - 4000
    os.utime(path, (old, old))

    with file_lock(path, stale_after_s=1800):
        assert path.exists()
    assert not path.exists()
