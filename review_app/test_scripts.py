"""
Tests for the run.py / install.py command builders (stdlib unittest).

    cd review_app && python -m unittest test_scripts -v

Only the pure command-building / selection logic is tested -- nothing is
actually launched or installed.
"""
import sys
import argparse
import unittest

import run
import install


def run_args(**kw):
    base = dict(dev=False, port=8000, host="127.0.0.1",
                no_build=False, no_install=False)
    base.update(kw)
    return argparse.Namespace(**base)


class RunPlanTest(unittest.TestCase):
    def test_built_default_builds_then_serves(self):
        steps = run.built_commands(run_args())
        self.assertEqual(len(steps), 2)
        (build_cmd, _), (serve_cmd, _) = steps
        self.assertIn("run", build_cmd)
        self.assertIn("build", build_cmd)
        self.assertEqual(serve_cmd[:3], [sys.executable, "-m", "uvicorn"])
        self.assertNotIn("--reload", serve_cmd)        # built mode = no reload

    def test_built_no_build_skips_build(self):
        steps = run.built_commands(run_args(no_build=True))
        self.assertEqual(len(steps), 1)                # just uvicorn
        self.assertIn("uvicorn", steps[0][0])

    def test_port_and_host_flow_through(self):
        cmd = run.uvicorn_cmd(run_args(port=9001, host="0.0.0.0"))
        self.assertEqual(cmd[cmd.index("--port") + 1], "9001")
        self.assertEqual(cmd[cmd.index("--host") + 1], "0.0.0.0")

    def test_dev_runs_two_processes_with_reload(self):
        (be_cmd, _), (fe_cmd, _) = run.dev_commands(run_args(dev=True))
        self.assertIn("--reload", be_cmd)
        self.assertIn("uvicorn", be_cmd)
        self.assertEqual(fe_cmd[-2:], ["run", "dev"])

    def test_default_host_is_a_hostname_not_an_ip(self):
        # Regression guard: YouTube's iframe player shows "Video unavailable"
        # when the page origin is a bare IP (127.0.0.1). Must default to a
        # hostname so embeds work out of the box.
        host = run.build_parser().parse_args([]).host
        self.assertEqual(host, "localhost")
        self.assertFalse(host.replace(".", "").isdigit(), "host must not be an IP literal")


class InstallSelectionTest(unittest.TestCase):
    def _args(self, backend=False, frontend=False):
        return argparse.Namespace(backend=backend, frontend=frontend)

    def test_default_installs_both(self):
        self.assertEqual(install.selection(self._args()), (True, True))

    def test_backend_only(self):
        self.assertEqual(install.selection(self._args(backend=True)), (True, False))

    def test_frontend_only(self):
        self.assertEqual(install.selection(self._args(frontend=True)), (False, True))

    def test_commands(self):
        self.assertEqual(install.backend_command()[:3],
                         [sys.executable, "-m", "pip"])
        self.assertIn("requirements.txt", install.backend_command())
        self.assertEqual(install.frontend_command()[-1], "install")


if __name__ == "__main__":
    unittest.main(verbosity=2)
