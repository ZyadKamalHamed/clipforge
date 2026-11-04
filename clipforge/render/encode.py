"""Turning an edit list into video (SPEC 7.10 steps 1, 3 and 5): one ffmpeg decode per stretch of the source
played straight through, each output frame composed and painted here, and an ffmpeg encode reading the
frames on its stdin. The frames cross to and from ffmpeg over sockets with deep buffers (a macOS pipe moves
64 KB at a time, which made the crossing most of the render), and the decode, the composing and the encode
run side by side on threads. Then the picture and the finished sound are muxed, and a video over
Instagram's peak bitrate is encoded again under a cap."""

from __future__ import annotations

import bisect
import contextlib
import functools
import json
import os
import queue
import socket
import subprocess
import tempfile
import threading
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import numpy as np

from clipforge import children
from clipforge.errors import ToolError
from clipforge.media.ffmpeg import HDR_TRANSFERS, QUIET, ffmpeg_bin, ffprobe_bin, probe, run
from clipforge.render.compositor import compose, lay, overlays
from clipforge.render.edl import EDL, EPS, GAMEPLAY_BOX, H_OUT, W_OUT, Span, fit_box
from clipforge.render.layouts import cover_size
from clipforge.render.timeline import Segment, Timeline, output_fps
from clipforge.vision.decode import ClipSource

GOP_S = 2.0  # a closed GOP every 2 s
RUN_GAP_S = 2.0  # a jump forward shorter than this is read through rather than sought
PEAK_LIMIT = 25_000_000  # Instagram's ceiling on any second of video, in bits
HD_WIDTH, SD_HEIGHT = 1280, 576  # an untagged video this wide, or taller than SD, is BT.709 to players
PROGRESS_EVERY = 10  # frames
WAIT_S = 600  # for an encode to finish once its last frame is in
STALL_S = 120  # an ffmpeg that takes or gives no frame for this long has hung
SOCKET_BUF = 4 << 20  # bytes each way on a frame socket (macOS allows up to 8 MB)
AHEAD = 4  # decoded frames read ahead of the one being composed
COMPOSERS = 3  # frames composed at once
QUEUED = 3  # composed frames waiting for the encoder

TO_709 = (
    "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
    "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
)
COLOUR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
MP4 = ["-movflags", "+faststart", "-use_editlist", "0"]
CODECS = {
    "fast": ["-c:v", "h264_videotoolbox", "-q:v", "65", "-profile:v", "high", "-allow_sw", "0"],
    "best": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "16M", "-bufsize", "32M"]
    + ["-profile:v", "high"],
}
CAPPED_BUFSIZE = "9M"  # with -maxrate 16M no second can pass 25 Mbit
NTSC_RATES = {23.976: "24000/1001", 29.97: "30000/1001", 59.94: "60000/1001"}

Progress = Callable[[float], None]


def rate(fps: float) -> str:
    """``fps`` as ffmpeg should read it, the NTSC rates exactly."""
    return next((r for f, r in NTSC_RATES.items() if abs(fps - f) < 0.01), f"{fps:g}")


def encoder_args(encoder: str, fps: float, *, capped: bool = False) -> list[str]:
    """The output side of an encode at ``fps``: BT.709 limited range yuv420p, H.264 High from VideoToolbox
    ("fast") or libx264 ("best", and always when ``capped``), no B-frames, a closed GOP every 2 s, the moov
    first and no edit list."""
    if encoder not in CODECS:
        raise ValueError(f"unknown encoder {encoder!r}")
    codec = list(CODECS["best" if capped else encoder])
    if capped:
        codec[codec.index("-bufsize") + 1] = CAPPED_BUFSIZE
    gop = str(round(GOP_S * fps))
    return [
        *("-vf", TO_709, "-pix_fmt", "yuv420p", *codec, "-bf", "0"),
        *("-g", gop, "-keyint_min", gop, "-flags", "+cgop", *COLOUR, *MP4),
    ]


