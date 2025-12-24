# cogs/tournament_bracket_cog.py

import io
import os
import math
import random
import logging
from typing import List, Optional, Dict, Tuple

import sqlite3
import discord
from discord.ext import commands

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .tournament_db import get_tournament, get_db_connection

log = logging.getLogger(__name__)


# -------------------------------------------------
# TEAM COLLECTION HELPER
# -------------------------------------------------

def collect_team_names(guild: discord.Guild) -> List[str]:
    """
    Collect team names for the bracket.

    Priority:
    1) DB: all teams that are marked READY in the `teams` table.
       (This now includes both player teams and bot teams.)
    2) Fallback: team channels under the tournament category whose names start
       with 'team-'.
    """
    names: List[str] = []

    t = get_tournament(guild.id)
    if not t:
        log.warning("collect_team_names: no tournament row found for guild %s", guild.id)
        return names

    # --- Primary: use REAL teams table (READY teams only) ---
    try:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            """
            SELECT team_name
            FROM teams
            WHERE guild_id = ? AND is_ready = 1
            ORDER BY team_id ASC
            """,
            (guild.id,),
        )
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            names.append(row["team_name"])

        if names:
            log.info(
                "collect_team_names: DB returned %d READY teams for guild %s",
                len(names),
                guild.id,
            )
            return names
        else:
            log.info(
                "collect_team_names: DB query ran but found 0 READY teams for guild %s",
                guild.id,
            )

    except Exception as e:
        log.warning("collect_team_names: DB lookup failed for guild %s: %r", guild.id, e)

    # --- Fallback: look at team channels under the tournament category ---
    cat_id = t.get("category_id")
    category = guild.get_channel(cat_id) if cat_id else None

    if isinstance(category, discord.CategoryChannel):
        for ch in category.text_channels:
            if ch.name.startswith("team-"):
                cleaned = ch.name.replace("team-", "").replace("-", " ").title()
                names.append(cleaned)

    log.info(
        "collect_team_names: fallback found %d team names for guild %s",
        len(names),
        guild.id,
    )
    return names


# -------------------------------------------------
# SHARED SEEDING HELPER
# -------------------------------------------------

def get_seeded_teams(guild: discord.Guild) -> List[str]:
    """
    Return the READY team list in a deterministic shuffled order.
    Both the bracket image and the match-channel creator use this
    so that visual bracket = actual match pairings.
    """
    team_names = collect_team_names(guild)
    if not team_names:
        return []

    random.seed(1)  # deterministic
    seeds = team_names[:]
    random.shuffle(seeds)
    return seeds


# -------------------------------------------------
# BACKGROUND + LOGO
# -------------------------------------------------

def create_tog_background(width: int, height: int) -> Image.Image:
    base = Image.new("RGBA", (width, height), (5, 5, 8, 255))

    vignette = Image.new("L", (width, height))
    vpx = vignette.load()
    cx, cy = width / 2, height / 2
    max_d = math.hypot(cx, cy)

    for y in range(height):
        for x in range(width):
            d = math.hypot(x - cx, y - cy) / max_d
            alpha = int(255 * (d ** 1.8))
            vpx[x, y] = alpha

    bg_rgb = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    bg = Image.composite(bg_rgb, base, vignette.point(lambda v: 255 - v))

    return bg


def draw_bot_logo_layer(width: int, height: int) -> Image.Image:
    # (kept for compatibility; not used by draw_bracket_image now)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    cx, cy = width // 2, height // 2
    radius = int(min(width, height) * 0.23)

    logo_color = (255, 40, 80, 35)
    d.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=logo_color,
        width=8,
    )

    try:
        font_title = ImageFont.truetype("arial.ttf", 80)
        font_sub = ImageFont.truetype("arial.ttf", 40)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    title = "TOG-BOT"
    sub = "TOURNAMENT"

    tw, th = d.textbbox((0, 0), title, font=font_title)[2:]
    sw, sh = d.textbbox((0, 0), sub, font=font_sub)[2:]

    d.text(
        (cx - tw / 2, cy - th),
        title,
        font=font_title,
        fill=(255, 40, 80, 40),
    )
    d.text(
        (cx - sw / 2, cy + 10),
        sub,
        font=font_sub,
        fill=(255, 40, 80, 40),
    )

    layer = layer.filter(ImageFilter.GaussianBlur(2))
    return layer


# -------------------------------------------------
# BRACKET IMAGE GENERATOR
# -------------------------------------------------

