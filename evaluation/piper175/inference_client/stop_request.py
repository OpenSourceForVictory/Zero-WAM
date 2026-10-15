"""Normal operator stop, distinct from a hardware/inference fault."""


class StopRequested(RuntimeError):
    """End the client without changing the arm controller's hold state."""
