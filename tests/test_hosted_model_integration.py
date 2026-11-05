from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.config import ModelBackend, RuntimeConfig
from src.context.token_budget import UsageBudget
from src.errors import ConfigurationError, ModelProtocolError
from src.main import run
from src.models.base import ModelTask
from src.models.mock import MockModelClient
from src.models.openai_compatible import OpenAICompatibleModelClient
from src.models.router import ModelRouter
from src.reasoning.controller import RepairController
from src.workspace.capabilities import CapabilityRegistry
from src.workspace.paths import WorkspacePaths

from .support import make_workspace, runtime_config


class HostedModelIntegrationTests(unittest.TestCase):
    def test_injected_settings_override_development_model_and_use_token_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "task-token"
            token_file.write_text("temporary-token\n", encoding="utf-8")
            with patch.dict(os.environ, {
                "BB_MODEL_BASE_URL": "http://127.0.0.1:9876/v1",
                "BB_MODEL_NAME": "organizer-alias",
                "BB_MODEL_TOKEN_FILE": str(token_file),
                "ARCHIMEDES_MODEL_BACKEND": "mock",
                "ARCHIMEDES_FAST_MODEL": "wrong-model",
            }, clear=True):
                config = RuntimeConfig.from_environment()
            self.assertIs(config.model_backend, ModelBackend.HOSTED_OPENAI_COMPATIBLE)
            self.assertEqual(config.fast_model, "organizer-alias")
            self.assertEqual(config.strong_model, "organizer-alias")
            self.assertEqual(config.model_api_key, "temporary-token")
            self.assertEqual(config.model_timeout_seconds, 620.0)
            self.assertNotIn("temporary-token", repr(config))

    def test_partial_or_unreadable_hosted_settings_fail_without_secret_in_error(self) -> None:
        with patch.dict(os.environ, {"BB_MODEL_NAME": "alias"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "incomplete hosted"):
                RuntimeConfig.from_environment()
        with patch.dict(os.environ, {
            "BB_MODEL_BASE_URL": "http://127.0.0.1:9876/v1",
            "BB_MODEL_NAME": "alias",
            "BB_MODEL_TOKEN_FILE": "/missing/private-token",
        }, clear=True):
            with self.assertRaises(ConfigurationError) as failure:
                RuntimeConfig.from_environment()
        self.assertNotIn("/missing/private-token", str(failure.exception))

    def test_oversized_credential_and_wrong_base_path_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "task-token"
            token_file.write_text("x" * 4097, encoding="utf-8")
            variables = {
                "BB_MODEL_BASE_URL": "http://127.0.0.1:9876/v1",
                "BB_MODEL_NAME": "alias",
                "BB_MODEL_TOKEN_FILE": str(token_file),
            }
            with patch.dict(os.environ, variables, clear=True):
                with self.assertRaisesRegex(ConfigurationError, "oversized"):
                    RuntimeConfig.from_environment()
            token_file.write_text("token", encoding="utf-8")
            variables["BB_MODEL_BASE_URL"] = "http://127.0.0.1:9876/v2"
            with patch.dict(os.environ, variables, clear=True):
                with self.assertRaisesRegex(ConfigurationError, "end in /v1"):
                    RuntimeConfig.from_environment()

    def test_incomplete_or_refused_completion_is_not_accepted(self) -> None:
        for finish_reason, refusal in (("length", None), (None, None), ("stop", "no")):
            with self.subTest(finish_reason=finish_reason, refusal=refusal):
                response = {
                    "choices": [{
                        "finish_reason": finish_reason,
                        "message": {"content": "{}", "refusal": refusal},
                    }]
                }
                with self.assertRaises(ModelProtocolError):
                    OpenAICompatibleModelClient._content(response)
        with self.assertRaises(ModelProtocolError):
            OpenAICompatibleModelClient._content({
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "{}"}},
                    {"finish_reason": "stop", "message": {"content": "{}"}},
                ]
            })

    def test_hosted_deterministic_repair_requires_successful_model_critique(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _ = make_workspace(
                root,
                metadata=json.dumps({"case_id": "generic-abort"}),
                log="configure stopped because target feature is unavailable\n",
            )
            script = repo / "build.sh"
            script.write_text(
                '#!/bin/sh\necho "configure stopped because target feature is unavailable" >&2\nexit 9\n',
                encoding="utf-8",
            )
            mock = MockModelClient({ModelTask.PATCH_CRITIQUE: {"accepted": True, "findings": []}})
            router = ModelRouter(mock, UsageBudget(5, 2, 20_000), 2.0)
            config = replace(
                runtime_config(root),
                model_backend=ModelBackend.HOSTED_OPENAI_COMPATIBLE,
            )
            outcome = RepairController(
                config=config,
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
                model_router=router,
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["build.sh"])
            self.assertEqual(outcome.metrics.model_calls, 1)
            self.assertEqual(router.records[0].task, ModelTask.PATCH_CRITIQUE)
            self.assertNotIn("exit 9", script.read_text(encoding="utf-8"))

    def _run_hosted_generic_abort(
        self, responses: dict[ModelTask, object]
    ) -> tuple[object, ModelRouter, Path]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repo, _ = make_workspace(
            root,
            metadata=json.dumps({"case_id": "generic-abort"}),
            log="configure stopped because target feature is unavailable\n",
        )
        script = repo / "build.sh"
        script.write_text(
            '#!/bin/sh\necho "configure stopped because target feature is unavailable" >&2\nexit 9\n',
            encoding="utf-8",
        )
        router = ModelRouter(MockModelClient(responses), UsageBudget(5, 2, 20_000), 2.0)  # type: ignore[arg-type]
        config = replace(runtime_config(root), model_backend=ModelBackend.HOSTED_OPENAI_COMPATIBLE)
        outcome = RepairController(
            config=config,
            workspace=WorkspacePaths.resolve(root),
            build_feedback=UnavailableBuildFeedbackProvider(),
            model_router=router,
        ).run()
        return outcome, router, script

    def test_hosted_critique_failure_keeps_deterministic_repair(self) -> None:
        outcome, _, script = self._run_hosted_generic_abort({})
        self.assertEqual(outcome.result.modified_paths, ["build.sh"])  # type: ignore[attr-defined]
        self.assertNotIn("exit 9", script.read_text(encoding="utf-8"))

    def test_hosted_critique_rejection_without_alternative_keeps_deterministic_repair(self) -> None:
        outcome, router, script = self._run_hosted_generic_abort(
            {ModelTask.PATCH_CRITIQUE: {"accepted": False, "findings": ["speculative"]}}
        )
        self.assertEqual(outcome.result.modified_paths, ["build.sh"])  # type: ignore[attr-defined]
        self.assertEqual(router.records[0].task, ModelTask.PATCH_CRITIQUE)
        self.assertNotIn("exit 9", script.read_text(encoding="utf-8"))

    def test_hosted_http_error_keeps_repair_and_traces_status_without_token(self) -> None:
        requests: list[str] = []

        class FailingHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["Content-Length"]))
                requests.append(self.path)
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), FailingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, output = make_workspace(
                    root,
                    metadata=json.dumps({"case_id": "generic-abort"}),
                    log="configure stopped because target feature is unavailable\n",
                )
                script = repo / "build.sh"
                script.write_text(
                    '#!/bin/sh\necho "configure stopped because target feature is unavailable" >&2\nexit 9\n',
                    encoding="utf-8",
                )
                token_file = root / "task-token"
                token_file.write_text("secret-task-token", encoding="utf-8")
                with patch.dict(os.environ, {
                    "BB_WORKSPACE": str(root),
                    "BB_MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                    "BB_MODEL_NAME": "organizer-alias",
                    "BB_MODEL_TOKEN_FILE": str(token_file),
                }, clear=True):
                    exit_code = run()
                self.assertEqual(exit_code, 0)
                self.assertNotIn("exit 9", script.read_text(encoding="utf-8"))
                trace = (output / "execution-trace.jsonl").read_text(encoding="utf-8")
                events = [json.loads(line) for line in trace.splitlines()]
                unavailable = [e for e in events if e["action"] == "model_critique_unavailable"]
                self.assertEqual(len(unavailable), 1)
                self.assertIn("HTTP 502", unavailable[0]["attributes"]["detail"])
                for path in output.rglob("*"):
                    if path.is_file():
                        self.assertNotIn("secret-task-token", path.read_text(encoding="utf-8", errors="replace"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(requests, ["/v1/chat/completions"])

    def test_fatal_hosted_failure_is_traced_and_exits_nonzero(self) -> None:
        class FailingHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), FailingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, output = make_workspace(
                    root,
                    metadata=json.dumps({"case_id": "no-deterministic-repair"}),
                    log="mysterious widget failure with no known cause\n",
                )
                (repo / "README").write_text("nothing to repair\n", encoding="utf-8")
                token_file = root / "task-token"
                token_file.write_text("secret-task-token", encoding="utf-8")
                with patch.dict(os.environ, {
                    "BB_WORKSPACE": str(root),
                    "BB_MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                    "BB_MODEL_NAME": "organizer-alias",
                    "BB_MODEL_TOKEN_FILE": str(token_file),
                }, clear=True):
                    exit_code = run()
                self.assertNotEqual(exit_code, 0)
                result = json.loads((output / "agent-result.json").read_text(encoding="utf-8"))
                self.assertEqual(result["status"], "failed")
                events = [
                    json.loads(line)
                    for line in (output / "execution-trace.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                fatal = [e for e in events if e["action"] == "model_unavailable_fatal"]
                self.assertEqual(len(fatal), 1)
                self.assertIn("HTTP 502", fatal[0]["attributes"]["detail"])
                for path in output.rglob("*"):
                    if path.is_file():
                        self.assertNotIn("secret-task-token", path.read_text(encoding="utf-8", errors="replace"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_injected_http_channel_repairs_case_and_sends_supported_request(self) -> None:
        captured: list[dict[str, object]] = []

        class ModelHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers["Content-Length"])
                captured.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "payload": json.loads(self.rfile.read(size)),
                })
                body = json.dumps({
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"content": '{"accepted":true,"findings":[]}'},
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, output = make_workspace(
                    root,
                    metadata=json.dumps({"case_id": "generic-abort"}),
                    log="configure stopped because target feature is unavailable\n",
                )
                script = repo / "build.sh"
                script.write_text(
                    '#!/bin/sh\necho "configure stopped because target feature is unavailable" >&2\nexit 9\n',
                    encoding="utf-8",
                )
                token_file = root / "task-token"
                token_file.write_text("temporary-token", encoding="utf-8")
                with (
                    patch.dict(os.environ, {
                        "BB_WORKSPACE": str(root),
                        "BB_MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                        "BB_MODEL_NAME": "organizer-alias",
                        "BB_MODEL_TOKEN_FILE": str(token_file),
                    }, clear=True),
                    patch("src.main.CapabilityRegistry.discover", return_value=CapabilityRegistry({"bb-build": "/bin/false"})),
                    patch("src.main.BbBuildFeedbackProvider.discover", side_effect=AssertionError("hosted build client must stay disabled")),
                ):
                    exit_code = run()
                result = json.loads((output / "agent-result.json").read_text(encoding="utf-8"))
                self.assertEqual(exit_code, 0)
                self.assertEqual(result["modified_paths"], ["build.sh"])
                self.assertNotIn("exit 9", script.read_text(encoding="utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["path"], "/v1/chat/completions")
        self.assertEqual(captured[0]["authorization"], "Bearer temporary-token")
        payload = captured[0]["payload"]
        assert isinstance(payload, dict)
        self.assertEqual(payload["model"], "organizer-alias")
        self.assertIs(payload["store"], False)
        self.assertEqual(payload["max_completion_tokens"], 8192)
        self.assertNotIn("temperature", payload)
        self.assertNotIn("response_format", payload)


if __name__ == "__main__":
    unittest.main()
