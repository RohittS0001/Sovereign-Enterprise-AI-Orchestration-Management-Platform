class AppException(Exception):
    """
    Base exception for application-level errors.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 500,
    ) -> None:
        self.message = message
        self.status_code = status_code

        super().__init__(message)

        class ConfigurationError(AppException):
            """Raised when application configuration is invalid."""

            def __init__(self, message: str) -> None:
                super().__init__(message, status_code=500)


        class IntegrationError(AppException):
            """Raised when an external integration fails."""

            def __init__(self, message: str) -> None:
                super().__init__(message, status_code=502)


        class ToolExecutionError(AppException):
            """Raised when an MCP/tool operation fails."""

            def __init__(self, message: str) -> None:
                super().__init__(message, status_code=500)


        class RetrievalError(AppException):
            """Raised when knowledge retrieval fails."""

            def __init__(self, message: str) -> None:
                super().__init__(message, status_code=500)