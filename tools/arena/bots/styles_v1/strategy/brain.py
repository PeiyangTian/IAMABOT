"""IAMABOT tournament controller.

Everything here works in the engine's mirrored frame (we are always team A, bottom-left),
so the same code plays both sides.

The design follows the engine source rather than the prose rules:

* A blaster shot is resolved with the shooter's *post-move, post-turn* pose against the
  enemies' post-move positions; the ray stops at the first enemy hull, the payload, a
  deposit, a wall or the map edge, and splashes every enemy whose centre is within
  ``splash + radius`` of that point.  We simulate exactly that before pulling a trigger, so
  no shot is spent on an invulnerable bot, on the payload or on thin air.
* A hit bot is immune for ``base_invulnerability_ticks``; shooters claim victims per tick so
  one volley is spread over several bodies instead of five shots landing as one.
* The payload moves at a fixed speed whenever exactly one team has a bot inside the capture
  radius, independent of how many.  Holding the circle needs bodies, not a crowd, so the
  army keeps a few anchors inside and a spread firing line behind them.
* Reinforcements that walk alone into a lost fight are what decides mirror matches, so the
  army regroups out of range when it is clearly outnumbered instead of feeding bots in.

All hot loops use plain floats; engine helpers are called through the raw C entry points
to avoid building ``Vec2`` objects in the inner loops.
"""

from __future__ import annotations

import math
import os

from . import *

try:  # raw entry points: same functions as core.channel, minus the Vec2 wrapping
    import ctypes as _ctypes

    from core import channel as _channel_mod
    from core._generated import bindings as _raw

    _RAW_OK = True
except Exception:  # pragma: no cover - defensive, the starterpack always has these
    _RAW_OK = False


DEBUG = bool(os.environ.get("IAMABOT_DEBUG"))

BATTLE = 0
HEALER = 1
EXTRACTOR = 2

RAD = math.pi / 180.0


def _ang(dx: float, dy: float) -> float:
    return math.atan2(dy, dx) / RAD % 360.0


def _adiff(a: float, b: float) -> float:
    """Signed shortest rotation from b to a, degrees."""
    return (a - b + 180.0) % 360.0 - 180.0


def _ray_circle(ox, oy, dx, dy, cx, cy, r):
    """Engine `ray_circle`: nearest t >= 0 where the unit ray meets the circle, or None."""
    mx = cx - ox
    my = cy - oy
    b = mx * dx + my * dy
    c = mx * mx + my * my - r * r
    if c > 0.0 and b < 0.0:
        return None
    disc = b * b - c
    if disc < 0.0:
        return None
    t = b - math.sqrt(disc)
    return t if t > 0.0 else 0.0


def _ray_boundary(ox, oy, dx, dy, size):
    t = 1e9
    if abs(dx) > 0.001:
        t = min(t, ((size if dx > 0 else 0.0) - ox) / dx)
    if abs(dy) > 0.001:
        t = min(t, ((size if dy > 0 else 0.0) - oy) / dy)
    return max(t, 0.0)


class Unit:
    __slots__ = ("id", "cls", "x", "y", "vx", "vy", "ang", "hp", "inv", "nft", "px", "py")


def _snapshot(fleet) -> list:
    out = []
    for b in fleet:
        sp = b.special
        tag = sp.tag
        u = Unit()
        u.id = b.id
        u.cls = tag
        p = b.pos
        u.x = p.x
        u.y = p.y
        v = b.vel
        u.vx = v.x
        u.vy = v.y
        u.ang = b.angle
        u.hp = b.health
        u.inv = b.invulnerable_until_tick
        u.nft = sp.payload.battle.next_fire_tick if tag == BATTLE else 0
        u.px = u.x + u.vx
        u.py = u.y + u.vy
        out.append(u)
    return out


