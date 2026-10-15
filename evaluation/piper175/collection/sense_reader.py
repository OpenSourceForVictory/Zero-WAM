"""Exclusive raw Sense serial reader. Never writes bytes to the device."""
import fcntl
import json
import os
from pathlib import Path
import select
import subprocess
import termios


class SenseReader:
    def __init__(self, port):
        self.port=str(Path(port).resolve(strict=True))
        busy=subprocess.run(['fuser',self.port],capture_output=True)
        if busy.returncode!=1: raise RuntimeError('Sense port occupied or ownership check failed')
        self.fd=os.open(self.port,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
        self.old=None; self.exclusive=False; self.buffer=''
        try:
            self.old=termios.tcgetattr(self.fd)
            fcntl.ioctl(self.fd,termios.TIOCEXCL); self.exclusive=True
            a=termios.tcgetattr(self.fd)
            a[0]=a[1]=a[3]=0; a[2]=termios.CS8|termios.CREAD|termios.CLOCAL
            a[4]=a[5]=termios.B460800; a[6][termios.VMIN]=a[6][termios.VTIME]=0
            termios.tcsetattr(self.fd,termios.TCSANOW,a)
            termios.tcflush(self.fd,termios.TCIFLUSH)
        except BaseException:
            self.close(); raise

    def read(self, timeout=.02):
        if select.select([self.fd],[],[],timeout)[0]:
            chunk=os.read(self.fd,8192)
            if not chunk: raise RuntimeError('Sense serial disconnected')
            self.buffer+=chunk.decode('utf-8',errors='strict')
        decoder=json.JSONDecoder(); rows=[]
        while True:
            start=self.buffer.find('{')
            if start<0:
                self.buffer=''; break
            self.buffer=self.buffer[start:]
            try: value,end=decoder.raw_decode(self.buffer)
            except json.JSONDecodeError:
                if len(self.buffer)>8192: raise RuntimeError('Malformed Sense serial stream')
                break
            self.buffer=self.buffer[end:]
            if isinstance(value,dict) and 'AS5047' in value: rows.append(value)
        return rows

    def close(self):
        if self.fd is None: return
        try:
            if self.old is not None: termios.tcsetattr(self.fd,termios.TCSANOW,self.old)
        finally:
            try:
                if self.exclusive: fcntl.ioctl(self.fd,termios.TIOCNXCL)
            finally: os.close(self.fd); self.fd=None
