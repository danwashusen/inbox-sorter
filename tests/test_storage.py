import stat

from inbox_sorter.storage import state_dir


def test_state_dir_is_created_private_under_application_support(tmp_path):
    path = state_dir(tmp_path)

    assert path == tmp_path / "Library" / "Application Support" / "inbox-sorter"
    assert path.is_dir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_state_dir_accepts_an_existing_directory(tmp_path):
    assert state_dir(tmp_path) == state_dir(tmp_path)


def test_state_dir_tightens_a_directory_the_user_made(tmp_path):
    made = tmp_path / "Library" / "Application Support" / "inbox-sorter"
    made.mkdir(parents=True)
    made.chmod(0o755)

    assert stat.S_IMODE(state_dir(tmp_path).stat().st_mode) == 0o700
