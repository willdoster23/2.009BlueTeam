r"""
4-winch wall robot (skycam-style), corner attachment: cable force, power and energy simulator.

Geometry (flat wall, units: ft and lbf unless noted)
------------------------------------------------------
    TL (0,H) o-------------------------o TR (W,H)
             |  \                   /  |
             |    \  +---------+  /    |        y up, x right,
             |      \| robot   |/      |        z out of the wall
             |      /|         |\      |
             |    /  +---------+  \    |
             |  /                   \  |
    BL (0,0) o-------------------------o BR (W,0)

Each cable runs from an anchor (at or beyond a wall corner) to the matching
corner of the robot chassis. The robot is held level (theta = 0). Fairleads
sit `standoff` out from the wall, so cables pull slightly into it.

Solver: "fix the slackest cable at pretension, solve for the other three"
---------------------------------------------------------------------------
Balance has 3 equations (x force, y force, in-plane moment) and 4 tensions:

    A T = w,   A is 3x4,   w = [0, load, cable-weight moment]

Every solution is   T = T0 + lam * n
    T0   any balancing solution (least-norm here)
    n    the null vector of A: tension pattern that pulls the cables against
         each other without moving the robot. Closed form (cofactors):
             n_i = (-1)^i det(A with column i removed)
    lam  the one free knob (overall tightness)

If all n_i > 0, the smallest lam that keeps every cable at its floor is

    lam = max_i (T_floor_i - T0_i) / n_i

which puts exactly one cable (the slackest) at its floor -- identical to
"fix that cable, solve the 3x3 for the others", without guessing which one.
Floor = max(pretension, sag limit for that cable).

A position is IGNORED (not usable) when:
    edge margin     the chassis can't get that close to the wall edge
    can't tension   some n_i <= 0: tightening one cable loosens another
    over limit      even the minimum-tension solution exceeds --tmax

Power (motor-independent; torque comes later from power + gearing)
------------------------------------------------------------------
With the robot moving at velocity v, cable i reels in at speed u_i . v, so the
mechanical power that winch must deliver is  P_i = T_i * (u_i . v).
Worst case, any direction: P_i = T_i * V (moving straight at its anchor).
"Continuous" = RMS over the usable wall (equal time everywhere); "peak" = max.
Motion is slow, so forces are quasi-static (acceleration ignored).

Energy for the whole wall
-------------------------
The robot covers the usable wall in vertical up/down passes spaced --swath apart
at max speed (a lawnmower pattern). At every step, battery power is

    sum over winches reeling in of  P_i / drive_eff
  + sum over ALL 4 winches of     (tau_i / Km)^2      (holding power)
  + hotel power

Holding power is the motor winding (I^2 R) loss just to hold torque
tau_i = T_i x spool radius; it is paid whether or not the robot moves, and
dominates at slow speed. Km is the actuator's motor constant at its output
(motor Km x gear ratio). Winches paying out return nothing (no
regeneration credit, conservative). A contingency factor covers acceleration,
friction, pauses and repositioning. Battery: 10 lb off-the-shelf Li-ion
(e-bike class, ~48 V 17 Ah, ~800 Wh, use 80%).

Usage
-----
    python wall_winch_sim.py                       # defaults, saves 2 PNGs, shows plots
    python wall_winch_sim.py --no-show             # just save the PNGs
    python wall_winch_sim.py --point 30 50         # one location
    python wall_winch_sim.py --speed 0.5 --swath 0.5
    python wall_winch_sim.py --anchor-top 10 --anchor-side 5 --chassis 1.5 0.5
"""

from __future__ import annotations

import argparse
import math
import os
import warnings
from dataclasses import dataclass

import numpy as np

warnings.filterwarnings("ignore", message="All-NaN slice encountered")

CABLES = ("TL", "TR", "BL", "BR")
CORNER_SIGN = np.array([[-1, 1], [1, 1], [-1, -1], [1, -1]], dtype=float)
FTLBF_S_TO_W = 1.35582
LBF_TO_N = 4.44822
IN_TO_M = 0.0254

CABLE_PRESETS = {           # linear weight of 1/8" cables, lb/ft
    "dyneema": 0.004,       # 3 mm 12-strand UHMWPE (e.g. AmSteel-Blue)
    "steel": 0.029,         # 7x19 galvanized wire rope
    "none": 0.0,
}

