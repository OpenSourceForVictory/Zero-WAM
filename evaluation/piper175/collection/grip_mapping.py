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


def limited_probe_target(base_m, initial_opening, opening, previous_m, dt):
    """Small relative test: ±5 mm from initial feedback, ramp at 5 mm/s.

    0..70 mm is a conservative software envelope, not verified mechanical travel.
    """
    if not all(math.isfinite(x) for x in (base_m,initial_opening,opening,previous_m,dt)):
        raise ValueError('Non-finite gripper target inputs')
    if not 0<=base_m<=.07 or not 0<=previous_m<=.07 or not 0<=opening<=1 or not 0<=initial_opening<=1 or dt<0:
        raise ValueError('Gripper test envelope exceeded')
    target=min(.07,max(0.,base_m+(opening-initial_opening)*.005))
    step=.005*min(dt,.05)
    return previous_m+min(step,max(-step,target-previous_m))
