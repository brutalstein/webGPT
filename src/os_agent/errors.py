class OSErrorBase(RuntimeError):
    """OS uygulamasının kontrollü çalışma hatası."""


class ConfigurationError(OSErrorBase):
    pass


class ProviderError(OSErrorBase):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ClipboardBridgeError(ProviderError):
    pass


class StorageError(OSErrorBase):
    pass


class ToolError(OSErrorBase):
    """Yerel araç katmanının kontrollü temel hatası."""


class ToolValidationError(ToolError):
    pass


class ToolPolicyError(ToolError):
    pass


class ToolProtocolError(ToolError):
    pass


class ToolLoopError(ToolError):
    pass


class WorkspaceError(ToolError):
    pass