# Workspace status codes
USABLE, MARGIN, NO_TENSION, OVER_LIMIT = 0, 1, 2, 3
STATUS_NAMES = {USABLE: "usable", MARGIN: "edge margin",
                NO_TENSION: "can't tension all cables", OVER_LIMIT: "over tension limit"}


@dataclass
class Config:
    wall_width: float = 60.0      # ft
    wall_height: float = 100.0    # ft
    weight: float = 25.0          # lbf, as built with all cable on the spools
    chassis_w: float = 1.0        # ft, horizontal spacing of corner fairleads
    chassis_h: float = 1.0        # ft, vertical spacing of corner fairleads
    t_pre: float = 15.0           # lbf, pretension: the slackest cable sits here
    t_max: float = 300.0          # lbf, cable tension limit
    cable_w: float = CABLE_PRESETS["dyneema"]
    sag_ratio: float = 0.01       # max mid-span sag / length (raises the floor)
    standoff: float = 0.25        # ft, fairlead distance out from the wall face
    margin: float = 1.0           # ft, min gap between chassis edge and wall edge
    resolution: float = 0.5       # ft, grid spacing
    speed: float = 100.0 / 300.0  # ft/s, max robot speed: 8 stories (~100 ft) in 5 min
    # Anchor placement relative to the wall corners (0 = exactly at the corners)
    anchor_side: float = 0.0      # ft, all anchors moved outward past the side edges
    anchor_top: float = 0.0       # ft, top anchors raised above the top edge (roof davit/mast)
    anchor_bottom: float = 0.0    # ft, bottom anchors lowered below the bottom edge
    # Energy / battery estimate
    swath: float = 1.0            # ft, spacing between vertical coverage passes
    drive_eff: float = 0.85       # driver x gearbox efficiency (motor winding loss is computed
                                  # separately as holding power below)
    # Holding power: winding (I^2 R) loss just to hold torque, P = (tau / Km)^2, every winch
    km: float = 2.0               # N*m/sqrt(W) at the actuator OUTPUT (= motor Km x gear ratio);
                                  # ~2 is an estimate for an AK80-9-class actuator, check datasheet
    spool_radius_in: float = 1.0  # in, spool radius (turns tension into holding torque)
    hotel_w: float = 20.0         # W, always-on electronics: controller, drivers idle, sensors, radio
    contingency: float = 1.2      # x energy, for accel, friction, pauses, repositioning
    battery_wh: float = 800.0     # Wh, 10 lb (4.5 kg) Li-ion e-bike pack (~48 V 17 Ah)
    battery_usable: float = 0.8   # fraction of rated capacity actually used

    @property
    def anchors(self) -> np.ndarray:
        W, H = self.wall_width, self.wall_height
        s, t, b = self.anchor_side, self.anchor_top, self.anchor_bottom
        return np.array([[-s, H + t, 0], [W + s, H + t, 0],
                         [-s, -b, 0], [W + s, -b, 0]], dtype=float)


# --------------------------------------------------------------------------
# Solver
# --------------------------------------------------------------------------

