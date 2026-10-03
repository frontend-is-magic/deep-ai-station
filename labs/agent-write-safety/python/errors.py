"""Only these stable errors cross the teaching HTTP boundary."""


class LabError(Exception):
    def __init__(self, status: int, code: str, *, persist: bool = False):
        super().__init__(code)
        self.status = status
        self.code = code
        self.persist = persist
