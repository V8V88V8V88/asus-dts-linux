#!/usr/bin/env python3
"""Extract the DTS speaker tuning for an ASUS laptop from ASUS's Windows
driver and install it as a PipeWire filter-chain."""

import argparse
import glob
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

DTS_DRIVER_URL = ("https://dlcdnets.asus.com/pub/ASUS/nb/Image/Driver/Audio/34394/"
                  "DTSAPOdriver_DCH_ALL_Xperi_D_V1.11.27.0_34394_1.exe")

HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME, ".local/share/asus-dts-linux")
PW_CONF = os.path.join(HOME, ".config/pipewire/pipewire.conf.d/60-asus-dts-speakers.conf")
WP_CONF = os.path.join(HOME, ".config/wireplumber/wireplumber.conf.d/60-asus-dts-priorities.conf")
RATES = {"44k": 44100, "48k": 48000}
IR_LEN = 8192


def detect_ssid():
    for path in glob.glob("/proc/asound/card*/codec#*"):
        with open(path, errors="replace") as f:
            text = f.read()
        if "Realtek" not in text:
            continue
        m = re.search(r"Subsystem Id: 0x([0-9a-fA-F]{8})", text)
        if m and m.group(1).lower().startswith("1043"):
            return m.group(1).upper()
    return None


