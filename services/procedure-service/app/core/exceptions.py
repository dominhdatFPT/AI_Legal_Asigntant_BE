"""
Custom exceptions cho procedure-service.
Định nghĩa các ngoại lệ nghiệp vụ cụ thể để phân loại HTTP Status Code chính xác
thay vì so khớp chuỗi (string matching) dễ lỗi.
"""


class ProcedureServiceException(Exception):
    """Ngoại lệ cơ sở cho procedure-service."""
    def __init__(self, message: str, code: str = "PROCEDURE_SERVICE_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


class ApplicationNotFoundError(ProcedureServiceException):
    """Hồ sơ không tồn tại trong hệ thống."""
    def __init__(self, message: str = "Không tìm thấy hồ sơ yêu cầu.", code: str = "APPLICATION_NOT_FOUND"):
        super().__init__(message, code)


class ApplicationLockedError(ProcedureServiceException):
    """Hồ sơ đã nộp hoặc đang xử lý, không thể chỉnh sửa hoặc xóa."""
    def __init__(self, message: str = "Hồ sơ đã được nộp hoặc bị khóa, không thể chỉnh sửa.", code: str = "APPLICATION_LOCKED"):
        super().__init__(message, code)


class ApplicationValidationError(ProcedureServiceException):
    """Dữ liệu hồ sơ không hợp lệ hoặc thiếu trường bắt buộc."""
    def __init__(self, message: str = "Dữ liệu hồ sơ không hợp lệ.", code: str = "VALIDATION_ERROR"):
        super().__init__(message, code)


class ApplicationPermissionError(ProcedureServiceException):
    """Người dùng không có quyền truy cập hoặc chỉnh sửa hồ sơ này."""
    def __init__(self, message: str = "Bạn không có quyền thao tác trên hồ sơ này.", code: str = "FORBIDDEN"):
        super().__init__(message, code)
