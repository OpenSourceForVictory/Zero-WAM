"""Launch the dataset ALOHA profile without hardware access."""
import sys
import os
import runpy
from pathlib import Path
from wan_va.configs.va_aloha_cfg import va_aloha_cfg
from wan_va import wan_va_server as server

if __name__ == '__main__':
    if any(a == '--config-name' or a.startswith('--config-name=') for a in sys.argv[1:]):
        raise SystemExit('This entry fixes --config-name=aloha_dataset')
    server.VA_CONFIGS['aloha_dataset'] = va_aloha_cfg
    sys.argv[1:1] = ['--config-name', 'aloha_dataset']
    if os.environ.get('ZERO_WAM_TRACE_ROOT'):
        runpy.run_path(str(Path(__file__).with_name('aloha_trace.py')), run_name='__main__')
    else:
        server.init_logger()
        server.main()
