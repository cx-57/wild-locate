"""Background analysis and training processes"""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from wildlocate.core.regional import get_region
from wildlocate.core.registry import cleanup_job

ROOT = Path(__file__).resolve().parent


class JobManager:
    """Own one cancellable prediction subprocess at a time."""

    def __init__(self, command=None, worker_mode="predict"):
        self.command = command
        self.worker_mode = worker_mode
        self.lock = threading.Lock()
        self.job = None
        self.process = None
        self.closed = False

    def start(self, payload, username=None):
        with self.lock:
            if self.closed or (self.job and self.job["status"] == "running"):
                raise ValueError("An analysis is already running or the server is stopping.")
            command = list(self.command) if self.command else [
                sys.executable, "-u", "-m", "wildlocate.core.worker", self.worker_mode,
            ]
            if self.command is None and username:
                command.extend(["--account", username])
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(ROOT.parents[1]), env.get("PYTHONPATH")))
            )
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            job = {"id": uuid.uuid4().hex, "status": "running"}
            self.job, self.process = job, process
            threading.Thread(
                target=self._collect, args=(process, job, payload), daemon=True
            ).start()
            return dict(job)

    def _collect(self, process, job, payload):
        try:
            stdout, _stderr = process.communicate(
                json.dumps(payload, allow_nan=False) + "\n", timeout=1800
            )
            response = json.loads(stdout)
            if process.returncode or not isinstance(response, dict):
                raise ValueError("Worker failed")
            if "error" in response:
                update = {
                    "status": "error",
                    "error": str(response["error"]),
                    "code": response.get("code"),
                }
            elif isinstance(response.get("result"), dict):
                json.dumps(response["result"], allow_nan=False)
                update = {"status": "complete", "result": response["result"]}
            else:
                raise ValueError("Invalid worker response")
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            update = {
                "status": "error",
                "error": "Analysis timed out. Try a smaller area.",
            }
        except Exception:
            update = {
                "status": "error",
                "error": (
                    "The analysis process could not complete. "
                    "Check your local models and environmental data."
                ),
            }
        with self.lock:
            if self.job is job and job["status"] == "running":
                job.update(update)

    def status(self, identifier):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            return dict(self.job)

    def cancel(self, identifier):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            if self.job["status"] == "running":
                self.job["status"] = "cancelled"
                if self.process and self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=10)
            return dict(self.job)

    def reset(self):
        with self.lock:
            if self.job and self.job.get("status") == "running":
                self.job["status"] = "cancelled"
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=10)
            self.job = None
            self.process = None

    def close(self):
        self.reset()
        with self.lock:
            self.closed = True