def draw_bracket_image(
    team_names: List[str],
    eliminated_slots: Optional[List[Tuple[int, int]]] = None,
    advancing_by_round: Optional[List[List[Optional[str]]]] = None,
) -> bytes:
    """
    Draw a single-elim bracket image with T0G styling and return PNG bytes.

    - team_names: original seeded team list (Round 1 teams, in bracket order).
    - eliminated_slots: list of (round_idx, box_idx) tuples indicating which
      boxes should be X'ed out. round_idx is 0-based (0 = first column).
    - advancing_by_round: list-of-lists describing which team is in each box:

        advancing_by_round[0] = list of teams in Round 1 boxes (same length as team_names)
        advancing_by_round[1] = list of teams in Round 2 boxes (winners from Round 1)
        advancing_by_round[2] = list of teams in Round 3 boxes, etc.

      Entries may be None for not-yet-decided slots.
      If advancing_by_round is None, only Round 1 boxes are labeled
      using team_names, and later rounds are left blank.
    """
    n = len(team_names)
    if n not in (2, 4, 8, 16, 32):
        raise ValueError("Team count must be 2, 4, 8, 16, or 32.")

    seeds = team_names[:]  # already seeded order
    num_rounds = int(math.log2(n))

    img_width, img_height = 1800, 900

    # --- NEW BACKGROUND USING YOUR PNG LOGO ---
    # Start with the dark T0G background
    img = create_tog_background(img_width, img_height)

    # Try to overlay your custom logo from /assets/tog_bot_tournament_logo.png
    try:
        logo_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "assets",
            "tog_bot_tournament_logo.png",
        )
        logo_path = os.path.abspath(logo_path)

        bot_logo = Image.open(logo_path).convert("RGBA")
        bot_logo = bot_logo.resize((img_width, img_height), Image.LANCZOS)

        # Control opacity (0–255). Higher = stronger logo.
        alpha = 110
        bot_logo.putalpha(alpha)

        img = Image.alpha_composite(img, bot_logo)

    except Exception as e:
        log.warning(f"Could not load T0G logo background: {e}")
    # --- END OF NEW BACKGROUND CODE ---

    draw = ImageDraw.Draw(img)

    try:
        font_team = ImageFont.truetype("arial.ttf", 22)
        font_label = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font_team = ImageFont.load_default()
        font_label = ImageFont.load_default()

    margin_x = 130
    margin_y = 80
    box_height = 46
    total_span = img_height - 2 * margin_y

    usable_width = img_width - 2 * margin_x
    col_step = usable_width / (num_rounds + 1)
    box_width = col_step * 0.75

    pink = (255, 0, 120, 255)
    gold = (255, 204, 120, 255)
    white = (240, 240, 240, 255)

    boxes_per_round: List[List[tuple]] = []

    # Helper to get the label for a given round/slot
    def get_label_for(round_idx: int, slot_idx: int) -> Optional[str]:
        if advancing_by_round and len(advancing_by_round) > round_idx:
            round_list = advancing_by_round[round_idx]
            if slot_idx < len(round_list):
                return round_list[slot_idx]
        # Fallback for round 0 (first column) if no advancing_by_round given
        if round_idx == 0 and slot_idx < len(seeds):
            return seeds[slot_idx]
        return None

    # ---------- Round 1 ----------
    gap0 = total_span / n
    col_x = margin_x
    r0_boxes = []
    for i in range(n):
        name = get_label_for(0, i) or seeds[i]
        cy = margin_y + gap0 * (i + 0.5)
        top = int(cy - box_height / 2)
        bottom = int(cy + box_height / 2)
        left = int(col_x)
        right = int(col_x + box_width)
        r = 18

        draw.rounded_rectangle([left, top, right, bottom], r, outline=pink, width=3)
        if name:
            draw.text((left + 10, top + 12), name, font=font_team, fill=white)
        r0_boxes.append((left, top, right, bottom))

    boxes_per_round.append(r0_boxes)

    # ---------- Later rounds ----------
    for r_idx in range(1, num_rounds):
        prev_count = len(boxes_per_round[r_idx - 1])
        count = prev_count // 2
        col_x = margin_x + r_idx * col_step
        gap = total_span / count

        round_boxes = []
        for i in range(count):
            cy = margin_y + gap * (i + 0.5)
            top = int(cy - box_height / 2)
            bottom = int(cy + box_height / 2)
            left = int(col_x)
            right = int(col_x + box_width)
            rad = 18

            draw.rounded_rectangle([left, top, right, bottom], rad, outline=pink, width=3)

            label = get_label_for(r_idx, i)
            if label:
                draw.text((left + 10, top + 12), label, font=font_team, fill=white)

            round_boxes.append((left, top, right, bottom))

        boxes_per_round.append(round_boxes)

    # Figure out champion name if we know it (last column has exactly 1 non-None team)
    champion_name: Optional[str] = None
    if advancing_by_round and advancing_by_round[-1]:
        last_col = advancing_by_round[-1]
        if len(last_col) == 1 and last_col[0]:
            champion_name = last_col[0]

    # ---------- Champion box ----------
    last_round = boxes_per_round[-1]
    if last_round:
        top_cy = (last_round[0][1] + last_round[0][3]) / 2
        bottom_cy = top_cy if len(last_round) == 1 else (last_round[-1][1] + last_round[-1][3]) / 2
        champion_y = int((top_cy + bottom_cy) / 2)
    else:
        champion_y = img_height // 2

    champ_x = margin_x + num_rounds * col_step
    champ_top = int(champion_y - box_height / 2)
    champ_bottom = int(champion_y + box_height / 2)
    champ_left = int(champ_x)
    champ_right = int(champ_x + box_width * 1.2)

    draw.rounded_rectangle(
        [champ_left, champ_top, champ_right, champ_bottom],
        20,
        outline=gold,
        width=3,
    )

    if champion_name:
        champ_label = f"Champion: {champion_name}"
    else:
        champ_label = "Champion"

    draw.text((champ_left + 22, champ_top + 12), champ_label, font=font_label, fill=white)

    champ_cy = (champ_top + champ_bottom) / 2
    draw.line([(champ_right, champ_cy), (champ_right + 60, champ_cy)], fill=gold, width=3)

    # ---------- Connecting lines between rounds ----------
    for r_idx in range(num_rounds - 1):
        children = boxes_per_round[r_idx]
        parents = boxes_per_round[r_idx + 1]
        for j, p in enumerate(parents):
            pl, pt, pr, pb = p
            pcy = (pt + pb) / 2
            c1 = children[2 * j]
            c2 = children[2 * j + 1]
            c1cy = (c1[1] + c1[3]) / 2
            c2cy = (c2[1] + c2[3]) / 2
            mid_x = (c1[2] + pl) / 2

            for cx, cy in [(c1[2], c1cy), (c2[2], c2cy)]:
                draw.line([(cx, cy), (mid_x, cy)], fill=pink, width=3)

            draw.line([(mid_x, c1cy), (mid_x, c2cy)], fill=pink, width=3)
            draw.line([(mid_x, pcy), (pl, pcy)], fill=pink, width=3)

    # ---------- Connect last round to Champion ----------
    if last_round:
        if len(last_round) == 1:
            l = last_round[0]
            lcy = (l[1] + l[3]) / 2
            mid_x = (l[2] + champ_left) / 2

            draw.line([(l[2], lcy), (mid_x, lcy)], fill=pink, width=3)
            draw.line([(mid_x, champion_y), (champ_left, champion_y)], fill=pink, width=3)
        else:
            t_box = last_round[0]
            b_box = last_round[1]
            tcy = (t_box[1] + t_box[3]) / 2
            bcy = (b_box[1] + b_box[3]) / 2
            mid_x = (t_box[2] + champ_left) / 2

            for bx, by in [(t_box[2], tcy), (b_box[2], bcy)]:
                draw.line([(bx, by), (mid_x, by)], fill=pink, width=3)

            draw.line([(mid_x, tcy), (mid_x, bcy)], fill=pink, width=3)
            draw.line([(mid_x, champion_y), (champ_left, champion_y)], fill=pink, width=3)

    # ---------- X out eliminated teams in specific boxes ----------
    elim_slots = set(eliminated_slots or [])
    if elim_slots:
        for round_idx, round_boxes in enumerate(boxes_per_round):
            for i, (left, top, right, bottom) in enumerate(round_boxes):
                if (round_idx, i) in elim_slots:
                    pad = 4
                    draw.line(
                        [(left + pad, top + pad), (right - pad, bottom - pad)],
                        fill=pink,
                        width=3,
                    )
                    draw.line(
                        [(left + pad, bottom - pad), (right - pad, top + pad)],
                        fill=pink,
                        width=3,
                    )

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


# -------------------------------------------------
# SETUP (required by auto-loader)
# -------------------------------------------------

async def setup(bot: commands.Bot):
    log.info("tournament_bracket_cog module loaded (bracket helpers + image).")
