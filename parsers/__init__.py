from .base import StatementResult, Transaction, AccountSummary, InvestmentHolding


def detect_and_parse(*args, **kwargs):
    from .router import detect_and_parse as _detect_and_parse

    return _detect_and_parse(*args, **kwargs)


def detect_bank_and_type(*args, **kwargs):
    from .router import detect_bank_and_type as _detect_bank_and_type

    return _detect_bank_and_type(*args, **kwargs)


def __getattr__(name: str):
    if name == "UnknownStatementError":
        from .router import UnknownStatementError

        return UnknownStatementError
    raise AttributeError(name)
