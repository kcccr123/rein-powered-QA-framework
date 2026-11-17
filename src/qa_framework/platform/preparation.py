"""Convert newly available ONNX models in an isolated Editor."""

from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from qa_framework.platform.config import DAGConfig
from qa_framework.platform.trial import stop_player


class PreparationError(RuntimeError):
    """A dependent suite cannot start when model preparation fails."""


@contextmanager
def conversion_project():
    project = Path(tempfile.mkdtemp(prefix="qa-model-conversion-"))
    try:
        yield project
    finally:
        # Windows can retain the Editor's directory handle briefly after process exit.
        for attempt in range(50):
            try:
                shutil.rmtree(project)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.1)


def validate_onnx(path: Path) -> None:
    import onnx

    # Inspect all protobuf messages, including tensors in nested graphs/attributes.
    def check(message):
        if isinstance(message, onnx.TensorProto):
            if message.data_location == onnx.TensorProto.EXTERNAL or message.external_data:
                raise PreparationError(f"External ONNX weights are not supported: {path}")
        for field, value in message.ListFields():
            if field.message_type is None:
                continue
            if field.is_repeated:
                for item in value:
                    check(item)
            else:
                check(value)

    try:
        model = onnx.load_model(str(path), load_external_data=False)
        check(model)
        onnx.checker.check_model(model)
    except PreparationError:
        raise
    except Exception as error:
        raise PreparationError(f"Invalid ONNX model {path}: {error}") from error


def prepare_models(config: DAGConfig, output: Path, paths: list[str], project: Path) -> dict[str, Path]:
    """Convert only newly available paths; the caller owns the per-run cache."""
    output.mkdir()
    paths = list(dict.fromkeys(paths))
    prepared = {path: output / f"model-{index:04d}.sentis" for index, path in enumerate(paths, 1)}
    manifest = [{"source": path, "artifact": str(artifact)} for path, artifact in prepared.items()]
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    log = output / "preparation.log"
    try:
        for path in paths:
            validate_onnx(Path(path))
        (project / "Assets" / "Editor").mkdir(parents=True, exist_ok=True)
        (project / "Packages").mkdir(exist_ok=True)
        (project / "ProjectSettings").mkdir(exist_ok=True)
        (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.3.4f1\n", encoding="utf-8")
        shutil.copyfile(Path(__file__).with_name("converter") / "QaModelConverter.cs",
                        project / "Assets" / "Editor" / "QaModelConverter.cs")
        (project / "Packages" / "manifest.json").write_text(json.dumps({
            "dependencies": {"com.unity.ai.inference": "2.4.1", "com.unity.modules.jsonserialize": "1.0.0"},
        }), encoding="utf-8")
        (project / "conversion.json").write_text(json.dumps({"models": manifest}), encoding="utf-8")
        command = [str(config.unity_editor), "-batchmode", "-nographics", "-quit",
                   "-projectPath", str(project), "-executeMethod", "QaModelConverter.Convert",
                   "-logFile", str(output / "unity.log")]
        options = {"start_new_session": True}
        if os.name == "nt":
            options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        with log.open("wb") as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, **options)
            try:
                code = process.wait(timeout=config.preparation_timeout)
            except subprocess.TimeoutExpired as error:
                raise PreparationError(f"Model conversion timed out after {config.preparation_timeout}s") from error
            finally:
                # Finish killing the owned Editor before removing its temporary project.
                handlers = {}
                for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                    if hasattr(signal, name):
                        sig = getattr(signal, name)
                        handlers[sig] = signal.signal(sig, signal.SIG_IGN)
                try:
                    if process.poll() is None and os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=stream, stderr=subprocess.STDOUT, check=False)
                        process.wait()
                    else:
                        stop_player(process)
                finally:
                    for sig, handler in handlers.items():
                        signal.signal(sig, handler)
        if code != 0:
            raise PreparationError(f"Unity model conversion exited with code {code}; see {output / 'unity.log'}")
        if any(not artifact.is_file() or artifact.stat().st_size == 0 for artifact in prepared.values()):
            raise PreparationError(f"Unity did not export all models; see {output / 'unity.log'}")
    except BaseException as error:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"{type(error).__name__}: {error}\n")
        raise
    return prepared
