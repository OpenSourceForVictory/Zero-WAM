"""Pure Sense endpoint mapping; no hardware or ROS calls."""
import math


def normalized_opening(raw_rad, closed_rad, open_rad, tolerance_rad=0.05):
    """Map measured endpoints to 0..1; reject values beyond endpoint tolerance.

    The 0.05 rad tolerance is an explicit provisional software setting, not a
    manufacturer specification. This function does not define robot jaw travel.
    """
    values=(raw_rad,closed_rad,open_rad,tolerance_rad)
    if not all(math.isfinite(x) for x in values):
        raise ValueError('Non-finite Sense angle/configuration')
    if not 0.5 <= open_rad-closed_rad <= 2.5 or not 0 <= tolerance_rad <= .1:
        raise ValueError('Invalid Sense endpoint span or tolerance')
    if raw_rad < closed_rad-tolerance_rad or raw_rad > open_rad+tolerance_rad:
        raise ValueError('Sense angle outside measured endpoint tolerance')
    return min(1.0,max(0.0,(raw_rad-closed_rad)/(open_rad-closed_rad)))


def gripper_target(raw_rad, closed_rad=0.0):
    """pika_ros getDistance conversion, then Piper's 0..80 mm clamp.

    Matches sensor_tools/serial_gripper_imu.cpp and piper_ctrl_single_node.py.
    """
    if not math.isfinite(raw_rad):
        raise ValueError('Non-finite Sense angle')
    if not math.isfinite(closed_rad) or not 0 <= closed_rad <= .1:
        raise ValueError('Invalid measured Sense closed endpoint')
    angle=min(1.67,max(0.,raw_rad-closed_rad))
    def distance(rad):
        theta=math.radians(180.-43.99)-rad
        height=.0325*math.sin(theta)
        return math.sqrt(.058**2-(height-.01456)**2)+.0325*math.cos(theta)
    target=min(.08,max(0.,2*(distance(angle)-distance(0.))))
    return 0. if target<.0002 else target