def solve_tensions(cfg: Config, x, y):
    """Tensions and winch power at robot-center positions (x, y), any array shape."""
    shape = np.shape(x)
    x = np.asarray(x, float).ravel()
    y = np.asarray(y, float).ravel()
    n_pts = x.size
    w = cfg.cable_w

    # Geometry: corner fairleads and straight cable chords
    offs = CORNER_SIGN * np.array([cfg.chassis_w / 2, cfg.chassis_h / 2])   # b_i
    p = np.empty((n_pts, 4, 3))
    p[..., 0] = x[:, None] + offs[:, 0]
    p[..., 1] = y[:, None] + offs[:, 1]
    p[..., 2] = cfg.standoff
    vec = cfg.anchors[None] - p
    L = np.linalg.norm(vec, axis=-1)
    u = vec / L[..., None]
    u_xy = np.hypot(u[..., 0], u[..., 1])
    cos_th = np.abs(u[..., 0]) / np.maximum(u_xy, 1e-12)

    # Load: as-built weight, minus deployed cable, plus half of each span
    load = cfg.weight - w * L.sum(axis=1) / 2
    moment = (offs[None, :, 0] * w * L / 2).sum(axis=1)   # half-span weights at corners

    # A T = rhs  (x force, y force, moment about robot center)
    A = np.stack([u[..., 0], u[..., 1],
                  offs[None, :, 0] * u[..., 1] - offs[None, :, 1] * u[..., 0]], axis=1)
    rhs = np.stack([np.zeros(n_pts), load, moment], axis=1)

    # Null vector by cofactors: n_i = (-1)^i det(A without column i)
    cols = [[1, 2, 3], [0, 2, 3], [0, 1, 3], [0, 1, 2]]
    null = np.stack([(-1) ** i * np.linalg.det(A[:, :, c]) for i, c in enumerate(cols)], axis=1)
    null *= np.sign(null.sum(axis=1, keepdims=True) + 1e-300)
    null_norm = np.linalg.norm(null, axis=1, keepdims=True)
    tensionable = (null > 1e-9 * null_norm).all(axis=1) & (null_norm[:, 0] > 1e-12)

    # Least-norm balancing solution T0 = A^T (A A^T)^-1 rhs
    AAt = A @ A.transpose(0, 2, 1) + 1e-12 * np.eye(3)
    T0 = np.einsum("nji,nj->ni", A, np.linalg.solve(AAt, rhs[..., None])[..., 0])

    # Per-cable floor and the knob
    t_sag = w * L * cos_th / (8 * cfg.sag_ratio) if cfg.sag_ratio > 0 else np.zeros_like(L)
    floor = np.maximum(cfg.t_pre, t_sag)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = (floor - T0) / null
        lam = ratios.max(axis=1)
        slack = ratios.argmax(axis=1)                 # which cable sits at its floor
        T = T0 + lam[:, None] * null

    # Status
    half_w, half_h = cfg.chassis_w / 2, cfg.chassis_h / 2
    in_margin = ((x - half_w < cfg.margin) | (x + half_w > cfg.wall_width - cfg.margin) |
                 (y - half_h < cfg.margin) | (y + half_h > cfg.wall_height - cfg.margin))
    status = np.full(n_pts, USABLE)
    status[~tensionable] = NO_TENSION
    status[tensionable & (T.max(axis=1) > cfg.t_max)] = OVER_LIMIT
    status[in_margin] = MARGIN

    usable = status == USABLE
    T[~usable] = np.nan
    N = np.where(usable, (T * -u[..., 2]).sum(axis=1), np.nan)
    p_worst = T * cfg.speed * u_xy * FTLBF_S_TO_W     # W, worst-case direction
    tau = T * LBF_TO_N * cfg.spool_radius_in * IN_TO_M  # N*m at the actuator output
    p_hold = (tau / cfg.km) ** 2                      # W, winding loss just to hold it

    def R(a, *extra):
        return a.reshape(*shape, *extra)

    return {"T": R(T, 4), "power": R(p_worst, 4), "hold": R(p_hold, 4),
            "ux": R(u[..., 0], 4), "uy": R(u[..., 1], 4),
            "N": R(N), "length": R(L, 4), "floor": R(floor, 4), "slack": R(slack),
            "load": R(load), "status": R(status), "usable": R(usable)}


# --------------------------------------------------------------------------
# Energy for covering the whole usable wall
# --------------------------------------------------------------------------

def coverage_energy(cfg: Config, xs, ys, res):
    """Lawnmower coverage of the usable wall: vertical passes `swath` apart."""
    T, ux, uy, ok, H = res["T"], res["ux"], res["uy"], res["usable"], res["hold"]
    V, r = cfg.speed, cfg.resolution
    k = FTLBF_S_TO_W / cfg.drive_eff                 # mech ft*lbf/s -> battery W
    step = max(1, int(round(cfg.swath / r)))
    dt_cell = r / V

    e_motion = 0.0      # J drawn by motoring winches
    e_hold = 0.0        # J of winding loss holding tension, all 4 winches
    t_total = 0.0       # s
    path = 0.0          # ft
    passes = 0
    for j in range(0, len(xs), step):
        rows = np.flatnonzero(ok[:, j])
        if rows.size == 0:
            continue
        up = passes % 2 == 0
        sgn = 1.0 if up else -1.0
        # Vertical pass: reel-in rate of cable i = u_iy * v_y
        P = T[rows, j, :] * V * uy[rows, j, :] * sgn
        e_motion += np.clip(P, 0, None).sum() * k * dt_cell
        e_hold += H[rows, j, :].sum() * dt_cell
        t_total += rows.size * dt_cell
        path += rows.size * r
        # Sideways shift to the next pass, at the end of this one
        end = rows[-1] if up else rows[0]
        shift = step * r
        Ps = T[end, j, :] * V * ux[end, j, :]
        e_motion += np.clip(Ps, 0, None).sum() * k * (shift / V)
        e_hold += H[end, j, :].sum() * (shift / V)
        t_total += shift / V
        path += shift
        passes += 1

    e_hotel = cfg.hotel_w * t_total
    e_total = (e_motion + e_hold + e_hotel) * cfg.contingency
    usable_wh = cfg.battery_wh * cfg.battery_usable
    total_wh = e_total / 3600
    packs = max(1, math.ceil(total_wh / usable_wh)) if total_wh > 0 else 0
    return {"time_h": t_total * cfg.contingency / 3600, "path_ft": path, "passes": passes,
            "motion_wh": e_motion / 3600, "hold_wh": e_hold / 3600,
            "hotel_wh": e_hotel / 3600, "total_wh": total_wh,
            "avg_w": total_wh / max(t_total * cfg.contingency / 3600, 1e-9),
            "usable_wh": usable_wh, "packs": packs, "swaps": max(0, packs - 1),
            "battery_frac": total_wh / usable_wh}


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def make_grid(cfg: Config):
    """Cell-centered grid over the whole wall, so point counts = area fractions."""
    r = cfg.resolution
    xs = np.arange(r / 2, cfg.wall_width, r)
    ys = np.arange(r / 2, cfg.wall_height, r)
    X, Y = np.meshgrid(xs, ys)
    return xs, ys, X, Y


