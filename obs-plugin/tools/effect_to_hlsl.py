#!/usr/bin/env python3
"""Turns the OBS .effect into plain HLSL so CI can compile it with fxc, the same compiler
OBS uses on Windows (Direct3D 11). Catches shader errors that would only show up there.

    effect_to_hlsl.py data/ascii-cam.effect out.hlsl
"""
import re, sys

src = open(sys.argv[1], encoding="utf-8").read()
src = src[:src.index("technique ")]                       # drop the technique block
src = re.sub(r"sampler_state\s+(\w+)\s*\{[^}]*\};", r"SamplerState \1;", src)
src = re.sub(r"\btexture2d\b", "Texture2D", src)
src = src.replace(": TARGET", ": SV_Target").replace(": POSITION", ": SV_Position")
open(sys.argv[2], "w", encoding="utf-8").write(src)
