from glob import glob
import os
import shutil
import subprocess as SP
import random
import time
import json
import io
import xml.etree.ElementTree as ET
from datetime import timedelta, datetime
from urllib.request import urlretrieve, urlcleanup

import sys

TIMEOUT = 300
DURATION = 20
MINSIZE = 1024


def getoutput(cmd):
    return SP.check_output(cmd, shell=False, encoding="utf-8")


ffmpeg_exec = "/bin/ffmpeg"
ffprobe_exec = "/bin/ffprobe"


def try_delete(filename, ignore=False):
    while True:
        try:
            if os.path.isdir(filename):
                shutil.rmtree(filename, ignore)
            elif os.path.isfile(filename):
                os.unlink(filename)
            return True
        except PermissionError:
            print("Can't remove", filename, "retrying...")
            time.sleep(1)


def get_attrs(node, *attrs):
    ret = {}
    for attr in attrs:
        ret[attr] = getattr(next(node.iter(attr), None), "text", None)
        if ret[attr] is None:
            del ret[attr]
    return ret


def make_bsf(noise=None, drop=None):
    if drop == 0:
        drop = None
    key = (noise is not None, drop is not None)
    return {
        (False, False): None,  # both None
        (True, False): f"noise=amount={noise}",  # only noise given
        (False, True): f"noise=dropamount={drop}",  # only drop given
        (True, True): f"noise=amount={noise}:dropamount={drop}",  # both given
    }[key]


def pipe(cmds, **kwargs):
    # print(kwargs)
    # print(" ")
    # print(' '.join(str(x) for x in cmds[0]))
    # print("")
    # print(' '.join(str(x) for x in cmds[1]))
    # print("")
    # print(' '.join(str(x) for x in cmds[2]))
    # print("")
    procs = []
    for n, cmd in enumerate(cmds):
        if cmd is None:
            continue
        cmd = list(map(str, cmd))
        sp_kwargs = kwargs.copy()
        if n < (len(cmds) - 1):
            sp_kwargs.update({"stdout": SP.PIPE})
        if procs:
            sp_kwargs.update({"stdin": procs[-1].stdout})
        procs.append(SP.Popen(cmd, **sp_kwargs))
    ret = []
    try:
        procs[-1].wait(TIMEOUT)
    except SP.TimeoutExpired:
        print("timeout expired 1")
        procs.pop(-1).terminate()
        ret.insert(0, None)
    for proc in procs[::-1]:
        try:
            ret.insert(0, proc.wait(TIMEOUT))
        except SP.TimeoutExpired:
            print("timeout expired 2")
            proc.terminate()
            ret.insert(0, None)
    print("file glitched via cmd pipe")
    return ret


def probe(vid_file_path):
    """ Give a json from ffprobe command line

    @vid_file_path : The absolute (full) path of the video file, string.
    """
    if type(vid_file_path) != str:
        return None

    command = [
        ffprobe_exec,
        "-loglevel",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        vid_file_path,
    ]

    pipe = SP.Popen(command, stdout=SP.PIPE, stderr=SP.STDOUT)
    out, err = pipe.communicate()
    if err:
        print(err)
    return json.loads(out)


def get_duration(vid_file_path):
    """ Video's duration in seconds, return a float number
    """
    _json = probe(vid_file_path)

    if "format" in _json:
        if "duration" in _json["format"]:
            return float(_json["format"]["duration"])

    if "streams" in _json:
        # commonly stream 0 is the video
        for s in _json["streams"]:
            if "duration" in s:
                return float(s["duration"])

    # if everything didn't happen,
    # we got here because no single 'return' in the above happen.
    return float("nan")


