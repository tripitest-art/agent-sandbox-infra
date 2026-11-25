from scripts.platzwaechter import anteil_belegt, genug_platz


def test_anteil_belegt_normal():
    assert anteil_belegt(50, 100) == 0.5


def test_genug_platz():
    assert genug_platz(80, 100, 5) is False
    assert genug_platz(10, 100, 5) is True
