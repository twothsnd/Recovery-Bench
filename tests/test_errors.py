from recovery_bench.errors import FatalRunError, raise_if_fatal_api_error


class StatusError(Exception):
    def __init__(self, status_code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code


def test_403_is_fatal() -> None:
    try:
        raise_if_fatal_api_error(StatusError(403, "credit balance too low"))
    except FatalRunError:
        pass
    else:
        raise AssertionError("403 should stop the benchmark run")


def test_quota_message_is_fatal_even_without_status() -> None:
    try:
        raise_if_fatal_api_error(RuntimeError("insufficient_quota: billing hard limit reached"))
    except FatalRunError:
        pass
    else:
        raise AssertionError("quota errors should stop the benchmark run")


def test_403_message_is_fatal_even_without_status_attribute() -> None:
    try:
        raise_if_fatal_api_error(RuntimeError("provider failed with Error code: 403 - Forbidden"))
    except FatalRunError:
        pass
    else:
        raise AssertionError("403 text should stop the benchmark run")


def test_access_denied_message_is_fatal_even_without_status_attribute() -> None:
    try:
        raise_if_fatal_api_error(RuntimeError("Access denied. Insufficient permissions."))
    except FatalRunError:
        pass
    else:
        raise AssertionError("access denied errors should stop the benchmark run")


def test_transient_503_is_not_fatal() -> None:
    raise_if_fatal_api_error(StatusError(503, "overloaded"))
