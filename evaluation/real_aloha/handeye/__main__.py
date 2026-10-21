"""Run with python -m evaluation.real_aloha.handeye --help."""
import argparse
import json
import threading
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from .solver import combine_bases, save_json, solve, transform


def positive(value):
    number = float(value)
    if not np.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Expected a finite positive number")
    return number


def pose_matrix(msg):
    p, q = msg.pose.position, msg.pose.orientation
    quat = np.array([q.x, q.y, q.z, q.w])
    if not np.isfinite(quat).all() or abs(np.linalg.norm(quat) - 1) > 0.01:
        raise ValueError("Invalid pose quaternion")
    return transform(Rotation.from_quat(quat).as_matrix(), [p.x, p.y, p.z])


def detect(image, info, args):
    if (image.shape[1], image.shape[0]) != (info.width, info.height):
        raise ValueError("CameraInfo resolution differs from image")
    if info.distortion_model not in ("plumb_bob", "rational_polynomial"):
        raise ValueError("Only plumb_bob/rational_polynomial raw images supported")
    k, d = np.array(info.K).reshape(3, 3), np.array(info.D)
    if not np.isfinite(k).all() or not np.isfinite(d).all() or min(k[0, 0], k[1, 1]) <= 0:
        raise ValueError("Invalid camera intrinsics")
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, args.dictionary))
    if hasattr(cv2.aruco, "ArucoDetector"):
        corners, ids, _ = cv2.aruco.ArucoDetector(dictionary).detectMarkers(image)
    else:
        corners, ids, _ = cv2.aruco.detectMarkers(image, dictionary)
    if ids is None or np.sum(ids == args.marker_id) != 1:
        raise ValueError("Expected exactly one requested ArUco marker")
    pixels = corners[int(np.flatnonzero(ids.ravel() == args.marker_id)[0])].reshape(4, 2)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    pixels = cv2.cornerSubPix(gray, pixels.astype(np.float32), (3, 3), (-1, -1),
                             (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    h = args.marker_size / 2
    points = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], dtype=float)
    ok, r, t = cv2.solvePnP(points, pixels, k, d, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok or t[2, 0] <= 0:
        raise ValueError("Marker pose estimation failed")
    projected, _ = cv2.projectPoints(points, r, t, k, d)
    error = float(np.sqrt(np.mean(np.sum((projected.reshape(4, 2) - pixels) ** 2, axis=1))))
    if not np.isfinite(error) or error > args.max_reprojection:
        raise ValueError("Marker reprojection error exceeds threshold: %.3f px" % error)
    cv2.aruco.drawDetectedMarkers(image, corners, ids)
    cv2.drawFrameAxes(image, k, d, r, t, args.marker_size / 2)
    return transform(cv2.Rodrigues(r)[0], t), error


def collect(args):
    import rospy
    import message_filters
    from cv_bridge import CvBridge
    from geometry_msgs.msg import PoseStamped
    from sensor_msgs.msg import CameraInfo, Image

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    rospy.init_node("aloha_handeye_capture", anonymous=True)
    bridge, lock = CvBridge(), threading.Lock()
    latest, history = {}, deque(maxlen=500)

    def receive(image, pose):
        with lock:
            latest["pair"] = (image, pose)

    def receive_pose(msg):
        with lock:
            history.append(msg)

    def receive_info(msg):
        with lock:
            latest["info"] = msg

    image_sub = message_filters.Subscriber(args.image_topic, Image, queue_size=1,
                                          buff_size=8 * 1024 * 1024)
    pose_sub = message_filters.Subscriber(args.pose_topic, PoseStamped, queue_size=100)
    pose_sub.registerCallback(receive_pose)
    synchronizer = message_filters.ApproximateTimeSynchronizer(
        [image_sub, pose_sub], queue_size=100, slop=args.max_skew)
    synchronizer.registerCallback(receive)
    info_sub = rospy.Subscriber(args.info_topic, CameraInfo, receive_info, queue_size=1)
    metadata = {k: v for k, v in vars(args).items() if k != "command"}
    dataset = {"schema_version": 1, "mode": args.mode, "metadata": metadata, "samples": []}
    last_stamp = None
    print("Move manually and hold still. ENTER: capture; q: finish. No motion commands are sent.")
    try:
        while not rospy.is_shutdown():
            if input("ENTER capture / q finish > ").strip().lower() == "q":
                break
            try:
                with lock:
                    pair, info, poses = latest.get("pair"), latest.get("info"), list(history)
                if pair is None or info is None:
                    raise ValueError("Waiting for synchronized image/pose and CameraInfo")
                image_msg, pose_msg = pair
                stamps = [image_msg.header.stamp.to_sec(), pose_msg.header.stamp.to_sec()]
                now = rospy.Time.now().to_sec()
                if any(s <= 0 or not 0 <= now - s <= args.max_age for s in stamps):
                    raise ValueError("Invalid or stale image/pose timestamps")
                if abs(stamps[0] - stamps[1]) > args.max_skew or stamps[0] == last_stamp:
                    raise ValueError("Unsynchronized or already captured image")
                if not image_msg.header.frame_id or image_msg.header.frame_id != info.header.frame_id:
                    raise ValueError("Image and CameraInfo optical frame IDs must match")
                frames = [pose_msg.header.frame_id, image_msg.header.frame_id]
                if not frames[0]:
                    raise ValueError("Pose must identify its base frame")
                if "frames" in metadata and metadata["frames"] != frames:
                    raise ValueError("Coordinate frames changed during capture")
                robot = pose_matrix(pose_msg)
                recent = [p for p in poses if stamps[1] - args.still_seconds <= p.header.stamp.to_sec() <= stamps[1]]
                if len(recent) < 3 or stamps[1] - recent[0].header.stamp.to_sec() < args.still_seconds * 0.8:
                    raise ValueError("Not enough pose history to check stationary capture")
                for p in recent:
                    other = pose_matrix(p)
                    angle = Rotation.from_matrix(robot[:3, :3].T @ other[:3, :3]).magnitude()
                    if np.linalg.norm(robot[:3, 3] - other[:3, 3]) > 0.001 or angle > np.deg2rad(0.5):
                        raise ValueError("Hold arm still: >1 mm or >0.5 degree motion detected")
                image = bridge.imgmsg_to_cv2(image_msg, desired_encoding="bgr8").copy()
                raw = image.copy()
                camera, error = detect(image, info, args)
                # Preserve exact camera parameters and forbid mixing configurations.
                intrinsics = {"K": list(info.K), "D": list(info.D), "width": info.width,
                              "height": info.height, "distortion_model": info.distortion_model}
                if "intrinsics" in metadata and metadata["intrinsics"] != intrinsics:
                    raise ValueError("Camera intrinsics changed during capture")
                metadata.update(frames=frames, intrinsics=intrinsics)
                index = len(dataset["samples"])
                for name, pixels in (("raw", raw), ("detected", image)):
                    if not cv2.imwrite(str(output / ("%04d_%s.png" % (index, name))), pixels):
                        raise OSError("Failed to save capture image")
                sample = {"base_from_ee": robot.tolist(), "camera_from_target": camera.tolist(),
                          "image_stamp": stamps[0], "pose_stamp": stamps[1],
                          "reprojection_rms_px": error}
                save_json(output / ("%04d_sample.json" % index), sample)
                dataset["samples"].append(sample)
                last_stamp = stamps[0]
                print("Saved %d samples; reprojection %.3f px" % (index + 1, error))
            except (ValueError, cv2.error) as exc:
                print("Capture rejected:", exc)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        save_json(output / "samples.json", dataset)
        image_sub.unregister()
        pose_sub.unregister()
        info_sub.unregister()
        print("Saved", output / "samples.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("collect", help="ROS1 subscriber-only ArUco capture")
    capture.add_argument("--mode", choices=["eye_in_hand", "eye_to_hand"], required=True)
    capture.add_argument("--image-topic", required=True)
    capture.add_argument("--info-topic", required=True)
    capture.add_argument("--pose-topic", required=True)
    capture.add_argument("--ee-frame", required=True, help="Physical frame represented by pose topic")
    capture.add_argument("--marker-size", type=positive, required=True, help="Black outer square width in metres")
    capture.add_argument("--marker-id", type=int, default=0)
    capture.add_argument("--dictionary", choices=["DICT_ARUCO_ORIGINAL", "DICT_4X4_50", "DICT_5X5_100"], default="DICT_4X4_50")
    capture.add_argument("--max-skew", type=positive, default=0.05)
    capture.add_argument("--max-age", type=positive, default=0.5)
    capture.add_argument("--still-seconds", type=positive, default=0.5)
    capture.add_argument("--max-reprojection", type=positive, default=1.5)
    capture.add_argument("--output", required=True, help="New session directory")
    compute = commands.add_parser("solve", help="Offline fitting and held-out validation")
    compute.add_argument("--input", required=True)
    compute.add_argument("--output", required=True)
    bases = commands.add_parser("bases", help="Combine fixed-camera calibrations into dual-arm base transforms")
    bases.add_argument("--left", required=True, help="Left arm eye_to_hand result JSON")
    bases.add_argument("--right", required=True, help="Right arm eye_to_hand result JSON")
    bases.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "collect":
        collect(args)
    else:
        if args.command == "bases":
            result = combine_bases(json.loads(Path(args.left).read_text()),
                                   json.loads(Path(args.right).read_text()))
        else:
            result = solve(json.loads(Path(args.input).read_text()))
        save_json(args.output, result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