def _tail(file) -> str:
    if file.closed:
        return ""
    file.seek(0)
    return file.read().decode("utf-8", "replace")[-800:]


def _socketpair() -> tuple[socket.socket, socket.socket]:
    """A connected pair to carry one ffmpeg's frames, ours first, with SOCKET_BUF deep buffers."""
    pair = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    for end in pair:
        for option in (socket.SO_SNDBUF, socket.SO_RCVBUF):
            with contextlib.suppress(OSError):  # a smaller buffer is only slower
                end.setsockopt(socket.SOL_SOCKET, option, SOCKET_BUF)
    return pair


def _start(args: list[str], *, err, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL):
    """Starts ffmpeg with ``args``; a socket given for its stdin or stdout is handed over, ours closed."""
    try:
        return children.popen(args, stdin=stdin, stdout=stdout, stderr=err)
    finally:
        for end in (stdin, stdout):
            if isinstance(end, socket.socket):
                end.close()


class _Ahead:
    """Up to ``count`` frames of ``size`` bytes read from ``sock`` on a thread of their own, at most AHEAD
    ahead of the one taken, until the socket ends. Iterate to take them (ToolError when none comes for
    STALL_S); ``stop`` once its writer is gone."""

    def __init__(self, sock: socket.socket, size: int, count: int):
        self._q: queue.Queue[np.ndarray | None] = queue.Queue(AHEAD)
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._read, args=(sock, size, count), daemon=True)
        self._thread.start()

    def _read(self, sock: socket.socket, size: int, count: int) -> None:
        try:
            for _ in range(count):
                buf = np.empty(size, np.uint8)  # not zeroed: every byte is read into
                view, got = memoryview(buf), 0
                while got < size and (k := sock.recv_into(view[got:])):
                    got += k
                if got < size or not self._put(buf):
                    break
        except OSError:
            pass  # the decode stopped under it
        finally:
            self._put(None)

    def _put(self, item: np.ndarray | None) -> bool:
        while not self._stopping.is_set():
            with contextlib.suppress(queue.Full):
                self._q.put(item, timeout=0.1)
                return True
        return False

    def __iter__(self) -> Iterator[np.ndarray]:
        while True:
            try:
                buf = self._q.get(timeout=STALL_S)
            except queue.Empty:
                raise ToolError("decode_timeout", "Couldn't decode this part of the video.") from None
            if buf is None:
                return
            yield buf

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join()


