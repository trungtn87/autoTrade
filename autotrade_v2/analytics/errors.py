"""Safe archive diagnostic codes; never include credentials/provider payloads."""
class ArchiveConfigurationError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ArchiveRateLimited(RuntimeError):
    def __init__(self, retry_seconds=900):
        self.retry_seconds = max(900, retry_seconds)
        super().__init__('Archive rate limited')