def ac_process(
    infile,
    start,
    duration,
    acodec,
    achannels,
    pix_fmt_in,
    pix_fmt_out,
    noise_amt,
    drop_amt=None,
    arate=44100,
    size=None,
):
    print(
        "[Audio] Glitching",
        infile,
        "with",
        acodec,
        "and",
        pix_fmt_in,
        "->",
        pix_fmt_out,
    )
    ffmpeg = [
        ffmpeg_exec,
        "-strict",
        "-2",
        "-hide_banner",
        "-loglevel",
        "panic",
        "-abort_on",
        "empty_output",
    ]
    ffprobe = [ffprobe_exec]
    outfile = os.path.join(
        "out",
        f"a_{acodec}_{pix_fmt_in}_{pix_fmt_out}_{achannels}ch_{noise_amt}_{drop_amt}.webm",
    )
    if os.path.isfile(outfile):
        try_delete(outfile)
    if size is None:
        size = getoutput(
            [
                *ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height,width",
                "-of",
                "csv=s=x:p=0",
                infile,
            ]
        )
    dx = 0
    dy = 0
    sx, sy = tuple(map(int, size.split("x")))
    if random.random() < 0.462:
        dx = int((1 / random.random()) - 2)
    if random.random() < 0.462:
        dy = int((1 / random.random()) - 2)
    sx += dx
    sy += dy
    bsf = make_bsf(noise_amt, drop_amt)
    if bsf is None:
        noise_filter = None
    else:
        noise_filter = [
            *ffmpeg,
            "-f",
            "matroska",
            "-i",
            "-",
            "-c:v",
            "copy",
            "-f",
            "matroska",
            "-bsf:a",
            bsf,
            "-strict",
            "-2",
            "-",
        ]
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    if bsf is None:
        noise_filter = None
    else:
        noise_filter = [
            *ffmpeg,
            "-f",
            "matroska",
            "-i",
            "-",
            "-c:v",
            "copy",
            "-f",
            "matroska",
            "-bsf:v",
            bsf,
            "-strict",
            "-2",
            "-",
        ]
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    commands = [
        [
            *ffmpeg,
            "-ss",
            start,
            "-i",
            infile,
            "-t",
            duration,
            "-f",
            "rawvideo",
            "-pix_fmt",
            pix_fmt_in,
            "-strict",
            "-2",
            "-",
        ],
        [
            *ffmpeg,
            "-f",
            "u8",
            "-ar",
            arate,
            "-ac",
            achannels,
            "-i",
            "-",
            "-f",
            "matroska",
            "-c:a",
            acodec,
            "-strict",
            "-2",
            "-",
        ],
        noise_filter,
        [
            *ffmpeg,
            "-f",
            "matroska",
            "-i",
            "-",
            "-f",
            "u8",
            "-ar",
            arate,
            "-ac",
            achannels,
            "-strict",
            "-2",
            "-",
        ],
        [
            *ffmpeg,
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            pix_fmt_out,
            "-s",
            f"{sx}x{sy}",
            "-i",
            "-",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=512:-1",
            "-strict",
            "-2",
            outfile,
        ],
    ]

    if noise_amt == 0:
        noise_amt = "random"
    elif noise_amt is not None:
        noise_amt = f"1/{noise_amt}"
    if drop_amt is not None and drop_amt != 0:
        drop_amt = f"1/{drop_amt}"
    pix_fmt = pix_fmt_in
    if pix_fmt_in != pix_fmt_out:
        pix_fmt = "{} -> {}".format(pix_fmt_in, pix_fmt_out)
    info = [
        ("mode", "audio"),
        ("codec", acodec),
        ("channels", achannels),
        ("pixel_format", pix_fmt),
        ("noise", noise_amt),
        ("drop", drop_amt),
        ("skew", (dx, dy)),
    ]
    return outfile, pipe(commands), dict(info)


def vc_process(infile, i, start, duration, vcodec, noise_amt, drop_amt=None):
    print("[Video] Glitching", infile, "with", vcodec)
    ffmpeg = [
        ffmpeg_exec,
        "-strict",
        "-2",
        "-hide_banner",
        "-loglevel",
        "panic",
        "-abort_on",
        "empty_output",
    ]
    filename = infile.split("/")[-1]
    print(filename)
    outfile = os.path.join(
        "out", f"v_{filename}_{i}_{vcodec}_{noise_amt}_{drop_amt}.webm")
    if os.path.isfile(outfile):
        print("outfile is file")
        try_delete(outfile)
    bsf = make_bsf(noise_amt, drop_amt)
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    if bsf is None:
        noise_filter = None
    else:
        noise_filter = [
            *ffmpeg,
            "-f",
            "matroska",
            "-i",
            "-",
            "-c:v",
            "copy",
            "-f",
            "matroska",
            "-bsf:v",
            bsf,
            "-strict",
            "-2",
            "-",
        ]

    commands = [
        [
            *ffmpeg,
            "-ss",
            start,
            "-i",
            infile,
            "-t",
            duration,
            "-f",
            "matroska",
            "-c:v",
            vcodec,
            "-strict",
            "-2",
            "-",
        ],
        noise_filter,
        [
            *ffmpeg,
            "-y",
            "-f",
            "matroska",
            "-i",
            "-",
            "-crf",
            "30",
            "-an",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=512:-1",
            "-strict",
            "-2",
            outfile,
        ],
    ]
    if noise_amt == 0:
        noise_amt = "random"
    elif noise_amt is not None:
        noise_amt = f"1/{noise_amt}"
    if drop_amt is not None and drop_amt != 0:
        drop_amt = f"1/{drop_amt}"
    info = [
        ("mode", "video"),
        ("codec", vcodec),
        ("noise", noise_amt),
        ("drop", drop_amt),
    ]
    return outfile, pipe(commands), dict(info)


acodecs = []
vcodecs = []

go = False
for line in getoutput([ffmpeg_exec, "-v", "0", "-codecs"]).splitlines():
    if set(line.strip()) == set("-"):
        go = True
        continue
    if not go:
        continue
    flags, name, desc = line.split(None, 2)
    if "pcm" in name:
        continue
    if flags[1] == "E" and flags[0] == "D":
        if flags[2] == "A":
            if desc.startswith("PCM"):
                continue
            name = (
                name.replace("vorbis", "libvorbis")
                .replace("opus", "libopus")
                .replace("speex", "libspeex")
            )
            acodecs.append(name)
        if flags[2] == "V":
            if desc.startswith("Uncompressed"):
                continue
            vcodecs.append(name)

