"""Hardware-free command conversion and switching state."""
import math


def encoded_target(position):
    if len(position)!=7 or not all(math.isfinite(x) for x in position):
        raise ValueError('Expected seven finite targets')
    grip=round(min(.08,max(0.,position[6]))*1e6)
    if grip<200: grip=0
    joints=[round(math.degrees(x)*1000) for x in position[:6]]
    return joints,grip


def sent_target(position):
    joints,grip=encoded_target(position)
    return [math.radians(x/1000) for x in joints]+[grip/1e6]


class Selection:
    def __init__(self):
        self.side=None; self.generation=0; self.phase='paused'

    def pause(self):
        self.side=None; self.phase='paused'; self.generation+=1

    def prepare(self,side):
        if side not in ('left','right'): raise ValueError('Unknown arm')
        self.pause(); self.side=side; self.phase='preparing'
        return self.generation

    def accept(self,generation):
        return self.phase=='active' and generation==self.generation
