class Calculator:
    """Tiny fixture for CodeGraphContext relationship discovery."""

    def add(self, left: int, right: int) -> int:
        return left + right

    def double(self, value: int) -> int:
        return self.add(value, value)


def build_default_calculator() -> Calculator:
    return Calculator()
