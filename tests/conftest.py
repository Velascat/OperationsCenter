import sys

# Guard: refuse to run tests under the bare system Python.
# The bare interpreter has a different package set and produces misleading results.
if sys.prefix == sys.base_prefix:
    raise SystemExit(
        "ERROR: Tests must be run inside the project virtual environment.\n"
        "Activate it first:\n"
        "  source .venv/bin/activate\n"
        "Or invoke pytest through the venv directly:\n"
        "  .venv/bin/pytest\n"
        "Running tests with the bare Python interpreter is not supported."
    )
