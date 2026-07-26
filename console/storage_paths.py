import os
import tempfile
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

from django.conf import settings


def resolve_model_artifact(value):
    """Resolve model records across Windows, Linux, containers, and shared volumes."""
    raw_value = str(value or "")
    stored = Path(raw_value)
    artifact_name = PureWindowsPath(raw_value).name if "\\" in raw_value else stored.name
    candidates = []
    if stored.is_absolute():
        candidates.append(stored)
    else:
        candidates.extend([Path(settings.BASE_DIR) / stored, Path(settings.MEDIA_ROOT) / stored])
    if artifact_name:
        candidates.append(Path(settings.BASE_DIR) / "models" / "registered" / artifact_name)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if candidates else stored


def portable_model_artifact_value(path):
    artifact = Path(path).resolve()
    for root in (Path(settings.BASE_DIR).resolve(), Path(settings.MEDIA_ROOT).resolve()):
        try:
            return artifact.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(artifact)


@contextmanager
def materialized_field_file(field_file):
    """Yield a local path for either filesystem-backed or remote Django files."""
    try:
        local_path = Path(field_file.path)
    except (AttributeError, NotImplementedError, ValueError):
        suffix = Path(getattr(field_file, "name", "")).suffix
        descriptor, temporary_name = tempfile.mkstemp(suffix=suffix)
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        field_file.open("rb")
        try:
            with temporary_path.open("wb") as target:
                chunks = getattr(field_file, "chunks", None)
                if chunks:
                    for chunk in chunks():
                        target.write(chunk)
                else:
                    while chunk := field_file.read(1024 * 1024):
                        target.write(chunk)
            yield temporary_path
        finally:
            field_file.close()
            temporary_path.unlink(missing_ok=True)
    else:
        yield local_path
