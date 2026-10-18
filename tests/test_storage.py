import stat

import pytest

from inbox_sorter.errors import UserError
from inbox_sorter.storage import StorageError, state_dir


def test_state_dir_is_created_private_under_application_support(tmp_path):
    path = state_dir(tmp_path)

    assert path == tmp_path / "Library" / "Application Support" / "inbox-sorter"
    assert path.is_dir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_state_dir_accepts_an_existing_directory(tmp_path):
    assert state_dir(tmp_path) == state_dir(tmp_path)


def test_state_dir_that_cannot_be_created_is_a_storage_error(tmp_path):
    (tmp_path / "Library").write_text("")  # a file where a directory must go

    with pytest.raises(StorageError, match="cannot create the state directory") as caught:
        state_dir(tmp_path)

    assert isinstance(caught.value, UserError)
    assert not str(caught.value).endswith(": ")


def test_state_dir_tightens_a_directory_the_user_made(tmp_path):
    made = tmp_path / "Library" / "Application Support" / "inbox-sorter"
    made.mkdir(parents=True)
    made.chmod(0o755)

    assert stat.S_IMODE(state_dir(tmp_path).stat().st_mode) == 0o700
