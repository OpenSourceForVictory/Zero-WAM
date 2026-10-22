"""Stream subprocess progress without exposing command arguments or credentials."""
import json
import os
import re
import selectors
import subprocess
import tempfile
import time


def progress_fields(line):
    if line.startswith('ARCHIVE_PROGRESS '):
        data=json.loads(line[len('ARCHIVE_PROGRESS '):])
        return {k:data[k] for k in ('detail','done','total','unit') if k in data}
    match=re.match(r'^\s*([\d,]+)\s+(\d+)%\s+(\S+)\s+(\S+)',line)
    if match:
        return dict(transferred_bytes=int(match[1].replace(',','')),transfer_percent=int(match[2]),
                    speed=match[3],transfer_time=match[4],detail='正在传输当前文件 / 文件批次')
    return {}


def stream_call(argv, report, timeout=120, input=None, **kwargs):
    started=time.monotonic();output=bytearray();pending=b''
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(argv,stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE,stderr=errors,**kwargs) as process:
            try:
                if input is not None:
                    process.stdin.write(input);process.stdin.close()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout,selectors.EVENT_READ)
                    eof=False
                    while not eof:
                        if time.monotonic()-started>timeout:raise TimeoutError('连接或处理超时，可重试')
                        events=selector.select(timeout=1)
                        if not events:report('')
                        for key,_ in events:
                            chunk=os.read(key.fileobj.fileno(),65536)
                            if not chunk:eof=True;break
                            output.extend(chunk);pending+=chunk
                            lines=re.split(b'[\r\n]',pending);pending=lines.pop()
                            for raw in lines:
                                if raw:report(raw.decode(errors='replace'))
                    if pending:report(pending.decode(errors='replace'))
                code=process.wait(timeout=max(1,timeout-(time.monotonic()-started)))
                if code:
                    errors.seek(0);raise RuntimeError(errors.read().decode(errors='replace')[-2000:] or '命令执行失败')
                return bytes(output)
            except BaseException:
                process.kill();process.wait();raise
