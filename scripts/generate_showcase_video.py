import os
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SOURCE_PATH = 'landing/assets/astra-app-real.png'
OUT_MP4 = 'landing/assets/astra-showcase.mp4'
OUT_WEBM = 'landing/assets/astra-showcase.webm'
TEMP_DIR = '/tmp/astra_video_frames'

os.makedirs(TEMP_DIR, exist_ok=True)

# Clean temp dir
for f in os.listdir(TEMP_DIR):
    if f.endswith('.png'):
        os.remove(os.path.join(TEMP_DIR, f))

# Video specs
TARGET_W = 1600
TARGET_H = 900
FPS = 30
TOTAL_SECONDS = 12
TOTAL_FRAMES = FPS * TOTAL_SECONDS

# Load base screenshot and convert to RGB
raw_img = Image.open(SOURCE_PATH).convert('RGB')
orig_w, orig_h = raw_img.size

# Key points of interest in raw image coordinates:
# 1. Full Overview: [0, 0, orig_w, orig_h]
# 2. Left Sidebar & Session selector:
#    e.g. box around sidebar and top left
# 3. Center Workspace: Session chat, instructions, tasks
# 4. Bottom Composer: Model selector (Claude, GPT), input field
# 5. Right Inspector: Access control, hard floor gates, session state

# Camera waypoints (t_sec, center_x_pct, center_y_pct, zoom_factor, caption_title, caption_desc)
# zoom_factor = 1.0 means fit full image into 16:9 frame with slight crop or padding
WAYPOINTS = [
    # 0.0s - 2.5s: Overview with subtle zoom in
    (0.0, 0.50, 0.50, 1.00, "ASTRA WORKSTATION", "Native Desktop AI Coworker · Governed & Local-first"),
    (2.2, 0.50, 0.50, 1.04, "ASTRA WORKSTATION", "Native Desktop AI Coworker · Governed & Local-first"),

    # 3.0s - 5.5s: Focus Left Sidebar & Workspace Sessions
    (3.2, 0.22, 0.35, 1.65, "MULTI-AGENT WORKSTATION", "Independent workspaces, session trees & task history"),
    (5.2, 0.25, 0.40, 1.70, "MULTI-AGENT WORKSTATION", "Independent workspaces, session trees & task history"),

    # 6.0s - 8.5s: Focus Center Agent Workflow & Reasoning Execution
    (6.2, 0.50, 0.45, 1.55, "SMART AGENT EXECUTION", "Transparent reasoning, artifact generation & task breakdown"),
    (8.2, 0.50, 0.52, 1.60, "SMART AGENT EXECUTION", "Transparent reasoning, artifact generation & task breakdown"),

    # 9.0s - 10.8s: Focus Smart Composer & Model Engine
    (9.2, 0.50, 0.85, 1.75, "BYOK & LOCAL LLM SWITCHER", "Instant model switching · Anthropic, OpenAI, Grok & Ollama"),
    (10.5, 0.50, 0.85, 1.75, "BYOK & LOCAL LLM SWITCHER", "Instant model switching · Anthropic, OpenAI, Grok & Ollama"),

    # 11.2s - 12.0s: Ease back out to complete view
    (11.4, 0.50, 0.50, 1.06, "100% PRIVATE & GOVERNED", "Your machine. Your secrets. Uncompromised privacy."),
    (12.0, 0.50, 0.50, 1.00, "100% PRIVATE & GOVERNED", "Your machine. Your secrets. Uncompromised privacy.")
]

def get_state(t):
    # Find surrounding waypoints
    if t <= WAYPOINTS[0][0]:
        return WAYPOINTS[0][1:] + (1.0,)
    if t >= WAYPOINTS[-1][0]:
        return WAYPOINTS[-1][1:] + (1.0,)

    for i in range(len(WAYPOINTS) - 1):
        t0, cx0, cy0, z0, cap0, desc0 = WAYPOINTS[i]
        t1, cx1, cy1, z1, cap1, desc1 = WAYPOINTS[i+1]
        if t0 <= t <= t1:
            ratio = (t - t0) / (t1 - t0)
            # Smooth cubic ease in-out
            s_ratio = ratio * ratio * (3 - 2 * ratio)
            cx = cx0 + (cx1 - cx0) * s_ratio
            cy = cy0 + (cy1 - cy0) * s_ratio
            z = z0 + (z1 - z0) * s_ratio
            caption = cap1 if ratio > 0.3 else cap0
            desc = desc1 if ratio > 0.3 else desc0
            # Alpha for caption transition
            cap_alpha = 1.0
            if ratio < 0.15:
                cap_alpha = ratio / 0.15
            elif ratio > 0.85:
                cap_alpha = (1.0 - ratio) / 0.15
            return cx, cy, z, caption, desc, cap_alpha
    return WAYPOINTS[-1][1:] + (1.0,)

print(f"Generating {TOTAL_FRAMES} frames...")

# Try to load fonts, fallback to default if not found
try:
    font_badge = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    font_desc = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    font_time = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
except:
    font_badge = ImageFont.load_default()
    font_title = font_badge
    font_desc = font_badge
    font_time = font_badge