class Encoder:
    """An ffmpeg encoding the 1080 x 1920 BGR frames written to it into ``out``. A thread of its own sends
    them, so the next frames are composed while it takes them."""

    def __init__(self, out: Path, *, fps: float, encoder: str = "fast"):
        self.out = Path(out).absolute()  # a name starting with "-" must not read as an option
        args = [
            *(ffmpeg_bin(), *QUIET, "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W_OUT}x{H_OUT}"),
            *("-framerate", rate(fps), "-i", "pipe:0", *encoder_args(encoder, fps), str(self.out)),
        ]
        self._err = tempfile.TemporaryFile()  # noqa: SIM115 (open as long as the encode; closed by close and kill)
        self._sock, theirs = _socketpair()
        try:
            self._proc = _start(args, err=self._err, stdin=theirs)
        except BaseException:
            self._sock.close()
            self._err.close()
            raise
        self._q: queue.Queue[np.ndarray | None] = queue.Queue(QUEUED)
        self._broken: OSError | None = None
        self._sender = threading.Thread(target=self._send, daemon=True)
        self._sender.start()
        self.frames = 0

    def _send(self) -> None:
        while (frame := self._q.get()) is not None:
            if self._broken is None:  # after a failure the rest are dropped, so writers never wait on it
                try:
                    self._sock.sendall(memoryview(frame).cast("B"))
                except OSError as err:
                    self._broken = err

    def _failed(self, code: str = "encode_failed") -> ToolError:
        """Stops the encode and says what went wrong, from what ffmpeg wrote before it stopped."""
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()
        details = _tail(self._err)
        self.kill()
        return ToolError(code, "The video encoder stopped with an error. Try again.", details=details)

    def write(self, frame: np.ndarray) -> None:
        """Queues ``frame`` for the encoder, which must not change it after; raises ToolError once the
        encoder has stopped taking frames."""
        if self._broken is None:
            try:
                self._q.put(np.ascontiguousarray(frame, np.uint8), timeout=STALL_S)
            except queue.Full:
                raise self._failed("encode_timeout") from None
        if self._broken is not None:
            raise self._failed() from self._broken
        self.frames += 1

    def _finish_sending(self) -> bool:
        """Lets the sender send the queued frames and stop; False when the encoder takes none for STALL_S."""
        if self._sender.is_alive():
            try:
                self._q.put(None, timeout=STALL_S)
            except queue.Full:
                return False
            self._sender.join(STALL_S)
        return not self._sender.is_alive()

    def close(self) -> None:
        """Waits for the encode to finish, raising ToolError when it failed."""
        if not self._finish_sending():
            raise self._failed("encode_timeout")
        if self._broken is not None:
            raise self._failed() from self._broken
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_WR)  # the end of the frames
        try:
            code = self._proc.wait(timeout=WAIT_S)
        except subprocess.TimeoutExpired as err:
            raise self._failed("encode_timeout") from err
        if code != 0:
            raise self._failed()
        self.kill()

    def kill(self) -> None:
        """Stops the encode if it is still going and lets go of its socket, thread and log; safe to call
        again."""
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()
        self._finish_sending()  # with the encoder gone every send fails at once, so the queue drains
        self._sock.close()
        self._err.close()


@functools.lru_cache(maxsize=32)
def _colours(path: str, stamp: tuple[int, int]) -> tuple[str | None, bool]:
    args = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries"]
    out = run([*args, "stream=color_space,color_transfer,width,height", "-of", "json", path], timeout_s=60)
    streams = json.loads(out.stdout or "{}").get("streams") or [{}]
    s = streams[0]
    hdr = s.get("color_transfer") in HDR_TRANSFERS
    if s.get("color_space") not in (None, "", "unknown", "reserved"):
        return None, hdr  # tagged: ffmpeg reads the tag
    hd = int(s.get("width") or 0) >= HD_WIDTH or int(s.get("height") or 0) > SD_HEIGHT
    return ("bt709" if hd else "bt601"), hdr


def source_colours(path: Path) -> tuple[str | None, bool]:
    """The colour matrix to read an untagged video with, as players do: BT.709 from 1280 wide or taller
    than 576, BT.601 below; None for a tagged video, whose tag ffmpeg follows. And whether the video is HDR
    (PQ or HLG)."""
    st = Path(path).stat()
    return _colours(str(Path(path).absolute()), (st.st_mtime_ns, st.st_size))