def wall_usage(res):
    st = res["status"]
    return {k: float((st == k).mean()) for k in STATUS_NAMES}


def _rms_peak(a, ok):
    a = a.reshape(-1, 4)[ok.ravel()]
    if a.size == 0:
        return None
    return {"rms": np.sqrt((a ** 2).mean(axis=0)), "peak": np.abs(a).max(axis=0)}


def stats(res):
    t = _rms_peak(res["T"], res["usable"])
    if t is None:
        return None
    h = res["hold"].reshape(-1, 4)[res["usable"].ravel()]
    return {"T": t, "P": _rms_peak(res["power"], res["usable"]),
            "H": {"mean": h.mean(axis=0), "peak": h.max(axis=0)}}


def describe_point(cfg: Config, x: float, y: float) -> str:
    r = solve_tensions(cfg, np.array([x]), np.array([y]))
    head = f"Robot center at ({x:.1f}, {y:.1f}) ft, carrying {r['load'][0]:.2f} lbf"
    st = int(r["status"][0])
    if st != USABLE:
        return f"{head}\n  IGNORED: {STATUS_NAMES[st]}"
    lines = [f"{c}: T={t:6.1f} lbf  worst-case motion power={pw:5.1f} W  holding={ph:5.1f} W"
             + ("   <- slackest (at floor)" if i == r["slack"][0] else "")
             for i, (c, t, pw, ph) in enumerate(zip(CABLES, r["T"][0], r["power"][0],
                                                     r["hold"][0]))]
    lines.append(f"Holding power, all 4 winches: {r['hold'][0].sum():.1f} W")
    return (head + f", speed {cfg.speed:.2f} ft/s\n  " + "\n  ".join(lines)
            + f"\n  Wall normal force N = {r['N'][0]:.2f} lbf")


