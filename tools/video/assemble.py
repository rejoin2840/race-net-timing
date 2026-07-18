#!/usr/bin/env python3
import subprocess, os
SCR = os.path.dirname(__file__)
FR = os.path.join(SCR, "frames")
frames = ["00_title","01_overview","02_rows","03_net","04_detail","05_wywa","06_fcy","07_rc","08_battles","09_accuracy"]
d = [4.0, 9.0, 9.0, 9.5, 9.5, 9.0, 8.5, 9.5, 8.5, 12.0]
T = 0.6
FPS = 30
OUT = os.path.join(SCR, "out", "dashboard_demo.mp4")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

inputs = []
for f in frames:
    inputs += ["-loop","1","-t",str(d[frames.index(f)]),"-i",os.path.join(FR,f+".png")]

# per-input normalize
fc = []
for i in range(len(frames)):
    fc.append(f"[{i}:v]scale=1600:1040:force_original_aspect_ratio=decrease,"
              f"pad=1600:1040:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v{i}]")
# xfade chain
off = d[0]-T
fc.append(f"[v0][v1]xfade=transition=fade:duration={T}:offset={off:.3f}[x1]")
length = d[0]+d[1]-T
for k in range(2,len(frames)):
    off = length - T
    fc.append(f"[x{k-1}][v{k}]xfade=transition=fade:duration={T}:offset={off:.3f}[x{k}]")
    length = length + d[k] - T
last = f"x{len(frames)-1}"
# gentle fade in/out
fc.append(f"[{last}]fade=t=in:st=0:d=0.5,fade=t=out:st={length-0.6:.3f}:d=0.6[vout]")

filter_complex = ";".join(fc)
cmd = ["ffmpeg","-y",*inputs,"-filter_complex",filter_complex,
       "-map","[vout]","-c:v","libx264","-pix_fmt","yuv420p","-crf","20",
       "-movflags","+faststart",OUT,"-loglevel","error"]
print("total duration ~", round(length,1),"s")
subprocess.run(cmd, check=True)
print("wrote", OUT)
