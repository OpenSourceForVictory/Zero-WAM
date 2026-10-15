"""Read-only settling after one controller reset; never retries hardware commands."""
import time
from arm import feedback, check_motion


def wait_after_reset(arm, timeout=5.0, stable_for=.5, poll=.05, record=lambda row: None):
    deadline = time.monotonic() + timeout
    stable_since = None
    last_problem = 'no stable feedback'
    while time.monotonic() < deadline:
        now = time.monotonic()
        try:
            _, status = feedback(arm)
        except RuntimeError as exc:
            stable_since = None
            last_problem = str(exc)
            record({'time': time.time(), 'ready': False, 'reason': last_problem})
        else:
            msg = status.msg
            # Only communication recovery is tolerated in this bounded window.
            # Teaching, limits and other faults abort immediately.
            if int(msg.ctrl_mode) not in (0, 1) or int(msg.teach_status) != 0:
                raise RuntimeError('Teaching/other control mode during reset recovery')
            if any(getattr(msg.err_status, f'joint_{i}_angle_limit') for i in range(1, 7)):
                raise RuntimeError('Joint limit fault during reset recovery')
            fault = int(msg.arm_status)
            if fault not in (0, 5):
                raise RuntimeError(f'Non-communication fault during reset recovery: {msg}')
            communication = fault == 5 or any(
                getattr(msg.err_status, f'communication_status_joint_{i}') for i in range(1, 7))
            if communication:
                stable_since = None
                last_problem = str(msg)
            else:
                stable_since = now if stable_since is None else stable_since
            record({'time': time.time(), 'ready': not communication, 'status': str(msg)})
            if stable_since is not None and now - stable_since >= stable_for:
                return check_motion(arm)
        time.sleep(poll)
    raise RuntimeError(f'Reset feedback did not stabilize within {timeout}s: {last_problem}')
