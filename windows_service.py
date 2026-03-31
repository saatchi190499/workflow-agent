import os
import subprocess
import sys
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil


class WorkflowAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "WorkflowAgentService"
    _svc_display_name_ = "Workflow Agent Service"
    _svc_description_ = "Runs Workflow Agent FastAPI app locally on 127.0.0.1:9000."

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.proc = None
        self.base_dir = Path(__file__).resolve().parent
        self.log_dir = self.base_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.stdout_log = self.log_dir / "service_stdout.log"
        self.stderr_log = self.log_dir / "service_stderr.log"

    def _python_executable(self) -> str:
        configured = os.getenv("WORKFLOW_AGENT_PYTHON", "").strip()
        if configured:
            return configured

        venv_python = self.base_dir / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return str(venv_python)

        return sys.executable

    def _load_service_env_file(self) -> dict:
        env_file = self.base_dir / "service.env"
        if not env_file.exists():
            return {}

        loaded = {}
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                loaded[key] = value
        return loaded

    def _build_env(self) -> dict:
        env = os.environ.copy()
        env.update(self._load_service_env_file())
        return env

    def _start_agent_process(self):
        python_exe = self._python_executable()
        run_script = self.base_dir / "run.py"
        if not run_script.exists():
            raise FileNotFoundError(f"run.py not found at {run_script}")

        stdout_fh = open(self.stdout_log, "a", encoding="utf-8")
        stderr_fh = open(self.stderr_log, "a", encoding="utf-8")

        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        self.proc = subprocess.Popen(
            [python_exe, str(run_script)],
            cwd=str(self.base_dir),
            stdout=stdout_fh,
            stderr=stderr_fh,
            creationflags=creationflags,
            env=self._build_env(),
        )

    def _stop_agent_process(self):
        if not self.proc or self.proc.poll() is not None:
            return

        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self._stop_agent_process()
        self.ReportServiceStatus(win32service.SERVICE_STOPPED)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("WorkflowAgentService starting")
        try:
            self._start_agent_process()
        except Exception as exc:
            servicemanager.LogErrorMsg(f"WorkflowAgentService failed to start: {exc}")
            raise

        while True:
            wait_result = win32event.WaitForSingleObject(self.stop_event, 1000)
            if wait_result == win32event.WAIT_OBJECT_0:
                break

            if self.proc and self.proc.poll() is not None:
                servicemanager.LogErrorMsg(
                    f"WorkflowAgentService worker exited with code {self.proc.returncode}"
                )
                break


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(WorkflowAgentService)
