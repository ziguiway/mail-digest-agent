class AppError(Exception):
    def __init__(self, message: str, code="operation_failed", *, retryable=False, status=400):
        super().__init__(message)
        self.message, self.code, self.retryable, self.status = message, code, retryable, status
