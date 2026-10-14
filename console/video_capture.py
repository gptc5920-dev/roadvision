from urllib.parse import urlsplit

from django.conf import settings


def open_video_capture(source, cv2):
    """Bound network connection/read waits so stopped cameras can reconnect."""
    if urlsplit(str(source)).scheme.lower() in {"rtsp", "http", "https"}:
        return cv2.VideoCapture(source, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, settings.STREAM_OPEN_TIMEOUT_SECONDS * 1000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, settings.STREAM_READ_TIMEOUT_SECONDS * 1000,
        ])
    return cv2.VideoCapture(source)
