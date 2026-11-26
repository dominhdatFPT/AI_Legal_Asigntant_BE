"""Make collection-time and runtime skips fail a selected integration run."""

import pytest


_OPTIONAL_DEPENDENCIES = ("openapi_schema_validator", "openapi_spec_validator", "pypdfium2")


def pytest_configure(config):
    config.pluginmanager.register(NoSkips(), "three-layer-integration-no-skips")


class NoSkips:
    def __init__(self):
        self.skips = []

    def _record_skip(self, report):
        if not report.skipped:
            return
        reason = report.longrepr[-1] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        if any(reason.startswith(f"Skipped: could not import '{module}':") for module in _OPTIONAL_DEPENDENCIES):
            return
        self.skips.append((report.nodeid, report.longrepr))

    def pytest_collectreport(self, report):
        self._record_skip(report)

    def pytest_runtest_logreport(self, report):
        self._record_skip(report)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.skips:
            terminal = session.config.pluginmanager.get_plugin("terminalreporter")
            if terminal:
                terminal.write_sep("=", "Unexpected integration skips")
                for name, reason in self.skips:
                    terminal.write_line(f"{name}: {reason}")
            if exitstatus == pytest.ExitCode.OK:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
