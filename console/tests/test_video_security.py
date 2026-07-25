from django.test import SimpleTestCase, override_settings

from console.views import read_video_stream_metadata


class StreamSecurityTests(SimpleTestCase):
    @override_settings(ALLOW_PRIVATE_STREAMS=False)
    def test_private_stream_is_blocked_before_opening(self):
        with self.assertRaisesRegex(ValueError, "Private-network streams are disabled"):
            read_video_stream_metadata("http://127.0.0.1/camera")

    def test_stream_credentials_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "credentials"):
            read_video_stream_metadata("rtsp://user:password@example.com/feed")