pix_fmts = []

go = False
for line in getoutput([ffmpeg_exec, "-v", "0", "-pix_fmts"]).splitlines():
    if set(line.strip()) == set("-"):
        go = True
        continue
    if not go:
        continue
    flags, name, num_componenets, bpp = line.split(None)
    if flags[0] == "I" and flags[1] == "O":
        num_componenets = int(num_componenets)
        bpp = int(bpp)
        pix_fmts.append((name, num_componenets, bpp))

achannels = [1, 2, 3]
try:
    try_delete("out", ignore=True)
    os.makedirs("out", exist_ok=True)
except Exception:
    pass


def v_glitch(filename, i, submitter=None, start=0, duration=10):
    while True:
        noise_amt = None
        drop_amt = None
        while noise_amt is None and drop_amt is None:
            noise_amt = int((1 / random.random()) - 2)
            if noise_amt == -1:
                noise_amt = None
            drop_amt = None
            if random.random() > 0.5:
                drop_amt = int((1 / random.random()))
        print("noise_amt", noise_amt, "drop_amt", drop_amt)
        args = (filename, i, start, duration,
                random.choice(vcodecs), noise_amt, drop_amt)
        filename, status, info = vc_process(*args)
        if status is None:
            break
        if os.path.isfile(filename) and os.stat(filename).st_size < MINSIZE:
            try_delete(filename)
            return filename, "outfile too small, ffmpeg error: {}".format(status), True
        if set(status) == {0}:
            break
        if not os.path.isfile(filename):
            print("suppousedly no file")
            # return None, "outfile does not exist, ffmpeg error: {}".format(status)
        # try_delete(filename)
        return filename, "ffmpeg error: {}".format(status), True
    info = type("Info", (object,), info)
    submitter = submitter or "Random selection"
    start = str(timedelta(seconds=start))
    info_text = f"""
Source: {submitter}
Timestamp: {start}
Mode: Video
Codec: {info.codec}
Noise amount: {info.noise}
Packet loss: {info.drop}
    """.strip()
    return filename, info_text, False


def a_glitch(filename, submitter=None, start=0, duration=10):
    while True:
        noise_amt = int((1 / random.random()) - 2)
        if noise_amt == -1:
            noise_amt = None
        drop_amt = None
        if random.random() > 0.5:
            drop_amt = int((1 / random.random()) - 1)
        # (name, num_componenets, bpp))
        pix_fmt_in = random.choice(pix_fmts)
        pix_fmt_out = pix_fmt_in
        if random.random() > 0.5:
            pix_fmt_out = random.choice(pix_fmts)
            while pix_fmt_in[2] != pix_fmt_out[2] and pix_fmt_in[1] != pix_fmt_out[1]:
                pix_fmt_out = random.choice(pix_fmts)
        pix_fmt_in = pix_fmt_in[0]
        pix_fmt_out = pix_fmt_out[0]
        args = (
            filename,
            start,
            duration,
            random.choice(acodecs),
            random.choice(achannels),
            pix_fmt_in,
            pix_fmt_out,
            noise_amt,
            drop_amt,
        )
        filename, status, info = ac_process(*args)
        if status is None:
            break
        if os.path.isfile(filename) and os.stat(filename).st_size < 1024:
            try_delete(filename)
            return None, "outfile too small, ffmpeg error: {}".format(status)
        if set(status) == {0}:
            break
        if not os.path.isfile(filename):
            return None, "outfile does not exist, ffmpeg error: {}".format(status)
        try_delete(filename)
        return None, "ffmpeg error: {}".format(status)
    info = type("Info", (object,), info)
    submitter = submitter or "Random selection"
    start = str(timedelta(seconds=start))
    info_text = f"""
Source: {submitter}
Timestamp: {start}
Mode: Audio
Codec: {info.codec}
Audio channels: {info.channels}
Pixel format: {info.pixel_format}
Noise amount: {info.noise}
Packet loss: {info.drop}
Size Skew: {info.skew[0]}, {info.skew[1]}
""".strip()
    # print("file glitched and returned")
    return filename, info_text


def prepare_file(uplodad=False):
    if uplodad:
        for file in glob("out/**"):
            if os.path.isfile(file):
                try:
                    os.unlink(file)
                except Exception:
                    pass
    i = 0
    while i < 8:
        print("on loop", i + 1)
        submitter = "me"
        vid = sys.argv[1]
        duration = get_duration(vid)
        print(duration)
        start = 0
        if duration > DURATION:
            start = int(random.random() * (duration - DURATION))
            duration = DURATION
        filename, info_text, failed = v_glitch(
            vid, i, submitter, start, duration
        )
        urlcleanup()
        if failed:
            print("something went wrong")
            print("FAILED:", info_text)
        else:
            print("filename if'ed")
            print(info_text)
            print("new path:", filename)
            vid = filename
            i += 1
    return info_text, filename


prepare_file()

# while True:
#     make_post()
