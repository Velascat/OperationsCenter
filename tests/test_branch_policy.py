from control_plane.adapters.git import branch_allowed


def test_branch_allowed_with_glob() -> None:
    assert branch_allowed("feature/my-work", ["main", "feature/*"])
    assert not branch_allowed("hotfix/x", ["main", "feature/*"])
