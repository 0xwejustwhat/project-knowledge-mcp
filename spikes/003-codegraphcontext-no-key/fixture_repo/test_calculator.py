from calculator import build_default_calculator


def test_double():
    assert build_default_calculator().double(3) == 6
