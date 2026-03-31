from control_plane.adapters.plane import PlaneClient


def test_execution_block_parse() -> None:
    client = PlaneClient("http://plane.local", "token", "ws", "proj")
    try:
        parsed = client.parse_execution_metadata(
            """## Execution
repo: repo_a
base_branch: main
mode: goal
open_pr: true

## Goal
Do thing
"""
        )
        assert parsed["repo"] == "repo_a"
        assert parsed["base_branch"] == "main"
        assert parsed["mode"] == "goal"
    finally:
        client.close()