def decode_frames(
    src: ClipSource,
    start_s: float,
    count: int,
    *,
    fps: float,
    size: tuple[int, int],
    speed: float = 1.0,
    loop: bool = False,
) -> Iterator[np.ndarray]:
    """``count`` BGR frames of ``src`` at ``size`` on the output grid: frame j shows source time
    ``start_s + j * speed / fps``. ``loop`` plays the file again from its start when it ends. A file that
    ends early repeats its last frame; one with no frame at all raises ToolError."""
    if count <= 0:
        return
    w, h = size
    at = src.file_time(start_s)
    seek = max(0.0, at)
    matrix, hdr = source_colours(src.path)
    scale = f"scale={w}:{h}:flags=lanczos" + (f":in_color_matrix={matrix}" if matrix else "")
    if hdr:  # BT.2408: reference white to white, BT.2020 to BT.709, brighter highlights clipped (SPEC 7.2)
        scale += ":out_transfer=bt709:out_primaries=bt709"
    graph = f"setpts=(PTS+{seek - at:.6f}/TB)/{speed:.9g},fps={rate(fps)}:start_time=0,{scale},format=bgr24"
    args = [ffmpeg_bin(), *QUIET, "-hwaccel", "videotoolbox"]
    if loop:
        args += ["-stream_loop", "-1"]
    if seek > 0:
        args += ["-ss", f"{seek:.6f}"]
    args += ["-i", str(Path(src.path).absolute()), "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", graph]
    args += ["-frames:v", str(count), "-f", "rawvideo", "pipe:1"]
    ours, theirs = _socketpair()
    with tempfile.TemporaryFile() as err, contextlib.closing(ours):
        proc = _start(args, err=err, stdout=theirs)
        frames = _Ahead(ours, w * h * 3, count)
        try:
            last, made = None, 0
            for buf in frames:
                last = buf.reshape(h, w, 3)
                made += 1
                yield last
            if last is None:
                proc.wait(timeout=60)
                raise ToolError(
                    "decode_failed", "Couldn't decode this part of the video.", details=_tail(err)
                )
            for _ in range(count - made):
                yield last
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            frames.stop()


def runs(segments: Sequence[Segment]) -> list[list[Segment]]:
    """The segments in the groups one decode each plays: forward in the source at one speed, each less than
    RUN_GAP_S after the one before."""
    out: list[list[Segment]] = []
    for s in segments:
        last = out[-1][-1] if out else None
        if (
            last is not None
            and s.speed == last.speed
            and -EPS <= s.src_start - last.src_end < RUN_GAP_S - EPS
        ):
            out[-1].append(s)
        else:
            out.append([s])
    return out


def run_frames(
    src: ClipSource, run: Sequence[Segment], *, fps: float, size: tuple[int, int]
) -> Iterator[np.ndarray]:
    """The output frames of a run of segments, from one decode that reads through the gaps between them."""
    a, speed = run[0].src_start, run[0].speed
    wanted, end = [], 0
    for s in run:
        first = max(end, round((s.src_start - a) * fps / speed))
        end = first + Timeline([s], fps).frames
        wanted.append((first, end))
    frames = decode_frames(src, a, end, fps=fps, size=size, speed=speed)
    try:
        k = 0
        for j, frame in enumerate(frames):
            while j >= wanted[k][1]:
                k += 1
            if j >= wanted[k][0]:
                yield frame
    finally:
        frames.close()


def _frame(frame, span: Span, f: int, gameplay_frame, sprites) -> np.ndarray:
    canvas = compose(frame, span, f, gameplay_frame=gameplay_frame)
    lay(canvas, sprites)
    return canvas


