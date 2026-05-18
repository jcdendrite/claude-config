import calculator


def test_add() -> None:
    assert calculator.add(2, 3) == 5


def test_subtract() -> None:
    assert calculator.subtract(5, 3) == 2
