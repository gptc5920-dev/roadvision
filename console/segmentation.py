def estimate_detection_mask(frame, bbox, cv2, max_refinement_size=192):
    """Return a visual-only foreground contour for a detection box using bounded GrabCut."""
    import numpy as np

    if frame is None or getattr(frame, "ndim", 0) < 2:
        return []
    height, width = frame.shape[:2]
    if width < 4 or height < 4:
        return []
    x1, y1, x2, y2 = [int(value) for value in bbox]
    x1 = max(0, min(width - 3, x1))
    y1 = max(0, min(height - 3, y1))
    x2 = max(x1 + 3, min(width, x2))
    y2 = max(y1 + 3, min(height, y2))
    box_width = x2 - x1
    box_height = y2 - y1
    roi = frame[y1:y2, x1:x2]
    scale = min(1.0, float(max_refinement_size) / max(box_width, box_height, 1))
    if scale < 1:
        refinement_width = max(3, int(round(box_width * scale)))
        refinement_height = max(3, int(round(box_height * scale)))
        roi = cv2.resize(roi, (refinement_width, refinement_height), interpolation=cv2.INTER_AREA)
    refinement_height, refinement_width = roi.shape[:2]
    inset_x = max(1, min(refinement_width // 3, int(round(refinement_width * 0.03))))
    inset_y = max(1, min(refinement_height // 3, int(round(refinement_height * 0.03))))
    rect = (
        inset_x,
        inset_y,
        max(1, refinement_width - inset_x * 2),
        max(1, refinement_height - inset_y * 2),
    )
    mask = np.zeros((refinement_height, refinement_width), np.uint8)
    background = np.zeros((1, 65), np.float64)
    foreground = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(roi, mask, rect, background, foreground, 2, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return []
    binary = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")
    kernel = np.ones((3, 3), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    minimum_area = max(8, refinement_width * refinement_height * 0.03)
    contours = [contour for contour in contours if cv2.contourArea(contour) >= minimum_area]
    if not contours:
        return []
    contour = max(contours, key=cv2.contourArea)
    epsilon = max(1.0, cv2.arcLength(contour, True) * 0.008)
    contour = cv2.approxPolyDP(contour, epsilon, True)
    inverse_scale = 1.0 / scale
    points = [
        [
            max(x1, min(x2 - 1, x1 + int(round(point[0][0] * inverse_scale)))),
            max(y1, min(y2 - 1, y1 + int(round(point[0][1] * inverse_scale)))),
        ]
        for point in contour
    ]
    return points if len(points) >= 3 else []


def normalized_polygon(points, width, height):
    return [
        [
            round(max(0.0, min(1.0, float(x) / max(width, 1))), 6),
            round(max(0.0, min(1.0, float(y) / max(height, 1))), 6),
        ]
        for x, y in points
    ]


def pixel_polygon(points, width, height):
    return [
        [
            max(0, min(width - 1, int(round(float(x) * width)))),
            max(0, min(height - 1, int(round(float(y) * height)))),
        ]
        for x, y in points
    ]


def draw_mask_overlay(frame, polygon, cv2, color=(255, 0, 255), alpha=0.48):
    import numpy as np

    if not polygon or len(polygon) < 3:
        return
    contour = np.asarray(polygon, dtype=np.int32).reshape((-1, 1, 2))
    overlay = frame.copy()
    cv2.fillPoly(overlay, [contour], color)
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, dst=frame)
    cv2.polylines(frame, [contour], True, color, 2, cv2.LINE_AA)
