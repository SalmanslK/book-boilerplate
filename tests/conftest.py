"""Tie pytest tests to factory acceptance criteria.

Copy into the repository's root ``conftest.py`` (or load it as a plugin).

- Registers the ``ac`` marker, so ``@pytest.mark.ac('R-001.AC-1')`` tags a test with the
  criterion it proves. The factory's ``[adapter].criterion_marker`` matches that text.
- Adds ``--ac ID``: only tests whose ``ac`` marker names ID run; every other test is
  deselected (reported as deselected, not skipped). A test may carry several ``ac``
  markers and runs for any of them.

Example: ``pytest --ac R-001.AC-1 --junitxml=results.xml``
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--ac",
        action="store",
        default=None,
        metavar="ID",
        help="run only the tests tagged @pytest.mark.ac(ID)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "ac(id): the acceptance criterion this test proves, e.g. R-001.AC-1"
    )


def pytest_collection_modifyitems(config, items):
    wanted = config.getoption("--ac")
    if wanted is None:
        return
    selected, deselected = [], []
    for item in items:
        ids = {marker.args[0] for marker in item.iter_markers(name="ac") if marker.args}
        (selected if wanted in ids else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
