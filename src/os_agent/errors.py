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
