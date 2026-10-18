import os


def test_tests_laufen_ohne_echten_worker_klon():
    # Regression: Auf dem Server existierte WORKDIR, ältere Tests liefen in prepare_issue_branch
    # und der Auto-Deploy rollte zurück. conftest.py muss WORKDIR auf einen leeren Pfad legen.
    assert not os.path.isdir(os.environ["WORKDIR"])
