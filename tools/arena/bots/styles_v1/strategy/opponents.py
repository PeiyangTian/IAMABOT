"""Alternative play styles built on the v1 machinery, used only as sparring partners."""
from __future__ import annotations

import math

from .brain import AdvancedStrategy, BATTLE, HEALER, EXTRACTOR, _ang


class Sniper(AdvancedStrategy):
    """Keeps the army at long range around the payload with a single body inside."""

    def _formation_slots(self, n):
        px, py = self.P
        ux, uy = self.u
        key = (round(px * 2), round(py * 2), round(math.atan2(uy, ux) / 0.35), n, "snipe")
        if key == self.slot_cache_key and len(self.slots) >= n:
            return self.slots
        cands = []
        for (x, y) in self.grid:
            dx, dy = x - px, y - py
            r = math.hypot(dx, dy)
            if r > 10.0:
                continue
            side = dx * ux + dy * uy
            s = -abs(r - 8.0) * 0.6 - max(0.0, side + 1.0) * 0.5
            cands.append((s, x, y))
        cands.sort(reverse=True)
        chosen = []
        # one anchor
        for (x, y) in sorted(self.grid, key=lambda p: abs(math.hypot(p[0] - px, p[1] - py) - 1.9)):
            if self._los(x, y, px, py):
                chosen.append((x, y))
                break
        for s, x, y in cands[:600]:
            if len(chosen) >= n:
                break
            if all((x - a) ** 2 + (y - b) ** 2 >= 1.1 for a, b in chosen) and self._los(x, y, px, py):
                chosen.append((x, y))
        while len(chosen) < n:
            chosen.append((px - ux * 3, py - uy * 3))
        self.slots = chosen
        self.slot_cache_key = key
        return chosen


class Raider(AdvancedStrategy):
    """Sends a large detachment to kill the enemy economy and camp its spawn."""

    RAID_FRACTION = 0.5

    def _battle_moves(self, front, moves, cheap):
        n_raid = int(len(front) * self.RAID_FRACTION)
        if self.T < 200:
            n_raid = 0
        front_sorted = sorted(front, key=lambda b: b.id)
        raiders = front_sorted[:n_raid]
        rest = front_sorted[n_raid:]
        super()._battle_moves(rest, moves, cheap)
        dx0, dy0 = self.dep_other
        enemy_ex = [e for e in self.op if e.cls == EXTRACTOR]
        for i, b in enumerate(raiders):
            if enemy_ex:
                ang = i * 2.0 * math.pi / max(1, len(raiders))
                tx, ty = dx0 + math.cos(ang) * 4.0, dy0 + math.sin(ang) * 4.0
            else:
                sx, sy = self.SIZE - 1.5, 1.5  # enemy spawn corner in our frame
                ang = i * 2.0 * math.pi / max(1, len(raiders))
                tx, ty = sx - 3.0 + math.cos(ang) * 1.5, sy + 3.0 + math.sin(ang) * 1.5
            moves[b.id] = self._nav(b.x, b.y, tx, ty)

    def _enemy_value(self, e):
        v = super()._enemy_value(e)
        if e.cls == EXTRACTOR:
            v += 8.0
        return v


class Hunter(AdvancedStrategy):
    """Ignores formation: the whole army chases the enemy's nearest concentration."""

    def _battle_moves(self, front, moves, cheap):
        fighters = [e for e in self.op if e.cls != EXTRACTOR] or self.op
        if not fighters:
            return super()._battle_moves(front, moves, cheap)
        for b in front:
            e = min(fighters, key=lambda o: (o.x - b.x) ** 2 + (o.y - b.y) ** 2)
            d = math.hypot(e.x - b.x, e.y - b.y)
            if d > 6.0:
                moves[b.id] = self._nav(b.x, b.y, e.x, e.y)
            else:
                moves[b.id] = (0.0, 0.0)


class Stacker(AdvancedStrategy):
    """Good fire control but no spacing discipline: everything sits on the payload."""

    def _separate(self, me, moves):
        return

    def _formation_slots(self, n):
        px, py = self.P
        ux, uy = self.u
        return [(px - ux * 1.3, py - uy * 1.3)] * n


class Thief(AdvancedStrategy):
    """Gang-style: most of the army and every extractor go to the ENEMY deposit, kill its
    miners and take its extraction slots; a small group contests the payload."""

    OPENING = "BBBBEBBBEBBBEBHEB"
    EXTRACTOR_TARGET = 8
    RAID_FRACTION = 0.7

    def _setup(self, state):
        super()._setup(state)
        # Mine at the enemy deposit instead of ours.
        self.dep, self.dep_other = self.dep_other, self.dep
        self.mine_slots = self._make_mine_slots_at(self.dep)

    def _make_mine_slots_at(self, dep):
        dx0, dy0 = dep
        reach = self.EXTRACT_R + self.DEP_R - 0.35
        min_r = self.DEP_R + self.HULL_SAFE
        cand = []
        for (x, y) in self.grid:
            r = math.hypot(x - dx0, y - dy0)
            if r < min_r or r > reach or not self._los(x, y, dx0, dy0):
                continue
            cand.append((-abs(r - 2.5), x, y))
        cand.sort(reverse=True)
        chosen = []
        for _, x, y in cand:
            if all((x - a) ** 2 + (y - b) ** 2 >= 1.05 for a, b in chosen):
                chosen.append((x, y))
            if len(chosen) >= 12:
                break
        return chosen

    def _next_class(self, counts, total, T):
        nb, nh, ne = counts
        if T < 60:
            return super()._next_class(counts, total, T)
        if ne < self.EXTRACTOR_TARGET and nb >= 8 and T < 4300:
            return EXTRACTOR
        if nb >= 5 and nh < int(0.12 * (nb + nh) + 0.5):
            return HEALER
        return BATTLE

    def _pick_guards(self, battles, op):
        return {}

    def _battle_moves(self, front, moves, cheap):
        n_raid = int(len(front) * self.RAID_FRACTION + 0.5)
        front_sorted = sorted(front, key=lambda b: b.id)
        raiders = front_sorted[:n_raid]
        rest = front_sorted[n_raid:]
        super()._battle_moves(rest, moves, cheap)
        dx0, dy0 = self.dep  # (the enemy deposit after the swap)
        for i, b in enumerate(raiders):
            ang = math.pi * 0.5 + (i - len(raiders) / 2.0) * 0.45
            tx, ty = dx0 + math.cos(ang) * 4.5, dy0 - abs(math.sin(ang)) * 4.5
            moves[b.id] = self._nav(b.x, b.y, tx, ty)

    def _enemy_value(self, e):
        v = super()._enemy_value(e)
        if e.cls == EXTRACTOR:
            v += 6.0
        return v
