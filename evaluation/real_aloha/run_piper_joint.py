"""Run the IPC's merged joint client, including its original CLI and defaults.

This entry point enables and moves hardware, just like the original client.
Use --help to inspect arguments without connecting to hardware.
"""
from pathlib import Path
import runpy
import sys


def main():
    client_dir = Path(__file__).resolve().parent / "merged_client"
    sys.path.insert(0, str(client_dir))
    runpy.run_path(str(client_dir / "real_robot_inference_merged.py"), run_name="__main__")


if __name__ == "__main__":
    main()
