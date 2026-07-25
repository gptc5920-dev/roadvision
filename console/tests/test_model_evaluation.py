from django.test import SimpleTestCase

from console.management.commands.evaluate_pothole_model import box_iou, polygon_iou, score_predictions


class ModelEvaluationMetricTests(SimpleTestCase):
    def test_ranked_average_precision_penalizes_high_confidence_false_positive(self):
        ground_truth = {1: [{"box": [0.2, 0.2, 0.6, 0.6]}]}
        predictions = [
            {"image_id": 1, "confidence": 0.9, "box": [0.7, 0.7, 0.9, 0.9]},
            {"image_id": 1, "confidence": 0.8, "box": [0.2, 0.2, 0.6, 0.6]},
        ]
        average_precision, true_positives, false_positives = score_predictions(
            predictions,
            ground_truth,
            0.5,
            lambda prediction, target: box_iou(prediction["box"], target["box"]),
        )
        self.assertAlmostEqual(average_precision, 0.5)
        self.assertEqual(true_positives, [0, 1])
        self.assertEqual(false_positives, [1, 0])

    def test_polygon_iou_uses_mask_area(self):
        triangle = [[0.1, 0.1], [0.8, 0.1], [0.4, 0.7]]
        distant = [[0.8, 0.8], [0.95, 0.8], [0.9, 0.95]]
        self.assertEqual(polygon_iou(triangle, triangle, 100, 100), 1.0)
        self.assertEqual(polygon_iou(triangle, distant, 100, 100), 0.0)
