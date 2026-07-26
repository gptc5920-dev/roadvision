import io
from pathlib import Path

from django.test import SimpleTestCase

from console.storage_paths import materialized_field_file


class RemoteOnlyFile:
    name = "uploads/sample.mp4"

    def __init__(self, content):
        self.stream = io.BytesIO(content)

    @property
    def path(self):
        raise NotImplementedError

    def open(self, mode):
        self.stream.seek(0)

    def chunks(self):
        while chunk := self.stream.read(3):
            yield chunk

    def close(self):
        pass


class MaterializedFieldFileTests(SimpleTestCase):
    def test_remote_file_is_materialized_and_removed(self):
        remote = RemoteOnlyFile(b"roadvision")

        with materialized_field_file(remote) as local_path:
            materialized_path = Path(local_path)
            self.assertEqual(materialized_path.suffix, ".mp4")
            self.assertEqual(materialized_path.read_bytes(), b"roadvision")
            self.assertTrue(materialized_path.exists())

        self.assertFalse(materialized_path.exists())
