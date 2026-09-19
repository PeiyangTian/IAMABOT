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


class GangAllIn(Thief):
    """Faithful Gang v2 (match 135): seven extractors mine the ENEMY deposit, and the whole
    army goes there first (9 of 10 battle bots at t=1000), wipes whatever comes to defend,
    then turns on the payload with the press."""

    OPENING = __import__("os").environ.get("GANG_OPENING", "EEEEEEEBBBBBBBBHB")
    RAID_UNTIL = int(__import__("os").environ.get("GANG_UNTIL", "2400"))

    def _next_class(self, counts, T):
        nb, nh, ne = counts
        if T < 60:
            return AdvancedStrategy._next_class(self, counts, T)
        if ne < 7 and T < 4300 and nb >= 6:
            return EXTRACTOR
        if nh < int(0.07 * (nb + nh) + 0.5):
            return HEALER
        return BATTLE

    def _battle_moves(self, front, moves):
        dx0, dy0 = self.dep  # the ENEMY deposit (swapped in Thief._setup)
        defenders = [e for e in self.op_fighters if (e.x - dx0) ** 2 + (e.y - dy0) ** 2 < 12.0 ** 2]
        if self.T < self.RAID_UNTIL or defenders:
            # Hold the enemy deposit; press anyone who comes to defend it.
            for i, b in enumerate(sorted(front, key=lambda b: b.id)):
                near = [e for e in defenders if (e.x - b.x) ** 2 + (e.y - b.y) ** 2 < 9.0 ** 2]
                if near:
                    e = min(near, key=lambda o: (o.x - b.x) ** 2 + (o.y - b.y) ** 2)
                    d = math.hypot(e.x - b.x, e.y - b.y)
                    moves[b.id] = self._nav(b.x, b.y, e.x, e.y) if d > 6.0 else (0.0, 0.0)
                else:
                    ang = math.pi * 0.5 + (i - len(front) / 2.0) * 0.35
                    tx, ty = dx0 + math.cos(ang) * 4.0, dy0 - abs(math.sin(ang)) * 4.0
                    moves[b.id] = self._nav(b.x, b.y, tx, ty)
            return
        AdvancedStrategy._battle_moves(self, front, moves)


class Escort(AdvancedStrategy):
    """Faithful JaniceKeepTalking v1: fixed opening BBBHBHBEEBHBBHBBB, a third healers glued
    to the battle bots, and the whole army escorting the payload in a spread ring
    (centroid 1-5 tiles from it, ~1.5-2.5 tiles between bots), fighting whatever comes
    close while pushing all game."""

    OPENING = "BBBHBHBEEBHBBHBBB"
    EXTRACTOR_TARGET = 3
    HEALER_RATIO = 0.33
    RING = float(__import__("os").environ.get("ESCORT_RING", "2.6"))
    GAP = float(__import__("os").environ.get("ESCORT_GAP", "1.7"))

    def _escort_slots(self, n):
        px, py = self.P
        key = (round(px * 2), round(py * 2), n)
        if getattr(self, "_esc_key", None) == key:
            return self._esc_slots
        ux, uy = self.u
        cands = []
        for (x, y) in self.grid:
            dx, dy = x - px, y - py
            r = math.hypot(dx, dy)
            if r < 1.45 or r > 5.5:
                continue
            side = dx * ux + dy * uy
            cands.append((-abs(r - self.RING) - 0.15 * max(0.0, -side), x, y))
        cands.sort(reverse=True)
        chosen = []
        g2 = self.GAP ** 2
        for s, x, y in cands:
            if len(chosen) >= n:
                break
            if all((x - a) ** 2 + (y - b) ** 2 >= g2 for a, b in chosen) and self._los(x, y, px, py):
                chosen.append((x, y))
        while len(chosen) < n:
            chosen.append((px - ux * 3.0, py - uy * 3.0))
        self._esc_key, self._esc_slots = key, chosen
        return chosen

    def _battle_moves(self, front, moves):
        if not front:
            return
        slots = self._escort_slots(len(front))
        pairs = sorted(
            ((b.x - sx) ** 2 + (b.y - sy) ** 2, b.id, k)
            for b in front
            for k, (sx, sy) in enumerate(slots)
        )
        tb, ts, owner = set(), set(), {}
        for d, bid, k in pairs:
            if bid in tb or k in ts:
                continue
            tb.add(bid)
            ts.add(k)
            owner[bid] = k
        for b in front:
            sx, sy = slots[owner.get(b.id, 0)]
            moves[b.id] = self._nav(b.x, b.y, sx, sy)

    def _pick_guards(self, battles, op):
        self.raid = []
        return {}