class AdvancedStrategy:
    """Fire-control, formation, healing and economy controller."""

    # Production plan.  The opening spends the starting bank on fighters: the first
    # engagement at the payload decides most matches, and an economy that has not paid
    # back yet does not shoot.
    OPENING = os.environ.get("IAMABOT_OPENING", "BBHBBBHBBBBHBBBEE")
    EXTRACTOR_TARGET = int(os.environ.get("IAMABOT_EXTRACTORS", "6"))
    HEALER_RATIO = float(os.environ.get("IAMABOT_HEALER_RATIO", "0.24"))
    EXTRACTOR_CUTOFF = 4300  # an extractor built later cannot pay for itself

    CHEAP_BUDGET = 60_000

    def __init__(self) -> None:
        self.init_done = False
        self.built = 0
        self.prev_tick = -1
        self.aim: dict[int, int] = {}  # battle id -> enemy id
        self.heal_target: dict[int, int] = {}
        self.mine_slot: dict[int, int] = {}
        self.slot_cache_key = None
        self.slots: list = []
        self.slot_owner: dict[int, int] = {}
        self.mode = "hold"
        self.mode_since = 0
        self.debug_next = 0

    # ================================================================== setup

    def _setup(self, state: GameState) -> None:
        conf = get_config()
        self.conf = conf
        b = conf.bot
        self.R = b.radius
        self.SPEED = b.speed
        self.TURN = b.turn_speed
        self.RANGE = b.blaster_range
        self.DMG = b.blaster_damage
        self.MAXHP = b.health
        self.INV = b.base_invulnerability_ticks
        self.SPLASH = b.base_blaster_splash_radius + b.radius
        self.HEAL_R = b.base_heal_range
        self.HEAL_HALF = b.base_heal_arc_deg / 2.0
        self.STACK = int(b.heal_stack_cap + 1e-6)
        self.EXTRACT_R = b.base_extract_range
        self.P_R = conf.payload.radius
        self.CAP_R = conf.payload.capture_radius
        self.DEP_R = conf.deposit.radius
        self.SIZE = float(MAP_SIZE)
        self.END_T = conf.max_ticks - conf.endgame_ticks
        self.MAX_T = conf.max_ticks
        # Keep bots this far from a solid hull (payload/deposit) so a shot that hits the
        # hull cannot splash them: hull distance must exceed the splash reach.
        self.HULL_SAFE = b.base_blaster_splash_radius + b.radius + 0.08
        self.SPACING = 2.0 * self.R + b.base_blaster_splash_radius + 0.22  # ~1.02

        if _RAW_OK:
            self._h = _channel_mod._channel._handle
            self._los_fn = _raw.mm_line_of_sight
            self._nav_fn = _raw.mm_navigate_to
            self._navbuf = (_ctypes.c_float * 2)()
            self._free_fn = _raw.mm_disc_free

        d = state.deposit_me.pos
        self.dep = (d.x, d.y)
        d2 = state.deposit_other.pos
        self.dep_other = (d2.x, d2.y)
        # Spawn corner in our frame (engine spawns team A at its own goal corner).
        self.spawn = (self.R + 0.001, self.SIZE - self.R - 0.001)

        # Static grid of standable points with a small clearance margin.
        grid = []
        step = 0.5
        n = int(self.SIZE / step)
        for i in range(1, n):
            x = i * step
            for j in range(1, n):
                y = j * step
                if self._disc_free(x, y, self.R + 0.06):
                    grid.append((x, y))
        self.grid = grid
        self.mine_slots = self._make_mine_slots()
        self.init_done = True

    # ============================================================ engine calls

    def _los(self, ax, ay, bx, by) -> bool:
        if _RAW_OK:
            return self._los_fn(self._h, ax, ay, bx, by)
        return line_of_sight(Vec2(ax, ay), Vec2(bx, by))

    def _disc_free(self, x, y, r) -> bool:
        if _RAW_OK:
            return self._free_fn(self._h, x, y, r)
        return disc_free(Vec2(x, y), r)

    def _nav(self, fx, fy, tx, ty):
        if _RAW_OK:
            buf = self._navbuf
            self._nav_fn(self._h, fx, fy, tx, ty, buf)
            dx, dy = buf[0], buf[1]
        else:
            v = navigate_to(Vec2(fx, fy), Vec2(tx, ty))
            dx, dy = v.x, v.y
        n = math.hypot(dx, dy)
        if n > 1.0:
            dx /= n
            dy /= n
        return dx, dy

    # ================================================================ geometry

    def _make_mine_slots(self) -> list:
        dx0, dy0 = self.dep
        reach = self.EXTRACT_R + self.DEP_R - 0.35  # centre distance the ray still reaches
        min_r = self.DEP_R + self.HULL_SAFE
        # Prefer the side of the deposit facing our spawn corner: it is the side farthest
        # from the enemy's approach and the shortest walk for fresh extractors.
        sx, sy = self.spawn
        cand = []
        for (x, y) in self.grid:
            r = math.hypot(x - dx0, y - dy0)
            if r < min_r or r > reach:
                continue
            if not self._los(x, y, dx0, dy0):
                continue
            score = -abs(r - 2.3) * 0.8 - math.hypot(x - sx, y - sy) * 0.08
            # Tucked away from the map centre (where the enemy comes from).
            score += (y - 16.0) * 0.05
            cand.append((score, x, y))
        cand.sort(reverse=True)
        chosen: list = []
        for _, x, y in cand:
            if all((x - a) ** 2 + (y - b) ** 2 >= self.SPACING ** 2 for a, b in chosen):
                chosen.append((x, y))
            if len(chosen) >= 12:
                break
        if not chosen:
            chosen.append((dx0 - 2.0, dy0))
        return chosen

    def _payload(self, capture: float):
        v = payload_pos(max(-1.0, min(1.0, capture)))
        return v.x, v.y

    # ============================================================== main entry

    def __call__(self, state: GameState) -> FleetAction:
        try:
            if not self.init_done:
                self._setup(state)
            return self._tick(state)
        except Exception as exc:  # never let a bug take the whole fleet out of the match
            if DEBUG:
                import traceback

                traceback.print_exc()
            try:
                return self._fallback(state)
            except Exception:
                return FleetAction.new()

    # ================================================================ the tick

    def _tick(self, state: GameState) -> FleetAction:
        T = state.tick
        self.T = T
        cheap = get_budget().remaining < self.CHEAP_BUDGET
        act = FleetAction.new()

        me = _snapshot(state.fleet_me)
        op = _snapshot(state.fleet_other)
        self.me = me
        self.op = op
        self.op_by_id = {e.id: e for e in op}
        self.capture = state.capture
        px, py = self._payload(state.capture)
        self.P = (px, py)
        self.endgame = T >= self.END_T

        self._production(state, act, me)

        battles = [u for u in me if u.cls == BATTLE]
        healers = [u for u in me if u.cls == HEALER]
        extractors = [u for u in me if u.cls == EXTRACTOR]
        op_fighters = [e for e in op if e.cls != EXTRACTOR]

        # ---- situation -------------------------------------------------------
        cap2 = self.CAP_R ** 2
        self.op_in_zone = [e for e in op if (e.px - px) ** 2 + (e.py - py) ** 2 <= cap2]
        self.me_in_zone = [u for u in me if (u.x - px) ** 2 + (u.y - py) ** 2 <= cap2]
        ux, uy = self._enemy_dir(op_fighters)
        self.u = (ux, uy)

        # Strength near the objective: health-weighted fighters within a battle radius.
        near_r2 = 13.0 ** 2
        my_str = sum(
            (u.hp / self.MAXHP) * (1.0 if u.cls == BATTLE else 0.55)
            for u in me
            if u.cls != EXTRACTOR and (u.x - px) ** 2 + (u.y - py) ** 2 <= near_r2
        )
        op_str = sum(
            (e.hp / self.MAXHP) * (1.0 if e.cls == BATTLE else 0.55)
            for e in op_fighters
            if (e.x - px) ** 2 + (e.y - py) ** 2 <= near_r2
        )
        my_total = sum(
            (u.hp / self.MAXHP) * (1.0 if u.cls == BATTLE else 0.55) for u in me if u.cls != EXTRACTOR
        )
        self.my_str, self.op_str, self.my_total = my_str, op_str, my_total
        self._update_mode(T, my_str, op_str, my_total)

        # ---- roles -------------------------------------------------------------
        guards = self._pick_guards(battles, op)
        front = [b for b in battles if b.id not in guards]

        moves: dict[int, tuple] = {}
        # Battle formation.
        self._battle_moves(front, moves, cheap)
        for b in battles:
            if b.id in guards:
                gx, gy = guards[b.id]
                moves[b.id] = self._nav(b.x, b.y, gx, gy)

        # Extractors.
        mine_flags = self._extractor_moves(extractors, moves)

        # Healers.
        heal_plans = self._healer_moves(healers, me, moves)

        # Spread everyone apart so a single splash never lands on two of ours.
        self._separate(me, moves)

        # ---- aiming and fire -------------------------------------------------
        self._assign_aims(battles, op, cheap)
        fire = self._fire_control(battles, op, moves)

        # ---- write actions -----------------------------------------------------
        for u in me:
            ba = act.bots[u.id]
            mx, my = moves.get(u.id, (0.0, 0.0))
            ba.move_action = MoveAction(Vec2(mx, my))
            ox = u.x + mx * self.SPEED
            oy = u.y + my * self.SPEED
            if u.cls == BATTLE:
                ang = fire.get(u.id, (None, False))[0]
                if ang is None:
                    ang = self._battle_facing(u, ox, oy)
                ba.turn_action = TurnAction.TargetRotation(deg=ang % 360.0)
                ba.special_action = SpecialAction.Battle(fire=fire.get(u.id, (None, False))[1])
            elif u.cls == HEALER:
                target, ang, go = heal_plans.get(u.id, (0, None, False))
                if ang is None:
                    ang = _ang(px - ox, py - oy)
                ba.turn_action = TurnAction.TargetRotation(deg=ang % 360.0)
                ba.special_action = SpecialAction.Healer(fire=go, target=target)
            else:
                ang, mine = mine_flags.get(u.id, (None, False))
                if ang is None:
                    ang = _ang(self.dep[0] - ox, self.dep[1] - oy)
                ba.turn_action = TurnAction.TargetRotation(deg=ang % 360.0)
                ba.special_action = SpecialAction.Extractor(mine=mine)

        if DEBUG and T >= self.debug_next:
            self.debug_next = T + 250
            b = get_budget()
            print(
                f"[dbg] t={T} mode={self.mode} cap={state.capture:+.3f} my_str={my_str:.1f} "
                f"op_str={op_str:.1f} B/H/E={len(battles)}/{len(healers)}/{len(extractors)} "
                f"op={len(op)} zone={len(self.me_in_zone)}v{len(self.op_in_zone)} "
                f"tok={state.fabricator_me.tokens:.0f} bank={b.remaining} last={b.last_charge}",
                flush=True,
            )
        self.prev_tick = T
        return act

    # ============================================================== production

    def _production(self, state: GameState, act: FleetAction, me: list) -> None:
        counts = [0, 0, 0]
        for u in me:
            counts[u.cls] += 1
        total = len(me)
        T = state.tick
        nxt = self._next_class(counts, total, T)
        act.fabricator_next = nxt
        act.rush_order = (
            T < self.END_T
            and total < BOTS_MAX
            and state.fabricator_me.tokens >= self.conf.fabricator.rush_cost
        )

    def _next_class(self, counts, total, T) -> int:
        nb, nh, ne = counts
        # Opening: follow the scripted order while the class counts lag behind it.
        if T < 400:
            want = {"B": 0, "H": 0, "E": 0}
            for ch in self.OPENING:
                want[ch] += 1
                if nb < want["B"] and ch == "B":
                    return BATTLE
                if nh < want["H"] and ch == "H":
                    return HEALER
                if ne < want["E"] and ch == "E":
                    return EXTRACTOR
        dep_safe = not any(
            (e.x - self.dep[0]) ** 2 + (e.y - self.dep[1]) ** 2 < 9.0 ** 2
            for e in self.op
            if e.cls == BATTLE
        )
        fighters = nb + nh
        # Never build economy while the army is badly outnumbered at the objective.
        army_ok = self.my_total_guess() >= 0.8 * self.op_fighter_guess()
        if (
            ne < self.EXTRACTOR_TARGET
            and T < self.EXTRACTOR_CUTOFF
            and dep_safe
            and fighters >= 8
            and army_ok
        ):
            return EXTRACTOR
        if nh < int(self.HEALER_RATIO * (fighters + 1) + 0.5) and nb >= 4:
            return HEALER
        return BATTLE

    def my_total_guess(self) -> float:
        return sum(1 for u in self.me if u.cls != EXTRACTOR)

    def op_fighter_guess(self) -> float:
        return sum(1 for e in self.op if e.cls != EXTRACTOR)

    # ================================================================ strategy

    def _enemy_dir(self, op_fighters):
        px, py = self.P
        sx = sy = w = 0.0
        for e in op_fighters:
            d = math.hypot(e.x - px, e.y - py)
            if d < 15.0 and self._los(px, py, e.x, e.y):
                k = 1.0 / (1.0 + d)
                sx += (e.x - px) * k
                sy += (e.y - py) * k
                w += k
        if w > 0.0:
            n = math.hypot(sx, sy)
            if n > 1e-3:
                return sx / n, sy / n
        # No visible enemy: they come along the payload path from their side.
        ax, ay = self._payload(self.capture + 0.04)
        dx, dy = ax - px, ay - py
        n = math.hypot(dx, dy)
        if n < 1e-3:
            return 0.7071, -0.7071
        return dx / n, dy / n

    def _update_mode(self, T, my_str, op_str, my_total) -> None:
        # Regroup when the fight at the objective is clearly lost and reinforcements are
        # on the way; re-engage once the army is back to parity.
        if self.mode == "hold":
            if op_str > 1.45 * my_str + 1.0 and my_total < 0.85 * op_str and T > 300:
                self.mode = "regroup"
                self.mode_since = T
        else:
            if my_total >= 1.05 * op_str or T - self.mode_since > 900 or self.endgame:
                self.mode = "hold"
                self.mode_since = T

    def _pick_guards(self, battles, op) -> dict:
        """Battle bots peeled off to protect extractors from raiders."""
        dx0, dy0 = self.dep
        threats = [
            e
            for e in op
            if e.cls == BATTLE and (e.x - dx0) ** 2 + (e.y - dy0) ** 2 < 10.0 ** 2
        ]
        if not threats or self.endgame:
            return {}
        n = min(len(battles) // 2, len(threats) + 1, 5)
        order = sorted(battles, key=lambda b: (b.x - dx0) ** 2 + (b.y - dy0) ** 2)
        tx = sum(e.x for e in threats) / len(threats)
        ty = sum(e.y for e in threats) / len(threats)
        out = {}
        for i, b in enumerate(order[:n]):
            # Stand between the deposit and the raiders, a little spread.
            vx, vy = tx - dx0, ty - dy0
            d = math.hypot(vx, vy) or 1.0
            vx, vy = vx / d, vy / d
            off = (i - (n - 1) / 2.0) * self.SPACING
            gx = dx0 + vx * min(3.0, d * 0.5) - vy * off
            gy = dy0 + vy * min(3.0, d * 0.5) + vx * off
            out[b.id] = (gx, gy)
        return out

    # --------------------------------------------------------------- formation

    def _formation_slots(self, n: int):
        """Standing points around the payload: anchors inside the capture circle on our
        side, then a spread firing line behind them.  Cached while the payload is still."""
        px, py = self.P
        if self.mode == "regroup":
            # Fall back along our half of the path, out of the enemy's reach.
            bx, by = self._payload(self.capture - 0.16)
            cx, cy = bx, by
            ux, uy = self.u
        else:
            cx, cy = px, py
            ux, uy = self.u
        key = (round(cx * 2.0), round(cy * 2.0), round(math.atan2(uy, ux) / 0.35), self.mode, n)
        if key == self.slot_cache_key and len(self.slots) >= n:
            return self.slots
        anchors_wanted = 0 if self.mode == "regroup" else min(3, max(1, n // 4))
        cap_r = self.CAP_R - 0.25
        min_r = self.P_R + self.HULL_SAFE
        anchors = []
        line = []
        for (x, y) in self.grid:
            dx, dy = x - cx, y - cy
            r2 = dx * dx + dy * dy
            if r2 > 7.0 * 7.0:
                continue
            r = math.sqrt(r2)
            side = dx * ux + dy * uy  # >0 toward the enemy
            lat = -dx * uy + dy * ux
            if self.mode != "regroup" and min_r <= r <= cap_r:
                s = -abs(r - 1.85) * 1.5 - max(0.0, side) * 1.2 - abs(lat) * 0.25
                anchors.append((s, x, y))
            if 2.2 <= r <= 6.5:
                s = -abs(r - 3.7) * 0.6 - max(0.0, side + 0.8) * 0.9 - abs(lat) * 0.08
                line.append((s, x, y))
        anchors.sort(reverse=True)
        line.sort(reverse=True)
        chosen: list = []
        sp2 = self.SPACING ** 2

        def take(cands, limit):
            k = 0
            for s, x, y in cands:
                if k >= limit:
                    break
                if all((x - a) ** 2 + (y - b) ** 2 >= sp2 for a, b in chosen):
                    if self._los(x, y, cx, cy):
                        chosen.append((x, y))
                        k += 1

        take(anchors[:120], anchors_wanted)
        take(line[:400], n - len(chosen))
        if len(chosen) < n:  # corridor too narrow: accept anything standable nearby
            rest = sorted(
                ((x - cx) ** 2 + (y - cy) ** 2, x, y) for (x, y) in self.grid
                if (x - cx) ** 2 + (y - cy) ** 2 <= 9.0 ** 2
            )
            for _, x, y in rest:
                if len(chosen) >= n:
                    break
                if all((x - a) ** 2 + (y - b) ** 2 >= sp2 for a, b in chosen) and (
                    (x - px) ** 2 + (y - py) ** 2 >= min_r ** 2
                ):
                    chosen.append((x, y))
        self.slots = chosen
        self.slot_cache_key = key
        return chosen

    def _battle_moves(self, front, moves, cheap) -> None:
        if not front:
            return
        slots = self._formation_slots(len(front))
        if not slots:
            return
        # Greedy min-distance matching, keeping previous owners when still close.
        pairs = []
        for b in front:
            for k, (sx, sy) in enumerate(slots):
                d = (b.x - sx) ** 2 + (b.y - sy) ** 2
                if self.slot_owner.get(b.id) == k:
                    d *= 0.6
                # Anchor slots (first ones) go preferably to healthy bots.
                pairs.append((d + (0.0 if b.hp > 6.0 else 4.0 * (k < 3)), b.id, k))
        pairs.sort()
        taken_b: set = set()
        taken_s: set = set()
        owner = {}
        for d, bid, k in pairs:
            if bid in taken_b or k in taken_s:
                continue
            taken_b.add(bid)
            taken_s.add(k)
            owner[bid] = k
        self.slot_owner = owner
        for b in front:
            k = owner.get(b.id)
            if k is None:
                tx, ty = self.P
            else:
                tx, ty = slots[k]
            moves[b.id] = self._nav(b.x, b.y, tx, ty)

    def _battle_facing(self, b, ox, oy) -> float:
        """Idle battle bots pre-aim where the enemy will appear."""
        e = self.op_by_id.get(self.aim.get(b.id, -1))
        if e is not None:
            return _ang(e.px - ox, e.py - oy)
        # Nearest enemy fighter in rough range, else along the enemy direction.
        best = None
        bd = 1e9
        for e in self.op:
            d = (e.x - ox) ** 2 + (e.y - oy) ** 2
            if d < bd:
                bd, best = d, e
        if best is not None and bd < (self.RANGE + 4.0) ** 2:
            return _ang(best.px - ox, best.py - oy)
        px, py = self.P
        ux, uy = self.u
        return _ang(px + ux * 6.0 - ox, py + uy * 6.0 - oy)

    # -------------------------------------------------------------- extractors

    def _extractor_moves(self, extractors, moves) -> dict:
        out = {}
        if not extractors:
            return out
        if self.endgame:
            return self._endgame_extractors(extractors, moves)
        slots = self.mine_slots
        used = {k for bid, k in self.mine_slot.items() if any(e.id == bid for e in extractors)}
        for u in sorted(extractors, key=lambda e: e.id):
            k = self.mine_slot.get(u.id)
            if k is None:
                free = [i for i in range(len(slots)) if i not in used]
                if not free:
                    k = u.id % len(slots)
                else:
                    k = min(free, key=lambda i: (slots[i][0] - u.x) ** 2 + (slots[i][1] - u.y) ** 2)
                self.mine_slot[u.id] = k
                used.add(k)
            tx, ty = slots[k]
            moves[u.id] = self._nav(u.x, u.y, tx, ty)
            out[u.id] = (None, True)  # always ask to mine; the engine checks the ray
        # Forget dead extractors so their slots free up.
        alive = {e.id for e in extractors}
        for bid in list(self.mine_slot):
            if bid not in alive:
                del self.mine_slot[bid]
        return out

    def _endgame_extractors(self, extractors, moves) -> dict:
        """Tokens are worthless now.  Extractors become bodies: they keep the capture
        circle contested when the army cannot, and one always hides to avoid a wipe."""
        out = {}
        px, py = self.P
        ux, uy = self.u
        need_bodies = len(self.me_in_zone) < 2
        hide = max(extractors, key=lambda e: min(
            ((e.x - o.x) ** 2 + (e.y - o.y) ** 2 for o in self.op), default=1e9))
        for i, u in enumerate(sorted(extractors, key=lambda e: e.id)):
            if u.id == hide.id and len(extractors) > 1 or len(extractors) == 1 and not need_bodies:
                tx, ty = self.spawn
                tx += 1.5
                ty -= 1.5
            elif need_bodies:
                ang = math.atan2(-uy, -ux) + (i - len(extractors) / 2.0) * 0.5
                tx = px + math.cos(ang) * 1.9
                ty = py + math.sin(ang) * 1.9
            else:
                ang = math.atan2(-uy, -ux) + (i - len(extractors) / 2.0) * 0.35
                tx = px + math.cos(ang) * 5.0
                ty = py + math.sin(ang) * 5.0
            moves[u.id] = self._nav(u.x, u.y, tx, ty)
            out[u.id] = (None, False)
        return out

    # ----------------------------------------------------------------- healers

    def _healer_moves(self, healers, me, moves) -> dict:
        plans = {}
        if not healers:
            return plans
        maxhp = self.MAXHP
        wounded = [a for a in me if a.hp < maxhp - 0.15]
        assigned: dict[int, int] = {}
        px, py = self.P
        # Where are the threats relative to our army?
        army = [u for u in me if u.cls == BATTLE] or me
        ax = sum(u.x for u in army) / len(army)
        ay = sum(u.y for u in army) / len(army)
        ux, uy = self.u
        order = sorted(healers, key=lambda h: h.id)
        for idx, h in enumerate(order):
            best = None
            best_s = -1e9
            prev = self.heal_target.get(h.id)
            for a in wounded:
                if a.id == h.id or assigned.get(a.id, 0) >= self.STACK:
                    continue
                d = math.hypot(a.x - h.x, a.y - h.y)
                s = (maxhp - a.hp) * 1.0
                s += 2.5 if a.cls == BATTLE else (1.0 if a.cls == HEALER else 0.0)
                s -= max(0.0, d - (self.HEAL_R - 0.4)) * 0.9
                if a.id == prev:
                    s += 1.5
                if s > best_s:
                    best_s, best = s, a
            if best is None:
                # Idle: sit behind the army, spread laterally.
                off = (idx - (len(order) - 1) / 2.0) * 1.1
                tx = ax - ux * 2.2 - uy * off
                ty = ay - uy * 2.2 + ux * off
                moves[h.id] = self._nav(h.x, h.y, tx, ty)
                self.heal_target.pop(h.id, None)
                plans[h.id] = (0, None, False)
                continue
            slot = assigned.get(best.id, 0)
            assigned[best.id] = slot + 1
            self.heal_target[h.id] = best.id
            # Stand behind the patient (away from the enemy), fanned out by slot.
            bx, by = -ux, -uy
            lat = (slot - 1) * 1.05 if slot else 0.0
            dist = 1.9
            tx = best.x + bx * dist - by * lat
            ty = best.y + by * dist + bx * lat
            mx, my = self._nav(h.x, h.y, tx, ty)
            # Close the gap first if out of reach.
            moves[h.id] = (mx, my)
            ox = h.x + mx * self.SPEED
            oy = h.y + my * self.SPEED
            ex, ey = best.x + best.vx, best.y + best.vy
            want = _ang(ex - ox, ey - oy)
            turn = max(-self.TURN, min(self.TURN, _adiff(want, h.ang)))
            after = h.ang + turn
            d = math.hypot(ex - ox, ey - oy)
            ok = (
                d <= self.HEAL_R - 0.03
                and abs(_adiff(want, after)) <= self.HEAL_HALF - 1.0
                and self._los(ox, oy, ex, ey)
            )
            plans[h.id] = (best.id, want, ok)
        return plans

    # -------------------------------------------------------------- separation

    def _separate(self, me, moves) -> None:
        sp = self.SPACING
        sp2 = sp * sp
        px, py = self.P
        hull_p = self.P_R + self.HULL_SAFE
        dx0, dy0 = self.dep
        hull_d = self.DEP_R + self.HULL_SAFE
        n = len(me)
        push = {u.id: [0.0, 0.0] for u in me}
        for i in range(n):
            a = me[i]
            for j in range(i + 1, n):
                b = me[j]
                dx = a.x - b.x
                dy = a.y - b.y
                d2 = dx * dx + dy * dy
                if d2 >= sp2:
                    continue
                if d2 < 1e-6:
                    ang = (a.id * 2.399963) % (2 * math.pi)
                    dx, dy, d = math.cos(ang), math.sin(ang), 1e-3
                else:
                    d = math.sqrt(d2)
                    dx /= d
                    dy /= d
                k = (sp - d) / sp
                pa = push[a.id]
                pb = push[b.id]
                pa[0] += dx * k
                pa[1] += dy * k
                pb[0] -= dx * k
                pb[1] -= dy * k
        for u in me:
            mx, my = moves.get(u.id, (0.0, 0.0))
            rx, ry = push[u.id]
            # Keep off solid hulls so a shot into the payload/deposit cannot splash us.
            for cx, cy, lim in ((px, py, hull_p), (dx0, dy0, hull_d)):
                dx, dy = u.x - cx, u.y - cy
                d = math.hypot(dx, dy)
                if d < lim and d > 1e-6 and not (u.cls == EXTRACTOR and cx == dx0):
                    k = (lim - d) / lim * 2.0
                    rx += dx / d * k
                    ry += dy / d * k
            vx = mx + rx * 1.6
            vy = my + ry * 1.6
            nrm = math.hypot(vx, vy)
            if nrm > 1.0:
                vx /= nrm
                vy /= nrm
            moves[u.id] = (vx, vy)

    # ================================================================== combat

    def _enemy_value(self, e) -> float:
        px, py = self.P
        v = 10.0
        if e.cls == HEALER:
            v += 5.0
        elif e.cls == EXTRACTOR:
            v -= 3.0
        if (e.x - px) ** 2 + (e.y - py) ** 2 <= (self.CAP_R + 0.4) ** 2:
            v += 5.0
        v += (self.MAXHP - e.hp) * 0.9
        if e.hp <= self.DMG + 0.01:
            v += 5.0
        return v

    def _assign_aims(self, battles, op, cheap) -> None:
        T = self.T
        if not op:
            self.aim = {}
            return
        if cheap and T % 4:
            return
        values = {e.id: self._enemy_value(e) for e in op}
        count: dict[int, int] = {}
        new_aim = {}
        rng2 = (self.RANGE + 1.0) ** 2
        order = sorted(battles, key=lambda b: b.nft)
        for b in order:
            cands = []
            for e in op:
                dx = e.px - b.x
                dy = e.py - b.y
                d2 = dx * dx + dy * dy
                if d2 > rng2:
                    continue
                d = math.sqrt(d2)
                turn = abs(_adiff(_ang(dx, dy), b.ang)) / self.TURN
                wait = max(turn, b.nft - T, e.inv - T, 0)
                s = values[e.id] - 0.14 * wait - 0.2 * d
                if self.aim.get(b.id) == e.id:
                    s += 2.5
                k = count.get(e.id, 0)
                need = int(e.hp / self.DMG + 0.999)
                if k >= need:
                    s -= 6.0 * (k - need + 1)
                cands.append((s, e))
            if not cands:
                continue
            cands.sort(key=lambda t: -t[0])
            for s, e in cands[:4]:
                if self._los(b.x, b.y, e.px, e.py):
                    new_aim[b.id] = e.id
                    count[e.id] = count.get(e.id, 0) + 1
                    break
        self.aim = new_aim

    def _fire_control(self, battles, op, moves) -> dict:
        """Decide each battle bot's heading and trigger for this tick.

        Returns {id: (heading, fire)}.  The heading is where the bot turns; fire is only
        set when an exact replay of the engine's ray says it lands on a vulnerable enemy
        that no earlier shooter in this volley has claimed.
        """
        T = self.T
        out = {}
        if not op:
            return out
        claimed: set = set()
        spd = self.SPEED
        # Cheapest shooters (fewest options) decide first would be ideal; readiness order
        # plus claim tracking is close enough and deterministic.
        ready = []
        for b in battles:
            mx, my = moves.get(b.id, (0.0, 0.0))
            ox = b.x + mx * spd
            oy = b.y + my * spd
            e = self.op_by_id.get(self.aim.get(b.id, -1))
            if e is not None:
                want = _ang(e.px - ox, e.py - oy)
            else:
                want = self._battle_facing(b, ox, oy)
            turn = max(-self.TURN, min(self.TURN, _adiff(want, b.ang)))
            after = (b.ang + turn) % 360.0
            if b.nft <= T:
                ready.append((b, ox, oy, want, after))
            out[b.id] = (want, False)

        for b, ox, oy, want, after in ready:
            victims = self._shot_victims(ox, oy, after, op, claimed)
            if not victims:
                # Maybe a quick re-aim at some other enemy already inside the turn cone
                # gives a shot this very tick.
                alt = self._snap_shot(b, ox, oy, op, claimed)
                if alt is None:
                    continue
                want, after, victims = alt
            for v in victims:
                claimed.add(v)
            out[b.id] = (want, True)
        return out

    def _snap_shot(self, b, ox, oy, op, claimed):
        best = None
        rng2 = self.RANGE ** 2
        for e in op:
            if e.inv > self.T or e.id in claimed:
                continue
            dx, dy = e.px - ox, e.py - oy
            if dx * dx + dy * dy > rng2:
                continue
            want = _ang(dx, dy)
            if abs(_adiff(want, b.ang)) > self.TURN:
                continue
            after = (b.ang + max(-self.TURN, min(self.TURN, _adiff(want, b.ang)))) % 360.0
            victims = self._shot_victims(ox, oy, after, op, claimed)
            if victims:
                val = sum(self._enemy_value(self.op_by_id[v]) for v in victims)
                if best is None or val > best[0]:
                    best = (val, want, after, victims)
        if best is None:
            return None
        return best[1], best[2], best[3]

    def _shot_victims(self, ox, oy, heading, op, claimed):
        """Enemy ids this shot would damage, requiring agreement between the predicted
        (pos + vel) and the stationary (pos) enemy layouts so a jink cannot waste it."""
        dx = math.cos(heading * RAD)
        dy = math.sin(heading * RAD)
        rng = self.RANGE
        r = self.R
        t_bound = min(rng, _ray_boundary(ox, oy, dx, dy, self.SIZE))
        # Solid non-bot colliders.
        t_static = t_bound
        px, py = self.P
        t = _ray_circle(ox, oy, dx, dy, px, py, self.P_R)
        if t is not None and t < t_static:
            t_static = t
        for cx, cy in (self.dep, self.dep_other):
            t = _ray_circle(ox, oy, dx, dy, cx, cy, self.DEP_R)
            if t is not None and t < t_static:
                t_static = t
        result = None
        for mode in (0, 1):
            best_t = t_static
            for e in op:
                ex, ey = (e.px, e.py) if mode == 0 else (e.x, e.y)
                t = _ray_circle(ox, oy, dx, dy, ex, ey, r)
                if t is not None and t < best_t:
                    best_t = t
            ix = ox + dx * best_t
            iy = oy + dy * best_t
            s2 = self.SPLASH ** 2 - 0.004
            vic = set()
            for e in op:
                if e.inv > self.T or e.id in claimed:
                    continue
                ex, ey = (e.px, e.py) if mode == 0 else (e.x, e.y)
                if (ex - ix) ** 2 + (ey - iy) ** 2 <= s2:
                    vic.add(e.id)
            if not vic:
                return None
            if mode == 0:
                # Walls stop the ray before the impact point?
                back = max(0.0, best_t - 0.02)
                if not self._los(ox, oy, ox + dx * back, oy + dy * back):
                    return None
                result = vic
            else:
                result = result & vic
        return result or None

    # ================================================================ fallback

    def _fallback(self, state: GameState) -> FleetAction:
        act = FleetAction.new()
        conf = get_config()
        act.fabricator_next = BATTLE
        act.rush_order = (
            state.tick < conf.max_ticks - conf.endgame_ticks
            and not state.fleet_me.is_full()
            and state.fabricator_me.tokens >= conf.fabricator.rush_cost
        )
        payload = state.payload_pos()
        enemies = list(state.fleet_other)
        for bot in state.fleet_me:
            ba = act.bots[bot.id]
            tag = bot.special.tag
            ba.move_action = move_bot(navigate_to(bot.pos, payload))
            if tag == EXTRACTOR:
                ba.turn_action = turn_towards(state.deposit_me.pos)
                ba.special_action = SpecialAction.Extractor(mine=True)
                ba.move_action = move_bot(navigate_to(bot.pos, state.deposit_me.pos))
            elif tag == HEALER:
                ba.special_action = SpecialAction.Healer(fire=False, target=0)
            else:
                enemy = min(enemies, key=lambda o: bot.pos.dist_sq(o.pos), default=None)
                if enemy is not None:
                    ba.turn_action = turn_towards(enemy.pos)
                ba.special_action = SpecialAction.Battle(fire=False)
        return act


class ReferenceStrategy:
    """Deliberately simple active baseline used only by local A/B tests."""

    def __call__(self, state: GameState) -> FleetAction:
        conf = get_config()
        action = FleetAction.new()
        allies = list(state.fleet_me)
        enemies = list(state.fleet_other)
        n_ext = sum(1 for b in allies if b.special.tag == EXTRACTOR)
        action.fabricator_next = EXTRACTOR if n_ext < 3 else BATTLE
        action.rush_order = (
            state.tick < conf.max_ticks - conf.endgame_ticks
            and not state.fleet_me.is_full()
            and state.fabricator_me.tokens >= conf.fabricator.rush_cost
        )
        mine = state.deposit_me.pos + Vec2(0.0, conf.deposit.radius + conf.bot.radius + 0.2)
        payload = state.payload_pos()
        for bot in allies:
            bot_action = action.bots[bot.id]
            if bot.special.tag == EXTRACTOR:
                bot_action.move_action = move_bot(navigate_to(bot.pos, mine))
                bot_action.turn_action = turn_towards(state.deposit_me.pos)
                bot_action.special_action = SpecialAction.Extractor(mine=True)
                continue
            enemy = min(enemies, key=lambda other: bot.pos.dist_sq(other.pos), default=None)
            target = enemy.pos if enemy else payload
            bot_action.move_action = move_bot(navigate_to(bot.pos, payload))
            bot_action.turn_action = turn_towards(target)
            can_fire = (
                enemy is not None
                and bot.next_fire_tick <= state.tick
                and bot.pos.dist(target) <= conf.bot.blaster_range
                and line_of_sight(bot.pos, target)
            )
            bot_action.special_action = SpecialAction.Battle(fire=can_fire)
        return action
