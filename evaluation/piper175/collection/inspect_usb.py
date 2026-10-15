"""Read-only USB topology snapshot; does not open devices or start drivers."""
import datetime
import json
from pathlib import Path


def snapshot():
    root = Path('/sys/bus/usb/devices')
    devices = []
    for p in sorted(root.iterdir()):
        if not (p / 'idVendor').exists():
            continue
        item = {'path': p.name}
        for key in ('idVendor', 'idProduct', 'manufacturer', 'product', 'serial',
                    'speed', 'busnum', 'devnum', 'authorized',
                    'power/control', 'power/runtime_status'):
            try:
                item[key] = (p / key).read_text().strip()
            except OSError:
                pass
        devices.append(item)
    serials = {p.name: str(p.resolve()) for p in Path('/sys/class/tty').glob('ttyUSB*')}
    aliases = {str(p): str(p.resolve()) for p in Path('/dev').glob('pika-sense-*')}
    return {'time': datetime.datetime.now().astimezone().isoformat(),
            'devices': devices, 'serials': serials, 'aliases': aliases}


if __name__ == '__main__':
    print(json.dumps(snapshot(), indent=2, ensure_ascii=False))