for frame_idx in range(TOTAL_FRAMES):
    t = frame_idx / FPS
    res = get_state(t)
    cx_pct, cy_pct, zoom, caption, desc, cap_alpha = res

    # Calculate crop box in raw_img
    # Target aspect ratio is 16:9
    target_aspect = TARGET_W / TARGET_H

    # Visible box dimensions
    box_h = orig_h / zoom
    box_w = box_h * target_aspect

    # If box_w > orig_w, clamp box_w and reduce box_h
    if box_w > orig_w:
        box_w = orig_w
        box_h = box_w / target_aspect

    center_x = cx_pct * orig_w
    center_y = cy_pct * orig_h

    x0 = int(center_x - box_w / 2)
    y0 = int(center_y - box_h / 2)
    x1 = int(x0 + box_w)
    y1 = int(y0 + box_h)

    # Boundary clamping
    if x0 < 0:
        x1 += -x0
        x0 = 0
    if y0 < 0:
        y1 += -y0
        y0 = 0
    if x1 > orig_w:
        x0 -= (x1 - orig_w)
        x1 = orig_w
    if y1 > orig_h:
        y0 -= (y1 - orig_h)
        y1 = orig_h

    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(orig_w, x1)
    y1 = min(orig_h, y1)

    cropped = raw_img.crop((x0, y0, x1, y1))
    frame_img = cropped.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)

    # Overlay an elegant HUD badge at bottom-left
    overlay = Image.new('RGBA', (TARGET_W, TARGET_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Bottom HUD Card
    card_w = 640
    card_h = 76
    card_x = 40
    card_y = TARGET_H - card_h - 40

    # Card background with translucent dark glass
    alpha_card = int(220 * max(0.2, cap_alpha))
    draw.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=14,
        fill=(12, 16, 24, alpha_card),
        outline=(0, 216, 255, int(160 * cap_alpha)),
        width=1
    )

    # Accent indicator dot
    dot_color = (0, 220, 255, int(255 * cap_alpha))
    draw.ellipse([card_x + 20, card_y + 24, card_x + 30, card_y + 34], fill=dot_color)

    # Title & Subtitle text
    text_color = (255, 255, 255, int(245 * cap_alpha))
    sub_color = (180, 195, 215, int(220 * cap_alpha))

    draw.text((card_x + 42, card_y + 16), caption, fill=text_color, font=font_title)
    draw.text((card_x + 42, card_y + 44), desc, fill=sub_color, font=font_desc)

    # Top-right live time indicator
    time_str = f"ASTRA OS LIVE · 00:{frame_idx//FPS:02d}.{int((frame_idx%FPS)/FPS*100):02d}s"
    time_w = 260
    draw.rounded_rectangle(
        [TARGET_W - time_w - 40, 30, TARGET_W - 40, 64],
        radius=8,
        fill=(8, 12, 20, 190),
        outline=(50, 70, 95, 120),
        width=1
    )
    draw.ellipse([TARGET_W - time_w - 24, 43, TARGET_W - time_w - 16, 51], fill=(16, 220, 130, 230))
    draw.text((TARGET_W - time_w - 6, 39), time_str, fill=(200, 215, 235, 230), font=font_time)

    # Merge overlay
    final_frame = Image.alpha_composite(frame_img.convert('RGBA'), overlay).convert('RGB')
    frame_path = os.path.join(TEMP_DIR, f"frame_{frame_idx:04d}.png")
    final_frame.save(frame_path, 'PNG')

    if frame_idx % 60 == 0:
        print(f"Rendered frame {frame_idx}/{TOTAL_FRAMES} ({(frame_idx/TOTAL_FRAMES)*100:.1f}%)")

print("All frames rendered. Encoding with FFmpeg...")

# Encode to MP4 (h264 + yuv420p for universal browser support)
cmd_mp4 = [
    'ffmpeg', '-y',
    '-framerate', str(FPS),
    '-i', os.path.join(TEMP_DIR, 'frame_%04d.png'),
    '-c:v', 'libx264',
    '-pix_fmt', 'yuv420p',
    '-preset', 'fast',
    '-crf', '20',
    '-movflags', '+faststart',
    OUT_MP4
]
subprocess.run(cmd_mp4, check=True)
print(f"Generated MP4: {OUT_MP4} ({os.path.getsize(OUT_MP4)/1024/1024:.2f} MB)")

# Encode to WebM (VP9 for modern web standards)
cmd_webm = [
    'ffmpeg', '-y',
    '-framerate', str(FPS),
    '-i', os.path.join(TEMP_DIR, 'frame_%04d.png'),
    '-c:v', 'libvpx-vp9',
    '-b:v', '1800k',
    '-crf', '28',
    '-pix_fmt', 'yuv420p',
    OUT_WEBM
]
subprocess.run(cmd_webm, check=True)
print(f"Generated WebM: {OUT_WEBM} ({os.path.getsize(OUT_WEBM)/1024/1024:.2f} MB)")

# Clean up temp frames to free disk space
for f in os.listdir(TEMP_DIR):
    if f.endswith('.png'):
        os.remove(os.path.join(TEMP_DIR, f))
os.rmdir(TEMP_DIR)
print("Done!")
