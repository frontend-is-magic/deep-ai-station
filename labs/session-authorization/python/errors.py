"""仅固定公开错误码进入 HTTP 响应。"""


class LabError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)
