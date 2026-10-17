"""Optional high-quality encoding; reference mode keeps the original encoder."""
from pathlib import Path
import av


def encode_h264(images, out_path, fps, codec='libx264', *, crf=18):
    if codec!='libx264' or not 0<=crf<=51:
        raise ValueError('H264 encoding requires libx264 and CRF 0..51')
    path=Path(out_path);path.parent.mkdir(parents=True,exist_ok=True)
    # Refuse to overwrite previous conversion output.
    with path.open('xb') as output:
        with av.open(output,mode='w',format='mp4',options={'movflags':'+faststart'}) as container:
            stream=container.add_stream('libx264',rate=fps)
            stream.width=images.shape[2];stream.height=images.shape[1]
            stream.pix_fmt='yuv420p'
            stream.options={'crf':str(crf),'preset':'medium'}
            for img in images:
                for packet in stream.encode(av.VideoFrame.from_ndarray(img,format='rgb24')):
                    container.mux(packet)
            for packet in stream.encode():container.mux(packet)