def summarize(cfg: Config, res, en):
    use = wall_usage(res)
    s = stats(res)
    print("=" * 74)
    print(f"Wall {cfg.wall_width:g} x {cfg.wall_height:g} ft | robot {cfg.weight:g} lbf | "
          f"chassis {cfg.chassis_w:g} x {cfg.chassis_h:g} ft | cable {cfg.cable_w:g} lb/ft")
    print(f"Pretension {cfg.t_pre:g} lbf | limit {cfg.t_max:g} lbf | speed {cfg.speed:.3f} ft/s "
          f"({cfg.speed * 0.3048:.3f} m/s) | edge margin {cfg.margin:g} ft")
    if cfg.anchor_side or cfg.anchor_top or cfg.anchor_bottom:
        print(f"Anchors: +{cfg.anchor_side:g} ft sides, +{cfg.anchor_top:g} ft top, "
              f"-{cfg.anchor_bottom:g} ft bottom")
    print("-" * 74)
    print("Wall usage:")
    for k, name in STATUS_NAMES.items():
        print(f"  {name:26s} {use[k]:6.1%}" + ("" if k == USABLE else "  (ignored)"))
    print(f"  {'total ignored':26s} {1 - use[USABLE]:6.1%}")
    if s is None:
        print("No usable positions.")
        print("=" * 74)
        return
    print("-" * 74)
    print("Per winch over the usable wall (continuous = RMS, power = worst-case direction):")
    print(f"  {'winch':6s}{'T cont':>9s}{'T peak':>9s}{'P cont':>9s}{'P peak':>9s}")
    print(f"  {'':6s}{'lbf':>9s}{'lbf':>9s}{'W':>9s}{'W':>9s}")
    for i, c in enumerate(CABLES):
        print(f"  {c:6s}{s['T']['rms'][i]:9.1f}{s['T']['peak'][i]:9.1f}"
              f"{s['P']['rms'][i]:9.1f}{s['P']['peak'][i]:9.1f}")
    print(f"  Size each winch for >= {s['P']['rms'].max():.0f} W continuous, "
          f">= {s['P']['peak'].max():.0f} W peak (mechanical, at the cable)")
    print(f"Holding power (winding loss, Km {cfg.km:g} N*m/sqrt(W), "
          f"{cfg.spool_radius_in:g} in spool):")
    for i, c in enumerate(CABLES):
        print(f"  {c:6s} average {s['H']['mean'][i]:6.1f} W   peak {s['H']['peak'][i]:6.1f} W")
    print(f"  All 4 winches: average {s['H']['mean'].sum():.0f} W just to hold tension")
    print("-" * 74)
    print(f"Whole-wall coverage: {en['passes']} vertical passes, {cfg.swath:g} ft apart, "
          f"{en['path_ft']:.0f} ft of travel, ~{en['time_h']:.1f} h")
    print(f"  Winch motion   {en['motion_wh']:7.0f} Wh  (motoring winches / {cfg.drive_eff:.0%} "
          f"efficiency, no regen)")
    print(f"  Winch holding  {en['hold_wh']:7.0f} Wh  (winding loss holding tension, all 4)")
    print(f"  Electronics    {en['hotel_wh']:7.0f} Wh  ({cfg.hotel_w:g} W always on)")
    print(f"  Total          {en['total_wh']:7.0f} Wh  (x{cfg.contingency:g} contingency), "
          f"average {en['avg_w']:.0f} W from the battery")
    print(f"  Battery: {cfg.battery_wh:g} Wh 10 lb Li-ion pack, {en['usable_wh']:.0f} Wh usable -> "
          f"{en['battery_frac']:.0%} of one pack -> {en['packs']} pack(s), "
          f"{en['swaps']} swap(s)")
    print("=" * 74)


# --------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------

INK, MUTED, GRID = "#222222", "#666666", "#bbbbbb"
IGNORED_GRAY = "#e6e6e6"


def _extent(cfg, xs, ys):
    h = cfg.resolution / 2
    return (xs[0] - h, xs[-1] + h, ys[0] - h, ys[-1] + h)


def _style_wall_axes(ax, cfg):
    ax.set_xlim(0, cfg.wall_width)
    ax.set_ylim(0, cfg.wall_height)
    ax.set_xlabel("x (ft)", fontsize=8, color=MUTED)
    ax.set_ylabel("y (ft)", fontsize=8, color=MUTED)
    ax.tick_params(labelsize=7, colors=MUTED)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.plot(cfg.anchors[:, 0], cfg.anchors[:, 1], "s", color=INK, ms=5, clip_on=False)


def _heat(ax, X, Y, data, extent, cmap, norm, levels, title):
    im = ax.imshow(data, origin="lower", extent=extent, cmap=cmap, norm=norm,
                   aspect="equal", interpolation="nearest")
    if levels and np.isfinite(data).sum() > 4:
        cs = ax.contour(X, Y, data, levels=levels, colors=INK, linewidths=0.5, alpha=0.6)
        ax.clabel(cs, fmt="%g", fontsize=7)
    ax.set_title(title, fontsize=10, color=INK)
    return im


def _norm_and_levels(vmin, vmax, candidates):
    from matplotlib.colors import LogNorm, Normalize
    if vmax / max(vmin, 1e-9) > 20:
        norm = LogNorm(vmin=max(vmin, 1e-3), vmax=vmax)
    else:
        norm = Normalize(vmin=vmin, vmax=vmax)
    return norm, [l for l in candidates if vmin < l < vmax]


