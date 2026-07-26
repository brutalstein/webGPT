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


class ProjectContextError(ToolError):
    pass


class SkillError(ToolError):
    pass


class SkillValidationError(SkillError):
    pass


class SkillInstallError(SkillError):
    pass


class CapabilityError(ToolError):
    """Global executable capability çalışma zamanının kontrollü temel hatası."""


class CapabilityValidationError(CapabilityError):
    pass


class CapabilityInstallError(CapabilityError):
    pass


class CapabilityExecutionError(CapabilityError):
    pass