class PayloadCover(Escort):
    """JaniceKeepTalking as measured in match 237 (beat v7): hold a spread line ~4.5 tiles
    BEHIND the payload (the payload between us and the enemy, used as a shield), shoot
    whoever stands in its shadow, and move into the capture circle once the enemy near the
    payload is weak or gone."""

    COVER = float(__import__("os").environ.get("COVER_R", "4.5"))

    def _cover_slots(self, n):
        px, py = self.P
        ux, uy = self.u  # toward the enemy
        key = (round(px * 2), round(py * 2), n, round(math.atan2(uy, ux) / 0.4))
        if getattr(self, "_cov_key", None) == key:
            return self._cov_slots
        cands = []
        for (x, y) in self.grid:
            dx, dy = x - px, y - py
            r = math.hypot(dx, dy)
            if r < 1.6 or r > 7.0:
                continue
            side = dx * ux + dy * uy  # < 0: behind the payload
            lat = abs(-dx * uy + dy * ux)
            cands.append((-abs(r - self.COVER) - 1.2 * max(0.0, side + 1.0) - 0.15 * lat, x, y))
        cands.sort(reverse=True)
        chosen = []
        for s, x, y in cands:
            if len(chosen) >= n:
                break
            if all((x - a) ** 2 + (y - b) ** 2 >= self.GAP ** 2 for a, b in chosen) and self._los(x, y, px, py):
                chosen.append((x, y))
        while len(chosen) < n:
            chosen.append((px - ux * self.COVER, py - uy * self.COVER))
        self._cov_key, self._cov_slots = key, chosen
        return chosen

    def _battle_moves(self, front, moves):
        if not front:
            return
        px, py = self.P
        threat = [e for e in self.op_fighters if (e.x - px) ** 2 + (e.y - py) ** 2 < 11.0 ** 2]
        tpow = sum(self._power(e) for e in threat)
        if not threat or self.my_near >= 1.4 * tpow + 1.0:
            return Escort._battle_moves(self, front, moves)  # take the circle
        slots = self._cover_slots(len(front))
        pairs = sorted(
            ((b.x - sx) ** 2 + (b.y - sy) ** 2, b.id, k)
            for b in front
            for k, (sx, sy) in enumerate(slots)
        )
        tb, ts, owner = set(), set(), {}
        for d, bid, k in pairs:
            if bid in tb or k in ts:
                continue
            tb.add(bid)
            ts.add(k)
            owner[bid] = k
        for b in front:
            sx, sy = slots[owner.get(b.id, 0)]
            moves[b.id] = self._nav(b.x, b.y, sx, sy)


class DibsfaConvert(AdvancedStrategy):
    """DIBSFA v8 as measured in match 376 (beat v8): seven extractors first, a fat bank,
    and right before production stops it self-destructs its extractors and rushes battle
    bots into the freed slots (20 -> 28 battle bots for the endgame fight)."""

    OPENING = "EEEEEEEBBBBBBBBHB"
    EXTRACTOR_TARGET = 8
    HEALER_RATIO = 0.15

    def _tick(self, state):
        act = super()._tick(state)
        T = state.tick
        if not (self.END_T - 30 <= T < self.END_T - 1):
            return act
        act.fabricator_next = BATTLE
        cost = self.conf.fabricator.rush_cost
        rushing = bool(act.rush_order)
        if state.fabricator_me.tokens - (cost if rushing else 0) < cost:
            return act
        if 32 - len(self.me) - (1 if rushing else 0) > 0:
            return act
        ex = [u for u in self.me if u.cls == EXTRACTOR]
        if ex:
            act.bots[ex[0].id].self_destruct = True
        return act


class DibsfaTurtle(DibsfaConvert):
    """DIBSFA v8 as it actually played match 376: 19-20 of its battle bots guard its own
    deposit (radius ~3-7) for the first 6500 ticks, pressing only what comes close; the
    payload is left almost undefended.  At the endgame it converts its extractors into
    battle bots and the whole army goes for the payload."""

    HOLD_UNTIL = int(__import__("os").environ.get("TURTLE_UNTIL", "6500"))

    def _battle_moves(self, front, moves):
        if self.T >= self.HOLD_UNTIL:
            return super()._battle_moves(front, moves)
        dx0, dy0 = self.dep
        for i, b in enumerate(sorted(front, key=lambda b: b.id)):
            near = [e for e in self.op_fighters if (e.x - b.x) ** 2 + (e.y - b.y) ** 2 < 8.0 ** 2]
            if near:
                e = min(near, key=lambda o: (o.x - b.x) ** 2 + (o.y - b.y) ** 2)
                d = math.hypot(e.x - b.x, e.y - b.y)
                moves[b.id] = self._nav(b.x, b.y, e.x, e.y) if d > 6.0 else (0.0, 0.0)
                continue
            ang = i * 2.39996
            r = 3.0 + (i % 3) * 1.6
            moves[b.id] = self._nav(b.x, b.y, dx0 + math.cos(ang) * r, dy0 + math.sin(ang) * r)

    def _pick_guards(self, battles, op):
        self.raid = []
        return {}
