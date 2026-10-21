import unittest
from types import SimpleNamespace

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from evaluation.real_aloha.handeye.solver import combine_bases, fit, rigid, solve, transform
from evaluation.real_aloha.handeye.__main__ import detect


class HandeyeTests(unittest.TestCase):
    def dataset(self, mode, noise=False):
        rng = np.random.default_rng(12)

        def random_pose():
            return transform(Rotation.random(random_state=rng).as_matrix(), rng.uniform(-0.4, 0.4, 3))

        solution, fixed = random_pose(), random_pose()
        samples = []
        for _ in range(30):
            robot = random_pose()
            effective = robot if mode == "eye_in_hand" else np.linalg.inv(robot)
            camera = np.linalg.inv(effective @ solution) @ fixed
            if noise:
                camera[:3, 3] += rng.normal(0, 0.0001, 3)
                camera[:3, :3] @= Rotation.from_rotvec(rng.normal(0, 0.0001, 3)).as_matrix()
            samples.append({"base_from_ee": robot.tolist(), "camera_from_target": camera.tolist()})
        return {"mode": mode, "samples": samples}, solution

    def test_both_transform_directions(self):
        for mode, key in (("eye_in_hand", "ee_from_camera"), ("eye_to_hand", "base_from_camera")):
            data, expected = self.dataset(mode)
            result = solve(data)
            np.testing.assert_allclose(result[key], expected, atol=1e-9)
            self.assertLess(result["held_out_residual"]["translation_max_mm"], 1e-6)
            self.assertFalse(result["verified"])

    def test_noisy_measurements(self):
        for mode in ("eye_in_hand", "eye_to_hand"):
            data, expected = self.dataset(mode, noise=True)
            np.testing.assert_allclose(fit(data["samples"], mode), expected, atol=0.001)

    def test_degenerate_and_insufficient_samples(self):
        data, _ = self.dataset("eye_in_hand")
        with self.assertRaises(ValueError):
            solve({**data, "samples": data["samples"][:10]})
        for sample in data["samples"]:
            sample["base_from_ee"] = np.eye(4).tolist()
        with self.assertRaisesRegex(ValueError, "rotation diversity"):
            solve(data)

    def test_invalid_transforms(self):
        for value in (np.zeros((4, 4)), np.full((4, 4), np.nan), np.diag([-1, 1, 1, 1])):
            with self.assertRaises(ValueError):
                rigid(value)

    def test_marker_detection_with_known_depth(self):
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        marker = cv2.aruco.generateImageMarker(dictionary, 0, 160)
        image = np.full((480, 640, 3), 255, dtype=np.uint8)
        image[160:320, 240:400] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        info = SimpleNamespace(width=640, height=480, distortion_model="plumb_bob",
                               K=[800, 0, 320, 0, 800, 240, 0, 0, 1], D=[0.] * 5)
        args = SimpleNamespace(dictionary="DICT_4X4_50", marker_id=0,
                               marker_size=0.08, max_reprojection=1.5)
        result, error = detect(image, info, args)
        self.assertAlmostEqual(result[2, 3], 0.4, delta=0.004)
        self.assertLess(error, 0.1)
        args.marker_id = 1
        with self.assertRaisesRegex(ValueError, "requested ArUco"):
            detect(image, info, args)

    def test_dual_bases_and_point_mapping(self):
        left_from_right = transform(Rotation.from_euler("xyz", [0.1, -0.2, 0.3]).as_matrix(), [0.1, 0.7, 0.03])
        left_from_camera = transform(Rotation.from_euler("xyz", [-0.4, 0.5, 0.2]).as_matrix(), [0.6, 0.2, 0.9])
        results = []
        for arm, matrix in (("left", left_from_camera), ("right", np.linalg.inv(left_from_right) @ left_from_camera)):
            results.append({"mode": "eye_to_hand", "units": "metres", "base_from_camera": matrix.tolist(),
                            "metadata": {"frames": [arm, "front_optical"], "image_topic": "/front/image",
                                         "pose_topic": "/" + arm + "/pose", "intrinsics": {"K": [1, 2, 3]}}})
        output = combine_bases(*results)
        np.testing.assert_allclose(output["left_from_right"], left_from_right, atol=1e-12)
        point_camera = np.array([0.02, -0.01, 0.6, 1.0])
        point_right = np.asarray(results[1]["base_from_camera"]) @ point_camera
        np.testing.assert_allclose(np.asarray(output["base_transforms"])[1] @ point_right,
                                   left_from_camera @ point_camera, atol=1e-12)
        results[1]["metadata"]["frames"][1] = "other_camera"
        with self.assertRaisesRegex(ValueError, "optical frame"):
            combine_bases(*results)


if __name__ == "__main__":
    unittest.main()
