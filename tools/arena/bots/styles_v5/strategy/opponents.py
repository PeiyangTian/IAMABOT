"""Sparring styles on top of the v5 machinery (same fire control, different plans)."""
from __future__ import annotations

import math

from .brain import AdvancedStrategy, BATTLE, HEALER, EXTRACTOR


class Hunter(AdvancedStrategy):
    """Every battle bot closes to 6 on its nearest enemy fighter; no formation."""

    def _battle_moves(self, front, moves):
        fighters = [e for e in self.op if e.cls != EXTRACTOR] or self.op
        if not fighters:
            return super()._battle_moves(front, moves)
        for b in front:
            e = min(fighters, key=lambda o: (o.x - b.x) ** 2 + (o.y - b.y) ** 2)
            d = math.hypot(e.x - b.x, e.y - b.y)
            moves[b.id] = self._nav(b.x, b.y, e.x, e.y) if d > 6.0 else (0.0, 0.0)


class Thief(AdvancedStrategy):
    """Gang-style: army and extractors go to the enemy deposit and take its slots."""

    OPENING = "BBBBEBBBEBBBEBHEB"
    RAID_FRACTION = 0.7

    def _setup(self, state):
        super()._setup(state)
        self.dep, self.dep_other = self.dep_other, self.dep
        dx0, dy0 = self.dep
        cand = []
        for (x, y) in self.grid:
            r = math.hypot(x - dx0, y - dy0)
            if r < self.DEP_R + self.HULL_SAFE or r > self.EXTRACT_R + self.DEP_R - 0.35:
                continue
            if self._los(x, y, dx0, dy0):
                cand.append((-abs(r - 2.5), x, y))
        cand.sort(reverse=True)
        chosen = []
        for _, x, y in cand:
            if all((x - a) ** 2 + (y - b) ** 2 >= 1.05 for a, b in chosen):
                chosen.append((x, y))
            if len(chosen) >= 12:
                break
        self.mine_slots = chosen

    def _next_class(self, counts, T):
        nb, nh, ne = counts
        if T < 60:
            return super()._next_class(counts, T)
        if ne < 8 and nb >= 8 and T < 4300:
            return EXTRACTOR
        if nb >= 5 and nh < int(0.12 * (nb + nh) + 0.5):
            return HEALER
        return BATTLE

    def _pick_guards(self, battles, op):
        self.raid = []
        return {}

    def _battle_moves(self, front, moves):
        n_raid = int(len(front) * self.RAID_FRACTION + 0.5)
        front_sorted = sorted(front, key=lambda b: b.id)
        raiders, rest = front_sorted[:n_raid], front_sorted[n_raid:]
        super()._battle_moves(rest, moves)
        dx0, dy0 = self.dep
        for i, b in enumerate(raiders):
            ang = math.pi * 0.5 + (i - len(raiders) / 2.0) * 0.45
            tx, ty = dx0 + math.cos(ang) * 4.5, dy0 - abs(math.sin(ang)) * 4.5
            moves[b.id] = self._nav(b.x, b.y, tx, ty)

    def _enemy_value(self, e):
        return super()._enemy_value(e) + (6.0 if e.cls == EXTRACTOR else 0.0)


class Camper(AdvancedStrategy):
    """Syntax-Terror-v17-style: win the army fight, then park on the enemy spawn so the
    fleet is empty when the endgame starts."""

    def _battle_moves(self, front, moves):
        if self.T > 1500 and self.my_all > 1.5 * self.op_all + 2:
            sx, sy = self.SIZE - 3.0, 3.0  # enemy spawn corner in our frame is (31.75, 0.25)
            sx, sy = 3.0, 28.5
            sx, sy = self.SIZE - 0.25 - 4.0, 0.25 + 4.0
            for i, b in enumerate(sorted(front, key=lambda b: b.id)):
                ang = math.pi * (0.5 + 0.5 * i / max(1, len(front) - 1))
                tx = sx + math.cos(ang) * 3.0
                ty = sy + math.sin(ang) * 3.0
                moves[b.id] = self._nav(b.x, b.y, tx, ty)
            return
        super()._battle_moves(front, moves)


class Greedy(AdvancedStrategy):
    OPENING = "EEEEEEEEBBBBHBBBB"
    EXTRACTOR_TARGET = 8