def _style_plain_axes(ax):
    ax.tick_params(labelsize=8, colors=MUTED)
    ax.grid(axis="y", color="#eeeeee", lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def plot_tensions(cfg, xs, ys, X, Y, res):
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, BoundaryNorm
    from matplotlib.patches import Patch

    T = res["T"]
    extent = _extent(cfg, xs, ys)
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_bad(IGNORED_GRAY)
    usable = res["usable"].any()
    vmin = np.nanmin(T) if usable else 1
    vmax = np.nanmax(T) if usable else 2
    norm, levels = _norm_and_levels(vmin, vmax, (20, 30, 50, 75, 100, 150, 200, 300, 500, 1000))

    fig, axes = plt.subplots(2, 3, figsize=(13.5, 9.5), constrained_layout=True)
    cable_axes = [axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]]
    for i, (ax, c) in enumerate(zip(cable_axes, CABLES)):
        im = _heat(ax, X, Y, T[..., i], extent, cmap, norm, levels, f"{c} cable tension")
    fig.colorbar(im, ax=axes[:, :2], label="Tension (lbf)", shrink=0.6, pad=0.02)

    Tmax = np.nanmax(T, axis=-1)
    imM = _heat(axes[0, 2], X, Y, Tmax, extent, cmap, norm, levels, "Max tension, any cable")
    fig.colorbar(imM, ax=axes[0, 2], label="lbf (same scale)", shrink=0.85)

    # Workspace map: usable in blue, ignored categories in grays
    use = wall_usage(res)
    colors = ["#6baed6", "#f2f2f2", "#8c8c8c", "#c8c8c8"]
    axW = axes[1, 2]
    axW.imshow(res["status"], origin="lower", extent=extent, aspect="equal",
               interpolation="nearest", cmap=ListedColormap(colors),
               norm=BoundaryNorm(np.arange(-0.5, 4.5), 4))
    axW.set_title(f"Workspace: {use[USABLE]:.1%} usable, {1 - use[USABLE]:.1%} ignored",
                  fontsize=10, color=INK)
    handles = [Patch(facecolor=colors[k], edgecolor=GRID, label=f"{STATUS_NAMES[k]} {use[k]:.1%}")
               for k in STATUS_NAMES]
    axW.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.1),
               fontsize=7, frameon=False, ncol=2)

    for ax in axes.flat:
        _style_wall_axes(ax, cfg)

    title = (f"Cable tensions: {cfg.weight:g} lbf robot, {cfg.chassis_w:g} x {cfg.chassis_h:g} ft "
             f"chassis, pretension {cfg.t_pre:g} lbf, limit {cfg.t_max:g} lbf (gray = ignored)")
    fig.suptitle(title + "\nclick a point for readout", fontsize=11, color=INK)

    marker = {"artists": []}

    def on_click(event):
        if event.inaxes is None or event.xdata is None:
            return
        px, py = event.xdata, event.ydata
        print(describe_point(cfg, px, py))
        for a in marker["artists"]:
            a.remove()
        marker["artists"] = []
        for ax in axes.flat:
            for a in cfg.anchors:
                marker["artists"].append(ax.plot([px, a[0]], [py, a[1]], color="#c0392b",
                                                 lw=0.6, alpha=0.6)[0])
            marker["artists"].append(ax.plot(px, py, "o", ms=7, mfc="none",
                                             mec="#c0392b", mew=1.5)[0])
        r = solve_tensions(cfg, np.array([px]), np.array([py]))
        if r["usable"][0]:
            txt = "   ".join(f"{c} {t:.1f}" for c, t in zip(CABLES, r["T"][0])) + "  lbf"
        else:
            txt = "ignored: " + STATUS_NAMES[int(r["status"][0])]
        fig.suptitle(f"({px:.1f}, {py:.1f}) ft   {txt}\nclick a point for readout",
                     fontsize=11, color=INK)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("button_press_event", on_click)
    return fig