class TrainingManager:
    """Keep one persistent training worker so resolve/prepare/train share state."""

    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.job = None
        self.username = None
        self.closed = False

    def _cleanup_workspace(self, job_id, username):
        if not job_id or not username:
            return
        try:
            cleanup_job(job_id, username=username)
        except (OSError, ValueError):
            pass

    def reset(self):
        with self.lock:
            process = self.process
            job = self.job
            username = self.username
            if job and job.get("status") == "running":
                job["status"] = "cancelled"
            self.process = None
            self.job = None
            self.username = None
        if process and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        if job:
            self._cleanup_workspace(job.get("id"), username)

    def close(self):
        self.reset()
        with self.lock:
            self.closed = True

    def start(self, region, username, query):
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 100:
            raise ValueError("Enter a species name up to 100 characters.")
        region = get_region(region).code
        self.reset()
        with self.lock:
            if self.closed:
                raise ValueError("The server is stopping.")
            job_id = uuid.uuid4().hex
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(ROOT.parents[1]), env.get("PYTHONPATH")))
            )
            command = [
                sys.executable,
                "-u",
                "-m",
                "wildlocate.core.worker",
                "train",
                "--job-id",
                job_id,
                "--region",
                region,
                "--account",
                username,
            ]
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            self.process = process
            self.username = username
            self.job = {
                "id": job_id,
                "region": region,
                "status": "running",
                "action": "resolve",
                "message": "Finding the species on iNaturalist…",
                "logs": [],
            }
            threading.Thread(
                target=self._read_stdout, args=(process, self.job), daemon=True
            ).start()
            threading.Thread(
                target=self._read_stderr, args=(process, self.job), daemon=True
            ).start()
            self._send_locked({"action": "resolve", "query": query.strip()})
            return self._snapshot_locked()

    def _send_locked(self, payload):
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            raise ValueError("The training process is no longer running.")
        self.process.stdin.write(json.dumps(payload, allow_nan=False) + "\n")
        self.process.stdin.flush()

    def _snapshot_locked(self):
        if not self.job:
            raise KeyError("training")
        snapshot = dict(self.job)
        snapshot["logs"] = list(self.job.get("logs", []))
        if isinstance(snapshot.get("result"), dict):
            snapshot["result"] = dict(snapshot["result"])
        return snapshot

    def _read_stdout(self, process, job):
        if not process.stdout:
            return
        for line in process.stdout:
            try:
                event = json.loads(line)
                kind = event.get("event")
                if not isinstance(event, dict) or not isinstance(kind, str):
                    raise ValueError
            except Exception:
                with self.lock:
                    if self.process is process and self.job is job and job["status"] == "running":
                        job.update(
                            status="error",
                            error="Training returned an unreadable response. Please try again.",
                        )
                continue

            with self.lock:
                if self.process is not process or self.job is not job:
                    return
                if kind == "progress":
                    job["message"] = str(event.get("message", "Working…"))
                    continue
                if kind == "error":
                    job.update(
                        status="error",
                        error=str(event.get("message", "Training failed.")),
                        code=event.get("code"),
                    )
                    continue
                result = {k: v for k, v in event.items() if k != "event"}
                job.update(status=kind, result=result)
                job.pop("error", None)
                job.pop("code", None)
                messages = {
                    "resolved": "Species found. Confirm it and check the available data.",
                    "prepared": "Species data is ready. You can start training.",
                    "initialized": "Environmental datasets are ready. Check the species data again.",
                    "completed": "Training complete. Review the model before enabling it.",
                }
                job["message"] = messages.get(kind, "Ready.")
                if kind == "completed" and process.stdin:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return
        with self.lock:
            if self.process is process and self.job is job and job.get("status") == "running":
                job.update(
                    status="error",
                    error="Training stopped unexpectedly. Your enabled models are unchanged.",
                )

    def _read_stderr(self, process, job):
        if not process.stderr:
            return
        for line in process.stderr:
            text = line.strip()
            if not text:
                continue
            with self.lock:
                if self.process is not process or self.job is not job:
                    return
                logs = job.setdefault("logs", [])
                logs.append(text)
                del logs[:-40]

    def status(self, identifier):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            return self._snapshot_locked()

    def action(self, identifier, action):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            if self.job["status"] == "running":
                raise ValueError("Wait for the current training step to finish.")
            if not self.process or self.process.poll() is not None:
                raise ValueError("The training session has ended. Start a new one.")
            allowed = {
                "prepare": {"resolved", "initialized", "error"},
                "train": {"prepared", "error"},
                "initialize": {"resolved", "initialized", "error"},
            }
            if action not in allowed or self.job["status"] not in allowed[action]:
                raise ValueError("That training step is not available yet.")
            self.job.update(status="running", action=action)
            self.job.pop("error", None)
            self.job.pop("code", None)
            self._send_locked({"action": action})
            return self._snapshot_locked()

    def cancel(self, identifier):
        with self.lock:
            if not self.job or self.job["id"] != identifier:
                raise KeyError(identifier)
            process = self.process
            username = self.username
            job_id = self.job["id"]
            self.job["status"] = "cancelled"
            self.job["message"] = "Training cancelled. Your enabled models are unchanged."
            snapshot = self._snapshot_locked()
        if process and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        self._cleanup_workspace(job_id, username)
        return snapshot