def _chase(self, bots, moves, D):
    fighters = [e for e in self.op if e.cls != EXTRACTOR] or self.op
    if not fighters:
        return False
    for b in bots:
        e = min(fighters, key=lambda o: (o.x - b.x) ** 2 + (o.y - b.y) ** 2)
        d = math.hypot(e.x - b.x, e.y - b.y)
        moves[b.id] = self._nav(b.x, b.y, e.x, e.y) if d > D else (0.0, 0.0)
    return True


class SyntaxTerror(AdvancedStrategy):
    """Imitation of Syntax Terror v24 (rank 1): ten extractors in the opening, an early
    raid on the enemy deposit, close-range chasing, healers glued to the battle bots."""

    OPENING = "BEEEEEEEEBBBBHHEE"
    RAID_UNTIL = 2200
    RAIDERS = 5

    def _next_class(self, counts, T):
        nb, nh, ne = counts
        if T < 60:
            return super()._next_class(counts, T)
        if ne < 10 and T < 4300 and nb >= 5:
            return EXTRACTOR
        if nh < int(0.25 * (nb + nh) + 0.5):
            return HEALER
        return BATTLE

    def _pick_guards(self, battles, op):
        self.raid = []
        return {}

    def _battle_moves(self, front, moves):
        order = sorted(front, key=lambda b: b.id)
        enemy_ex = [e for e in self.op if e.cls == EXTRACTOR]
        raiders = order[: self.RAIDERS] if (self.T < self.RAID_UNTIL and enemy_ex) else []
        rest = order[len(raiders):]
        dx0, dy0 = self.dep_other
        for i, b in enumerate(raiders):
            ang = math.pi * (0.25 + 0.5 * i / max(1, len(raiders) - 1))
            tx, ty = dx0 + math.cos(ang) * 3.5, dy0 + math.sin(ang) * 3.5
            moves[b.id] = self._nav(b.x, b.y, tx, ty)
        if rest and not _chase(self, rest, moves, 5.0):
            super()._battle_moves(rest, moves)

    def _enemy_value(self, e):
        return super()._enemy_value(e) + (6.0 if e.cls == EXTRACTOR else 0.0)


class Gang(Thief):
    """Imitation of Gang v2: seven extractors first, all mining the ENEMY deposit, then
    an all-battle army that sits in the capture circle; almost no healers."""

    OPENING = "EEEEEEEBBBBBBBBHB"
    RAID_FRACTION = 0.35

    def _next_class(self, counts, T):
        nb, nh, ne = counts
        if T < 60:
            return AdvancedStrategy._next_class(self, counts, T)
        if ne < 7 and T < 4300 and nb >= 6:
            return EXTRACTOR
        return BATTLE


class ZoneHugger(AdvancedStrategy):
    """Imitation of terryduan-chn v7: seven extractors first, then battle bots that pack
    into the capture circle and brawl at close range with healers glued on."""

    OPENING = "EEEEEEEBBBBBBHBBB"

    def _next_class(self, counts, T):
        nb, nh, ne = counts
        if T < 60:
            return super()._next_class(counts, T)
        if ne < 7 and T < 4300 and nb >= 6:
            return EXTRACTOR
        if nh < int(0.1 * (nb + nh) + 0.5):
            return HEALER
        return BATTLE

    def _battle_moves(self, front, moves):
        px, py = self.P
        for i, b in enumerate(sorted(front, key=lambda b: b.id)):
            ang = i * 2.4
            r = 1.2 + 0.9 * ((i * 0.618) % 1.0)
            moves[b.id] = self._nav(b.x, b.y, px + math.cos(ang) * r, py + math.sin(ang) * r)

    def _separate(self, me, moves):
        return


class Noeyedeer(AdvancedStrategy):
    """Imitation of noeyedeer: balanced opening with a third healers, fights around the
    capture circle, keeps moving."""

    OPENING = "EBBEHBHBEBHBBHBBB"
    HEALER_RATIO = 0.33
    EXTRACTOR_TARGET = 4

    def _battle_moves(self, front, moves):
        if not _chase(self, front, moves, 6.0):
            super()._battle_moves(front, moves)