def plot_power(cfg, xs, ys, X, Y, res, en):
    import matplotlib.pyplot as plt

    P = res["power"]
    s = stats(res)
    use = wall_usage(res)
    extent = _extent(cfg, xs, ys)
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_bad(IGNORED_GRAY)
    usable = res["usable"].any()
    vmin = np.nanmin(P) if usable else 1
    vmax = np.nanmax(P) if usable else 2
    norm, levels = _norm_and_levels(vmin, vmax, (10, 15, 20, 30, 50, 75, 100, 150, 200, 300, 500))

    fig, axes = plt.subplots(2, 3, figsize=(13.5, 9.5), constrained_layout=True)
    cable_axes = [axes[0, 0], axes[0, 1], axes[1, 0], axes[1, 1]]
    for i, (ax, c) in enumerate(zip(cable_axes, CABLES)):
        im = _heat(ax, X, Y, P[..., i], extent, cmap, norm, levels, f"{c} winch power")
        _style_wall_axes(ax, cfg)
    fig.colorbar(im, ax=axes[:, :2], shrink=0.6, pad=0.02,
                 label="Mechanical power at the cable (W), worst-case direction")

    # Bars: continuous vs peak
    axB = axes[0, 2]
    if s is not None:
        xpos = np.arange(4)
        bw = 0.38
        b1 = axB.bar(xpos - bw / 2 - 0.01, s["P"]["rms"], bw, color="#6baed6",
                     label="Continuous (RMS)")
        b2 = axB.bar(xpos + bw / 2 + 0.01, s["P"]["peak"], bw, color="#08519c", label="Peak")
        for bars in (b1, b2):
            for b in bars:
                axB.annotate(f"{b.get_height():.0f}", (b.get_x() + b.get_width() / 2,
                             b.get_height()), ha="center", va="bottom", fontsize=7,
                             color=INK, xytext=(0, 2), textcoords="offset points")
        axB.set_xticks(xpos, CABLES)
        axB.set_ylabel("W at the cable", fontsize=8, color=MUTED)
        axB.legend(fontsize=8, frameon=False, loc="upper left")
        axB.set_ylim(0, s["P"]["peak"].max() * 1.2)
    axB.set_title("Winch power per motor", fontsize=10, color=INK)
    _style_plain_axes(axB)

    # Summary: power sizing, whole-wall energy, battery
    axT = axes[1, 2]
    axT.axis("off")
    lines = [f"Max speed {cfg.speed:.3f} ft/s ({cfg.speed * 0.3048:.3f} m/s)"]
    if s is not None:
        lines += [f"Size each winch: >= {s['P']['rms'].max():.0f} W cont, "
                  f">= {s['P']['peak'].max():.0f} W peak",
                  f"Holding (Km {cfg.km:g}, {cfg.spool_radius_in:g} in spool): "
                  f"{s['H']['mean'].sum():.0f} W avg all 4,",
                  f"  up to {s['H']['peak'].max():.0f} W in one winch", ""]
    lines += [f"WHOLE-WALL ENERGY ({cfg.swath:g} ft passes)",
              f"  travel       {en['path_ft']:>7.0f} ft, ~{en['time_h']:.1f} h",
              f"  motion       {en['motion_wh']:>7.0f} Wh",
              f"  holding      {en['hold_wh']:>7.0f} Wh",
              f"  electronics  {en['hotel_wh']:>7.0f} Wh",
              f"  total        {en['total_wh']:>7.0f} Wh  (x{cfg.contingency:g})",
              f"  average draw {en['avg_w']:>7.0f} W", "",
              "BATTERY (10 lb Li-ion pack)",
              f"  {cfg.battery_wh:g} Wh rated, {en['usable_wh']:.0f} Wh usable",
              f"  uses {en['battery_frac']:.0%} of one pack",
              f"  -> {en['packs']} pack(s), {en['swaps']} swap(s)", "",
              f"WALL IGNORED: {1 - use[USABLE]:.1%}",
              f"  edge {use[MARGIN]:.1%}, no-tension {use[NO_TENSION]:.1%}, "
              f"over-limit {use[OVER_LIMIT]:.1%}"]
    axT.text(0.0, 0.98, "\n".join(lines), va="top", ha="left", family="monospace",
             fontsize=8.5, color=INK, transform=axT.transAxes)

    fig.suptitle(f"Winch power at {cfg.speed:.2f} ft/s (worst-case direction at each point) "
                 f"and whole-wall energy. Gray = ignored", fontsize=11, color=INK)
    return fig


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    # All defaults come from Config, so edit them in ONE place: the Config class.
    d = Config()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--width", type=float, default=d.wall_width, help="wall width, ft")
    ap.add_argument("--height", type=float, default=d.wall_height, help="wall height, ft")
    ap.add_argument("--weight", type=float, default=d.weight,
                    help="robot weight with all cable spooled (and battery, if onboard), lbf")
    ap.add_argument("--chassis", type=float, nargs=2, metavar=("W", "H"),
                    default=(d.chassis_w, d.chassis_h), help="corner fairlead spacing, ft")
    ap.add_argument("--tpre", type=float, default=d.t_pre, help="pretension of slackest cable, lbf")
    ap.add_argument("--tmax", type=float, default=d.t_max, help="cable tension limit, lbf")
    ap.add_argument("--cable", default=None,
                    help=f"cable preset {list(CABLE_PRESETS)} or linear weight in lb/ft "
                         f"(default {d.cable_w:g} lb/ft)")
    ap.add_argument("--sag-ratio", type=float, default=d.sag_ratio,
                    help="max mid-span sag / cable length (0 disables)")
    ap.add_argument("--standoff", type=float, default=d.standoff, help="fairlead standoff, ft")
    ap.add_argument("--margin", type=float, default=d.margin,
                    help="min gap between chassis edge and wall edge, ft")
    ap.add_argument("--res", type=float, default=d.resolution, help="grid spacing, ft")
    ap.add_argument("--speed", type=float, default=d.speed, help="max robot speed, ft/s")
    ap.add_argument("--anchor-side", type=float, default=d.anchor_side,
                    help="move all anchors outward past the side edges, ft")
    ap.add_argument("--anchor-top", type=float, default=d.anchor_top,
                    help="raise top anchors above the top edge, ft")
    ap.add_argument("--anchor-bottom", type=float, default=d.anchor_bottom,
                    help="lower bottom anchors below the bottom edge, ft")
    ap.add_argument("--swath", type=float, default=d.swath,
                    help="spacing between vertical coverage passes, ft")
    ap.add_argument("--eff", type=float, default=d.drive_eff,
                    help="battery-to-cable drive efficiency (0-1)")
    ap.add_argument("--hotel", type=float, default=d.hotel_w,
                    help="always-on electronics power, W")
    ap.add_argument("--km", type=float, default=d.km,
                    help="actuator motor constant at the OUTPUT, N*m/sqrt(W)")
    ap.add_argument("--spool-radius", type=float, default=d.spool_radius_in,
                    help="spool radius, in")
    ap.add_argument("--battery-wh", type=float, default=d.battery_wh,
                    help="battery pack rated energy, Wh")
    ap.add_argument("--point", type=float, nargs=2, metavar=("X", "Y"),
                    help="only report one location")
    ap.add_argument("--outdir", default=".", help="folder for the saved plots / CSV")
    ap.add_argument("--csv", action="store_true", help="also write grid results to CSV")
    ap.add_argument("--no-show", action="store_true", help="save plots without opening windows")
    args = ap.parse_args()

    if args.chassis[0] <= 0 or args.chassis[1] <= 0:
        ap.error("--chassis W H must both be > 0 for corner attachment")
    if args.cable is None:
        cable_w = d.cable_w
    elif args.cable in CABLE_PRESETS:
        cable_w = CABLE_PRESETS[args.cable]
    else:
        cable_w = float(args.cable)
    cfg = Config(wall_width=args.width, wall_height=args.height, weight=args.weight,
                 chassis_w=args.chassis[0], chassis_h=args.chassis[1], t_pre=args.tpre,
                 t_max=args.tmax, cable_w=cable_w, sag_ratio=args.sag_ratio,
                 standoff=args.standoff, margin=args.margin, resolution=args.res,
                 speed=args.speed, anchor_side=args.anchor_side,
                 anchor_top=args.anchor_top, anchor_bottom=args.anchor_bottom,
                 swath=args.swath, drive_eff=args.eff, hotel_w=args.hotel,
                 km=args.km, spool_radius_in=args.spool_radius,
                 battery_wh=args.battery_wh)

    if args.point:
        print(describe_point(cfg, *args.point))
        return

    xs, ys, X, Y = make_grid(cfg)
    res = solve_tensions(cfg, X, Y)
    en = coverage_energy(cfg, xs, ys, res)
    summarize(cfg, res, en)

    os.makedirs(args.outdir, exist_ok=True)
    if args.csv:
        path = os.path.join(args.outdir, "wall_winch_grid.csv")
        data = np.column_stack([X.ravel(), Y.ravel(), res["T"].reshape(-1, 4),
                                res["power"].reshape(-1, 4), res["status"].ravel()])
        np.savetxt(path, data, delimiter=",", fmt="%.4f", comments="",
                   header="x_ft,y_ft,T_TL,T_TR,T_BL,T_BR,P_TL_W,P_TR_W,P_BL_W,P_BR_W,"
                          "status(0=usable,1=margin,2=no_tension,3=over_limit)")
        print(f"Wrote {path}")

    import matplotlib
    if args.no_show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_t = plot_tensions(cfg, xs, ys, X, Y, res)
    fig_p = plot_power(cfg, xs, ys, X, Y, res, en)
    for fig, name in ((fig_t, "tension_map.png"), (fig_p, "power_map.png")):
        path = os.path.join(args.outdir, name)
        fig.savefig(path, dpi=130)
        print(f"Saved {path}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