def detect_speaker_sink():
    out = subprocess.run(["pactl", "list", "short", "sinks"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        name = line.split("\t")[1] if "\t" in line else ""
        if name.startswith("alsa_output.") and "Speaker" in name:
            return name
    return None


def extract_driver(exe, workdir):
    def unpack(src, dst):
        subprocess.run(["7z", "x", "-y", "-o" + dst, src],
                       check=True, stdout=subprocess.DEVNULL)

    outer = os.path.join(workdir, "exe")
    unpack(exe, outer)
    inner = os.path.join(workdir, "pkg")
    for zip_res in glob.glob(os.path.join(outer, ".rsrc", "ZIP", "*")):
        unpack(zip_res, inner)
    return inner


def find_model_dir(pkg, ssid):
    hits = glob.glob(os.path.join(pkg, "**", "ASUS" + ssid), recursive=True)
    return hits[0] if hits else None


def music_speaker_blobs(model_dir, ssid):
    """Return {rate_tag: blob_path} for the Music / Internal Stereo mode."""
    xml_path = os.path.join(model_dir, f"dts_apo4_oem_config_{ssid}.xml")
    root = ET.parse(xml_path).getroot()
    for mode in root.iter("operating_mode"):
        ctl = {c.get("name"): (c.text or "").strip() for c in mode.iter("control")}
        if ctl.get("APO4-Operating Mode") == "APO4-Music" and \
           ctl.get("APO4-Speaker Type") == "APO4-Internal Stereo":
            blobs = {}
            for tag in RATES:
                name = ctl.get(f"MFX:Eagle-GPEQ Blob {tag}")
                if name:
                    blobs[tag] = os.path.join(model_dir, name)
            return blobs
    return {}


def read_gpeq(path):
    """DTS GPEQ blob: 2 header words, then sections of
    [2, b0, b1, b2, a1, a2] as Q30 fixed point with a1/a2 negated."""
    with open(path, "rb") as f:
        raw = f.read()
    words = struct.unpack("<%di" % (len(raw) // 4), raw)
    biquads, o = [], 2
    while o + 6 <= len(words) and words[o] == 2:
        b0, b1, b2, a1, a2 = (w / 2**30 for w in words[o + 1:o + 6])
        biquads.append((b0, b1, b2, -a1, -a2))
        o += 6
    return biquads


def impulse_response(biquads, n=IR_LEN):
    x = [0.0] * n
    x[0] = 1.0
    for b0, b1, b2, a1, a2 in biquads:
        y = [0.0] * n
        x1 = x2 = y1 = y2 = 0.0
        for i in range(n):
            v = b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, x[i]
            y2, y1 = y1, v
            y[i] = v
        x = y
    return x


def write_wav(path, samples, rate):
    data = b"".join(struct.pack("<f", s) for s in samples)
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
              struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32) +
              b"data" + struct.pack("<I", len(data)))
    with open(path, "wb") as f:
        f.write(header + data)


def db(x):
    return 10 ** (x / 20)


def pipewire_conf(sink, irs):
    files = " ".join(f'"{p}"' for p in irs)
    return f'''context.modules = [
{{   name = libpipewire-module-filter-chain
    flags = [ nofail ]
    args = {{
        node.description = "Laptop Speakers (DTS)"
        media.name       = "Laptop Speakers (DTS)"
        filter.graph = {{
            nodes = [
                {{
                    type = lv2
                    plugin = "http://calf.sourceforge.net/plugins/BassEnhancer"
                    name = bass
                    control = {{ "amount" = {db(5):.3f} "drive" = 8.5 "blend" = 0.0 "freq" = 160.0 "floor_active" = 1 "floor" = 60.0 }}
                }}
                {{ type = builtin label = convolver name = eqL config = {{ filename = [ {files} ] channel = 0 gain = 1.0 }} }}
                {{ type = builtin label = convolver name = eqR config = {{ filename = [ {files} ] channel = 0 gain = 1.0 }} }}
                {{
                    type = lv2
                    plugin = "http://lsp-plug.in/plugins/lv2/mb_compressor_stereo"
                    name = mbc
                    control = {{
                        "mode" = 1 "envb" = 0
                        "ce_0" = 1 "al_0" = {db(-24):.4f} "at_0" = 15.0 "rt_0" = 400.0 "cr_0" = 1.6 "kn_0" = {db(-6):.3f} "mk_0" = {db(9):.3f}
                        "cbe_1" = 1 "sf_1" = 500.0
                        "ce_1" = 1 "al_1" = {db(-20):.4f} "at_1" = 15.0 "rt_1" = 250.0 "cr_1" = 2.2 "kn_1" = {db(-6):.3f} "mk_1" = {db(9):.3f}
                        "cbe_2" = 1 "sf_2" = 8000.0
                        "ce_2" = 1 "al_2" = {db(-20):.4f} "at_2" = 15.0 "rt_2" = 200.0 "cr_2" = 2.2 "kn_2" = {db(-6):.3f} "mk_2" = {db(10):.3f}
                        "cbe_3" = 0 "cbe_4" = 0 "cbe_5" = 0 "cbe_6" = 0 "cbe_7" = 0
                    }}
                }}
                {{ type = builtin label = bq_highpass name = hp1L control = {{ "Freq" = 100.0 "Q" = 0.5412 }} }}
                {{ type = builtin label = bq_highpass name = hp2L control = {{ "Freq" = 100.0 "Q" = 1.3066 }} }}
                {{ type = builtin label = bq_highpass name = hp1R control = {{ "Freq" = 100.0 "Q" = 0.5412 }} }}
                {{ type = builtin label = bq_highpass name = hp2R control = {{ "Freq" = 100.0 "Q" = 1.3066 }} }}
                {{
                    type = lv2
                    plugin = "http://lsp-plug.in/plugins/lv2/limiter_stereo"
                    name = lim
                    control = {{ "mode" = 0 "th" = {db(-1):.3f} "lk" = 5.0 "at" = 5.0 "rt" = 20.0 "boost" = 1 "alr" = 0 }}
                }}
            ]
            links = [
                {{ output = "bass:out_l" input = "eqL:In" }}
                {{ output = "bass:out_r" input = "eqR:In" }}
                {{ output = "eqL:Out" input = "mbc:in_l" }}
                {{ output = "eqR:Out" input = "mbc:in_r" }}
                {{ output = "mbc:out_l" input = "hp1L:In" }}
                {{ output = "mbc:out_r" input = "hp1R:In" }}
                {{ output = "hp1L:Out" input = "hp2L:In" }}
                {{ output = "hp1R:Out" input = "hp2R:In" }}
                {{ output = "hp2L:Out" input = "lim:in_l" }}
                {{ output = "hp2R:Out" input = "lim:in_r" }}
            ]
            inputs  = [ "bass:in_l" "bass:in_r" ]
            outputs = [ "lim:out_l" "lim:out_r" ]
        }}
        capture.props = {{
            node.name           = "asus_dts_speakers"
            media.class         = Audio/Sink
            audio.channels      = 2
            audio.position      = [ FL FR ]
            filter.smart        = true
            filter.smart.name   = "asus-dts"
            filter.smart.target = {{ node.name = "{sink}" }}
        }}
        playback.props = {{
            node.name         = "asus_dts_speakers.output"
            node.passive      = true
            audio.channels    = 2
            audio.position    = [ FL FR ]
            stream.dont-remix = true
        }}
    }}
}}
]
'''


WIREPLUMBER_CONF = '''monitor.alsa.rules = [
  {
    matches = [ { node.name = "~alsa_output.*Headphones.*" } ]
    actions = { update-props = { priority.session = 2000 priority.driver = 2000 } }
  }
]
monitor.bluez.rules = [
  {
    matches = [ { node.name = "~bluez_output.*" } ]
    actions = { update-props = { priority.session = 2100 priority.driver = 2100 } }
  }
]
'''


def restart_audio():
    subprocess.run(["systemctl", "--user", "restart",
                    "pipewire", "pipewire-pulse", "wireplumber"], check=False)


def install(args):
    for tool in ("7z", "pactl"):
        if not shutil.which(tool):
            sys.exit(f"missing required tool: {tool}")

    ssid = args.ssid or detect_ssid()
    if not ssid:
        sys.exit("could not detect an ASUS Realtek codec (use --ssid)")
    sink = args.sink or detect_speaker_sink()
    if not sink:
        sys.exit("could not find the internal speaker sink (use --sink)")
    print(f"model SSID: {ssid}\nspeaker sink: {sink}")

    with tempfile.TemporaryDirectory() as work:
        exe = args.driver
        if not exe:
            exe = os.path.join(work, "dts.exe")
            print("downloading DTS driver from ASUS ...")
            urllib.request.urlretrieve(DTS_DRIVER_URL, exe)
        pkg = extract_driver(exe, work)
        model_dir = find_model_dir(pkg, ssid)
        if not model_dir:
            sys.exit(f"no DTS tuning for SSID {ssid} in this driver package")
        blobs = music_speaker_blobs(model_dir, ssid)
        if not blobs:
            sys.exit("no Music/Internal Stereo EQ found in the tuning file")

        os.makedirs(DATA_DIR, exist_ok=True)
        irs = []
        for tag, path in sorted(blobs.items()):
            biquads = read_gpeq(path)
            out = os.path.join(DATA_DIR, f"dts-eq-{RATES[tag]}.wav")
            write_wav(out, impulse_response(biquads), RATES[tag])
            irs.append(out)
            print(f"{tag}: {len(biquads)} biquads -> {out}")

    os.makedirs(os.path.dirname(PW_CONF), exist_ok=True)
    os.makedirs(os.path.dirname(WP_CONF), exist_ok=True)
    with open(PW_CONF, "w") as f:
        f.write(pipewire_conf(sink, irs))
    with open(WP_CONF, "w") as f:
        f.write(WIREPLUMBER_CONF)
    restart_audio()
    print("installed. Speaker audio now goes through the DTS chain.")


def uninstall(_args):
    for path in (PW_CONF, WP_CONF):
        if os.path.exists(path):
            os.remove(path)
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    restart_audio()
    print("removed.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--ssid", help="codec subsystem id, e.g. 1043175F")
    i.add_argument("--sink", help="PipeWire node name of the internal speakers")
    i.add_argument("--driver", help="use an already downloaded DTS driver .exe")
    i.set_defaults(func=install)
    u = sub.add_parser("uninstall")
    u.set_defaults(func=uninstall)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