def render_video(
    edl: EDL,
    src: ClipSource,
    out: Path,
    *,
    gameplay: ClipSource | None = None,
    progress: Progress | None = None,
) -> None:
    """Renders the edit list's picture into ``out`` (no sound). ``progress`` hears the share done every
    PROGRESS_EVERY frames and 1.0 at the end; an exception from it stops the render, and any failure leaves
    no ``out`` behind. An edit list with a gameplay span needs ``gameplay``. COMPOSERS frames are composed
    at once on threads (OpenCV and numpy let go of the interpreter while they work); the sprites are drawn
    here first, since Pillow's fonts are not to be shared between threads."""
    out = Path(out)
    spans = edl.spans or [
        Span(start=0.0, end=edl.duration, layout="fit", boxes=[fit_box(edl.src_width, edl.src_height)])
    ]
    with_game = any(s.layout == "gameplay" for s in spans)
    if with_game and gameplay is None:
        raise ToolError(
            "gameplay_missing",
            "This clip's gameplay video is missing. Add it to the library again or pick another layout.",
        )
    fps, size = edl.fps, (edl.src_width, edl.src_height)
    tl = edl.timeline()
    total = tl.frames
    starts = [round(s.start * fps) for s in spans]
    open_: list[Iterator[np.ndarray]] = []
    pending: deque[Future[np.ndarray]] = deque()
    done = 0
    enc = Encoder(out, fps=fps, encoder=edl.style.encoder)
    pool = ThreadPoolExecutor(COMPOSERS, thread_name_prefix="compose")

    def hand_over() -> None:
        nonlocal done
        enc.write(pending.popleft().result())
        done += 1
        if progress is not None and done % PROGRESS_EVERY == 0 and done < total:
            progress(done / total)

    try:
        game = None
        if with_game:
            assert gameplay is not None
            info = probe(gameplay.path)
            box_w, box_h = GAMEPLAY_BOX[2:]
            start = float((edl.gameplay or {}).get("start_s") or 0.0)
            gsize = cover_size(info.width, info.height, box_w, box_h)
            game = decode_frames(gameplay, start, total, fps=fps, size=gsize, loop=True)
            open_.append(game)
        n = 0
        for group in runs(tl.segments):
            frames = run_frames(src, group, fps=fps, size=size)
            open_.append(frames)
            for frame in frames:
                i = max(0, bisect.bisect_right(starts, n) - 1)
                game_frame = next(game) if game is not None else None
                sprites = overlays(edl, n / fps)
                pending.append(pool.submit(_frame, frame, spans[i], n - starts[i], game_frame, sprites))
                n += 1
                if len(pending) > COMPOSERS:
                    hand_over()
        while pending:
            hand_over()
        enc.close()
    except BaseException:
        pool.shutdown(cancel_futures=True)
        enc.kill()
        for gen in open_:
            gen.close()  # type: ignore[attr-defined]
        out.unlink(missing_ok=True)
        raise
    pool.shutdown()
    for gen in open_:
        gen.close()  # type: ignore[attr-defined]
    if progress is not None:
        progress(1.0)


def mux(video: Path, audio: Path, out: Path) -> None:
    """The rendered picture and the finished sound in one MP4, both copied, the moov first, no edit list."""
    args = [ffmpeg_bin(), *QUIET, "-i", str(Path(video).absolute()), "-i", str(Path(audio).absolute())]
    args += ["-map", "0:v:0", "-map", "1:a:0", "-c", "copy", *MP4, str(Path(out).absolute())]
    run(args, timeout_s=300)


def peak_rate(packets: Sequence[tuple[float, int]], window_s: float = 1.0) -> float:
    """The most bits a second in any ``window_s`` long stretch starting at a packet: (time, bytes) pairs."""
    times = sorted(packets)
    best, j, total = 0, 0, 0
    for i, (t, _) in enumerate(times):
        while j < len(times) and times[j][0] < t + window_s - EPS:
            total += times[j][1]
            j += 1
        best = max(best, total)
        total -= times[i][1]
    return 8 * best / window_s


def peak_bitrate(path: Path) -> float:
    """The busiest second of ``path``'s video, in bits a second."""
    args = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries"]
    out = run([*args, "packet=dts_time,pts_time,size", "-of", "json", str(Path(path).absolute())]).stdout
    packets = []
    for p in json.loads(out or "{}").get("packets") or []:
        t = p.get("dts_time", p.get("pts_time"))
        with contextlib.suppress(TypeError, ValueError):
            packets.append((float(t), int(p["size"])))
    return peak_rate(packets)


def reencode_capped(path: Path) -> bool:
    """Encodes ``path`` again in place with libx264 under the bitrate cap when any second of its video is
    over PEAK_LIMIT, keeping its sound as it is. Says whether it had to."""
    path = Path(path).absolute()
    if peak_bitrate(path) <= PEAK_LIMIT:
        return False
    fps = output_fps(probe(path).fps or 30.0, False)
    tmp = path.with_name(f"{path.stem}.capped{path.suffix}")
    args = [ffmpeg_bin(), *QUIET, "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?"]
    args += [*encoder_args("best", fps, capped=True), "-c:a", "copy", "-fps_mode", "passthrough", str(tmp)]
    try:
        run(args, timeout_s=3600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return True
