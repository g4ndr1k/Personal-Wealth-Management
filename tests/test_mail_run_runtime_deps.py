import importlib.util

import finance.api as finance_api


def test_finance_api_mail_run_runtime_dependency_available():
    assert importlib.util.find_spec("httpx") is not None


def test_finance_api_mounts_mail_run_route():
    paths = {route.path for route in finance_api.app.routes}
    assert "/api/mail/run" in paths
