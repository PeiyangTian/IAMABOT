"""Stateful tournament strategy used by IAMABOT.

The controller combines four ideas that are especially valuable in this ruleset:

* front-load the economy and spend the starting token bank immediately;
* keep a splash-safe formation around the payload instead of one giant stack;
* sustain the front line with healers and reserve a small home guard;
* coordinate blaster targets so invulnerability does not waste an entire volley.

All coordinates are in the engine's mirrored frame, so this code is side agnostic.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math

from . import *


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _centroid(bots: list[BotState], fallback: Vec2) -> Vec2:
    if not bots:
        return fallback
    return Vec2(
        sum(bot.pos.x for bot in bots) / len(bots),
        sum(bot.pos.y for bot in bots) / len(bots),
    )


def _counts(bots: list[BotState]) -> Counter:
    return Counter(bot.class_ for bot in bots)


def _future_pos(bot: BotState, ticks: float = 1.0) -> Vec2:
    return bot.pos + bot.vel * ticks


def _disc_blocks_segment(a: Vec2, b: Vec2, centre: Vec2, radius: float) -> bool:
    # A collider containing the shooter is not an intervening obstacle.
    if a.dist(centre) <= radius + 0.02:
        return False
    return point_seg_dist(centre, a, b) <= radius


class AdvancedStrategy:
    """Economy, formation, healing, defence and focus-fire controller."""

    TARGET_EXTRACTORS = 8
    TARGET_HEALERS = 6
    CHEAP_BUDGET = 100_000

    def __init__(self) -> None:
        self.mine_slots: list[Vec2] = []
        self.previous_tick = -1
        self.skipped_ticks = 0

    def __call__(self, state: GameState) -> FleetAction:
        conf = get_config()
        if not self.mine_slots:
            self.mine_slots = self._make_mine_slots(state, conf)

        if self.previous_tick >= 0 and state.tick > self.previous_tick + 1:
            self.skipped_ticks += state.tick - self.previous_tick - 1
        self.previous_tick = state.tick

        if get_budget().remaining < self.CHEAP_BUDGET:
            return self._cheap_actions(state, conf)
        return self._coordinated_actions(state, conf)

    # ------------------------------------------------------------------ production

    def _production(self, state: GameState, conf: GameConfig, action: FleetAction) -> None:
        allies = list(state.fleet_me)
        count = _counts(allies)
        total = len(allies)

        # Economy first, but retain enough early guns to survive a rush.  Healer demand
        # scales up with the fleet so the opening does not consist of passive units.
        if count[BotClass.Battle] < 2:
            next_class = BotClass.Battle
        elif count[BotClass.Extractor] < self.TARGET_EXTRACTORS:
            next_class = BotClass.Extractor
        elif count[BotClass.Battle] < 6:
            next_class = BotClass.Battle
        else:
            desired_healers = 2 if total < 18 else 4 if total < 25 else self.TARGET_HEALERS
            desired_battles = max(6, total - self.TARGET_EXTRACTORS - desired_healers + 1)
            if count[BotClass.Healer] < desired_healers:
                next_class = BotClass.Healer
            elif count[BotClass.Battle] < desired_battles:
                next_class = BotClass.Battle
            elif count[BotClass.Extractor] < self.TARGET_EXTRACTORS:
                next_class = BotClass.Extractor
            elif count[BotClass.Healer] < self.TARGET_HEALERS:
                next_class = BotClass.Healer
            else:
                next_class = BotClass.Battle

        action.fabricator_next = int(next_class)
        in_endgame = state.tick >= conf.max_ticks - conf.endgame_ticks
        action.rush_order = (
            not in_endgame
            and not state.fleet_me.is_full()
            and state.fabricator_me.tokens >= conf.fabricator.rush_cost
        )

    # --------------------------------------------------------------- geometry/move

    def _make_mine_slots(self, state: GameState, conf: GameConfig) -> list[Vec2]:
        centre = state.deposit_me.pos
        radius = conf.deposit.radius + conf.bot.radius + 0.18
        slots: list[Vec2] = []
        # Begin on the safe/home-facing half of the ring, then fill the other half.
        for degrees in (225, 270, 180, 315, 135, 0, 90, 45):
            candidate = centre + Vec2.from_angle_deg(degrees) * radius
            if point_free(candidate) and line_of_sight(candidate, centre):
                slots.append(candidate)
        if not slots:
            slots.append(centre + Vec2(0.0, radius))
        seed = slots[:]
        while len(slots) < self.TARGET_EXTRACTORS:
            slots.append(seed[len(slots) % len(seed)])
        return slots

    def _payload_frame(self, state: GameState) -> tuple[Vec2, Vec2, Vec2]:
        centre = state.payload_pos()
        ahead = payload_pos(_clamp(state.capture + 0.015, -1.0, 1.0))
        tangent = (ahead - centre).normalize_or_zero()
        if tangent.norm_sq() < 0.1:
            tangent = Vec2(1.0, 1.0).normalize_or_zero()
        normal = Vec2(-tangent.y, tangent.x)
        return centre, tangent, normal

    def _legal_payload_point(
        self, centre: Vec2, tangent: Vec2, normal: Vec2, forward: float, lateral: float
    ) -> Vec2:
        candidate = centre + tangent * forward + normal * lateral
        if point_free(candidate):
            return candidate
        # Pull blocked formation points inward, then try the opposite side.
        candidate = centre + tangent * (forward * 0.65) + normal * (lateral * 0.65)
        if point_free(candidate):
            return candidate
        candidate = centre - tangent * 0.35 - normal * (1.05 if lateral >= 0 else -1.05)
        return candidate

    def _payload_positions(self, state: GameState, count: int) -> list[Vec2]:
        centre, tangent, normal = self._payload_frame(state)
        specs: list[tuple[float, float]] = []

        # Six inner points hold the objective; twelve outer points give firing arcs while
        # remaining inside the 2.5 capture radius.  Adjacent points exceed splash diameter.
        for i in range(6):
            angle = math.radians(i * 60 + 30)
            specs.append((math.cos(angle) * 1.20, math.sin(angle) * 1.20))
        for i in range(12):
            angle = math.radians(i * 30 + 15)
            specs.append((math.cos(angle) * 2.02, math.sin(angle) * 2.02))

        return [
            self._legal_payload_point(centre, tangent, normal, forward, lateral)
            for forward, lateral in specs[: max(1, min(count, len(specs)))]
        ]

    def _miner_payload_positions(self, state: GameState, count: int) -> list[Vec2]:
        centre, tangent, normal = self._payload_frame(state)
        positions: list[Vec2] = []
        # A third, staggered ring avoids reusing the Battle formation coordinates.
        for i in range(max(1, count)):
            angle = math.radians(i * (360.0 / max(1, count)) + 7.5)
            positions.append(
                self._legal_payload_point(
                    centre,
                    tangent,
                    normal,
                    math.cos(angle) * 2.43,
                    math.sin(angle) * 2.43,
                )
            )
        return positions

    def _spread_move(
        self,
        bot: BotState,
        target: Vec2,
        allies: list[BotState],
        conf: GameConfig,
    ) -> MoveAction:
        desired = navigate_to(bot.pos, target)
        repulsion = Vec2()
        comfort = conf.bot.radius * 2.35
        for ally in allies:
            if ally.id == bot.id:
                continue
            delta = bot.pos - ally.pos
            distance = delta.norm()
            if 0.001 < distance < comfort:
                repulsion += delta.normalize_or_zero() * ((comfort - distance) / comfort)
            elif distance <= 0.001:
                # Stable id-based split breaks exact stacks deterministically.
                angle = (bot.id * 137.5 + ally.id * 31.0) % 360.0
                repulsion += Vec2.from_angle_deg(angle)
        combined = desired + repulsion * 1.35
        return move_bot(combined.normalize_or_zero())

    # --------------------------------------------------------------- main controller

    def _coordinated_actions(self, state: GameState, conf: GameConfig) -> FleetAction:
        action = FleetAction.new()
        self._production(state, conf, action)

        allies = list(state.fleet_me)
        enemies = list(state.fleet_other)
        battles = [bot for bot in allies if bot.class_ == BotClass.Battle]
        healers = [bot for bot in allies if bot.class_ == BotClass.Healer]
        extractors = [bot for bot in allies if bot.class_ == BotClass.Extractor]
        payload = state.payload_pos()
        enemy_centre = _centroid(enemies, state.deposit_other.pos)
        endgame = state.tick >= conf.max_ticks - conf.endgame_ticks

        # Extractors retain stable IDs/slots so the sticky deposit allocation is not
        # churned.  In the endgame tokens no longer build anything, so miners become
        # capture bodies instead of guarding a useless resource.
        payload_for_miners = self._miner_payload_positions(state, max(1, len(extractors)))
        for index, bot in enumerate(extractors):
            bot_action = action.bots[bot.id]
            if endgame:
                target = payload_for_miners[index % len(payload_for_miners)]
                bot_action.move_action = self._spread_move(bot, target, allies, conf)
                bot_action.turn_action = turn_towards(enemy_centre)
                bot_action.special_action = SpecialAction.Extractor(mine=False)
            else:
                target = self.mine_slots[index % len(self.mine_slots)]
                bot_action.move_action = self._spread_move(bot, target, allies, conf)
                bot_action.turn_action = turn_towards(state.deposit_me.pos)
                in_range = bot.pos.dist(state.deposit_me.pos) <= conf.bot.base_extract_range
                can_see = line_of_sight(bot.pos, state.deposit_me.pos)
                bot_action.special_action = SpecialAction.Extractor(mine=in_range and can_see)

        self._control_healers(state, conf, action, allies, healers, payload, enemy_centre)
        fire_plans = self._control_battles(
            state, conf, action, allies, enemies, battles, payload, enemy_centre
        )
        self._assign_coordinated_fire(state, conf, action, fire_plans, enemies)
        return action

    # ---------------------------------------------------------------------- healers

    def _control_healers(
        self,
        state: GameState,
        conf: GameConfig,
        action: FleetAction,
        allies: list[BotState],
        healers: list[BotState],
        payload: Vec2,
        enemy_centre: Vec2,
    ) -> None:
        assigned: defaultdict[int, int] = defaultdict(int)
        candidates = [bot for bot in allies if bot.health < conf.bot.health - 0.02]

        for healer_index, healer in enumerate(healers):
            # Do not waste all heal beams on the same tank.  Battle bots and objective
            # holders are more valuable than miners with the same missing health.
            best = None
            best_score = -1e9
            for ally in candidates:
                if ally.id == healer.id or assigned[ally.id] >= int(conf.bot.heal_stack_cap):
                    continue
                missing = conf.bot.health - ally.health
                score = missing * 55.0
                score += 105.0 if ally.class_ == BotClass.Battle else 25.0
                score += max(0.0, 90.0 - ally.pos.dist(payload) * 35.0)
                score -= healer.pos.dist(ally.pos) * 9.0
                if ally.invulnerable_until_tick > state.tick:
                    score += 25.0
                if score > best_score:
                    best_score, best = score, ally

            bot_action = action.bots[healer.id]
            if best is None:
                # Idle healers form a rear screen one heal-range behind the payload.
                away = (payload - enemy_centre).normalize_or_zero()
                flank = Vec2(-away.y, away.x) * (((healer_index % 3) - 1) * 0.72)
                target = payload + away * (1.25 + (healer_index // 3) * 0.58) + flank
                bot_action.move_action = self._spread_move(healer, target, allies, conf)
                bot_action.turn_action = turn_towards(payload)
                bot_action.special_action = SpecialAction.Healer(fire=False, target=0)
                continue

            heal_slot = assigned[best.id]
            assigned[best.id] += 1
            away = (best.pos - enemy_centre).normalize_or_zero()
            if away.norm_sq() < 0.1:
                away = (state.deposit_me.pos - best.pos).normalize_or_zero()
            side = Vec2(-away.y, away.x) * ((heal_slot - 1) * 0.48)
            follow = best.pos + away * min(conf.bot.base_heal_range * 0.62, 1.65) + side
            bot_action.move_action = self._spread_move(healer, follow, allies, conf)

            predicted = _future_pos(best)
            bot_action.turn_action = turn_towards(predicted)
            desired_angle = (predicted - healer.pos).angle_deg()
            turn = _clamp(
                diff_degrees(desired_angle, healer.angle),
                -conf.bot.turn_speed,
                conf.bot.turn_speed,
            )
            after_turn = healer.angle + turn
            in_arc = abs(diff_degrees(desired_angle, after_turn)) <= conf.bot.base_heal_arc_deg / 2
            can_heal = (
                healer.pos.dist(predicted) <= conf.bot.base_heal_range
                and line_of_sight(healer.pos, predicted)
                and in_arc
            )
            bot_action.special_action = SpecialAction.Healer(fire=can_heal, target=best.id)

    # ---------------------------------------------------------------------- battles

    def _control_battles(
        self,
        state: GameState,
        conf: GameConfig,
        action: FleetAction,
        allies: list[BotState],
        enemies: list[BotState],
        battles: list[BotState],
        payload: Vec2,
        enemy_centre: Vec2,
    ) -> list[tuple[BotState, Vec2]]:
        fire_plans: list[tuple[BotState, Vec2]] = []
        if not battles:
            return fire_plans

        home_threats = sorted(
            (enemy for enemy in enemies if enemy.pos.dist(state.deposit_me.pos) < 11.5),
            key=lambda enemy: enemy.pos.dist_sq(state.deposit_me.pos),
        )
        defender_ids: set[int] = set()
        if home_threats:
            defenders = sorted(battles, key=lambda bot: bot.pos.dist_sq(state.deposit_me.pos))[:2]
            defender_ids = {bot.id for bot in defenders}

        front = [bot for bot in battles if bot.id not in defender_ids]
        positions = self._payload_positions(state, max(1, len(front)))
        support_targets = [enemy for enemy in enemies if enemy.class_ != BotClass.Battle]
        support_targets.sort(key=lambda enemy: enemy.pos.dist_sq(payload))

        for index, bot in enumerate(battles):
            if bot.id in defender_ids:
                target_enemy = home_threats[bot.id % len(home_threats)]
                guard_dir = (target_enemy.pos - state.deposit_me.pos).normalize_or_zero()
                destination = state.deposit_me.pos + guard_dir * 3.0
            else:
                front_index = front.index(bot)
                destination = positions[front_index % len(positions)]
                # A small raider group punishes undefended healers/miners, but only when
                # our objective force is already substantial and the payload is not lost.
                if (
                    len(battles) >= 13
                    and support_targets
                    and front_index >= max(9, len(front) - 4)
                    and state.capture > -0.45
                ):
                    quarry = support_targets[front_index % len(support_targets)]
                    if bot.pos.dist(quarry.pos) < 13.0:
                        destination = quarry.pos

            action.bots[bot.id].move_action = self._spread_move(bot, destination, allies, conf)

            target = self._best_target(state, conf, bot, enemies, payload)
            aim = target.pos if target is not None else enemy_centre
            action.bots[bot.id].turn_action = turn_towards(_future_pos(target) if target else aim)
            action.bots[bot.id].special_action = SpecialAction.Battle(fire=False)
            fire_plans.append((bot, destination))

        return fire_plans

    def _best_target(
        self,
        state: GameState,
        conf: GameConfig,
        shooter: BotState,
        enemies: list[BotState],
        payload: Vec2,
        reserved: set[int] | None = None,
    ) -> BotState | None:
        best = None
        best_score = -1e9
        for enemy in enemies:
            if enemy.invulnerable_until_tick > state.tick:
                continue
            if reserved is not None and enemy.id in reserved:
                continue
            predicted = _future_pos(enemy)
            distance = shooter.pos.dist(predicted)
            if distance > conf.bot.blaster_range + conf.bot.speed:
                continue
            if reserved is not None and not self._shot_clear(
                state, conf, shooter.pos, predicted
            ):
                continue
            score = 330.0 if enemy.pos.dist(payload) <= conf.payload.capture_radius else 0.0
            score += 285.0 if enemy.class_ == BotClass.Healer else 145.0
            score += 105.0 if enemy.class_ == BotClass.Extractor else 0.0
            score += (conf.bot.health - enemy.health) * 32.0
            if enemy.health <= conf.bot.blaster_damage + 0.05:
                score += 390.0
            nearby = sum(
                1
                for other in enemies
                if other.id != enemy.id
                and other.pos.dist(enemy.pos) <= conf.bot.base_blaster_splash_radius * 2.2
            )
            score += nearby * 75.0
            score -= distance * 8.0
            if score > best_score:
                best_score, best = score, enemy
        return best

    def _shot_clear(
        self, state: GameState, conf: GameConfig, origin: Vec2, target: Vec2
    ) -> bool:
        if not line_of_sight(origin, target):
            return False
        blockers = (
            (state.payload_pos(), conf.payload.radius),
            (state.deposit_me.pos, conf.deposit.radius),
            (state.deposit_other.pos, conf.deposit.radius),
        )
        for centre, radius in blockers:
            if target.dist(centre) > radius + 0.05 and _disc_blocks_segment(
                origin, target, centre, radius + 0.02
            ):
                return False
        return True

    def _assign_coordinated_fire(
        self,
        state: GameState,
        conf: GameConfig,
        action: FleetAction,
        fire_plans: list[tuple[BotState, Vec2]],
        enemies: list[BotState],
    ) -> None:
        reserved: set[int] = set()
        payload = state.payload_pos()

        # Ready shooters with the best immediate opportunities choose first.
        shooters = [bot for bot, _ in fire_plans if bot.next_fire_tick <= state.tick]
        shooters.sort(key=lambda bot: min((bot.pos.dist_sq(e.pos) for e in enemies), default=1e9))
        for shooter in shooters:
            target = self._best_target(state, conf, shooter, enemies, payload, reserved)
            if target is None:
                continue
            predicted = _future_pos(target)
            if shooter.pos.dist(predicted) > conf.bot.blaster_range:
                continue
            if not self._shot_clear(state, conf, shooter.pos, predicted):
                continue

            # The reserved-aware choice may differ from the generic target selected in
            # the movement pass; update the actual turn order to match the shot plan.
            action.bots[shooter.id].turn_action = turn_towards(predicted)

            desired = (predicted - shooter.pos).angle_deg()
            rotation = _clamp(
                diff_degrees(desired, shooter.angle),
                -conf.bot.turn_speed,
                conf.bot.turn_speed,
            )
            after_turn = shooter.angle + rotation
            # The target hull subtends this angle; a small minimum absorbs float noise.
            distance = max(0.01, shooter.pos.dist(predicted))
            tolerance = max(1.0, math.degrees(math.asin(min(0.99, conf.bot.radius / distance))))
            if abs(diff_degrees(desired, after_turn)) <= tolerance:
                action.bots[shooter.id].special_action = SpecialAction.Battle(fire=True)
                reserved.add(target.id)

    # -------------------------------------------------------------- budget fallback

    def _cheap_actions(self, state: GameState, conf: GameConfig) -> FleetAction:
        action = FleetAction.new()
        self._production(state, conf, action)
        allies = list(state.fleet_me)
        enemies = list(state.fleet_other)
        payload = state.payload_pos()

        for bot in allies:
            bot_action = action.bots[bot.id]
            if bot.class_ == BotClass.Extractor:
                target = self.mine_slots[bot.id % len(self.mine_slots)]
                bot_action.move_action = move_bot(navigate_to(bot.pos, target))
                bot_action.turn_action = turn_towards(state.deposit_me.pos)
                bot_action.special_action = SpecialAction.Extractor(
                    mine=bot.pos.dist(state.deposit_me.pos) <= conf.bot.base_extract_range
                )
            elif bot.class_ == BotClass.Healer:
                wounded = min(
                    (ally for ally in allies if ally.id != bot.id),
                    key=lambda ally: ally.health,
                    default=None,
                )
                target = wounded.pos if wounded else payload
                bot_action.move_action = move_bot(navigate_to(bot.pos, target))
                bot_action.turn_action = turn_towards(target)
                can_heal = wounded is not None and bot.pos.dist(target) <= conf.bot.base_heal_range
                bot_action.special_action = SpecialAction.Healer(
                    fire=can_heal, target=wounded.id if wounded else 0
                )
            else:
                enemy = min(enemies, key=lambda other: bot.pos.dist_sq(other.pos), default=None)
                target = enemy.pos if enemy else payload
                bot_action.move_action = move_bot(navigate_to(bot.pos, payload))
                bot_action.turn_action = turn_towards(target)
                bot_action.special_action = SpecialAction.Battle(fire=False)
        return action


class SuperStrategy:
    """Economy, formation, healing, defence and focus-fire controller.

    Upgrades over AdvancedStrategy:
    * Predictive lead-shot targeting based on enemy velocity and distance.
    * Adaptive build queue that reacts to enemy healer count.
    * HP-aware retreat: wounded battle bots pull back to the nearest healer.
    * Capture-urgency mode: when the payload is nearly lost, all units commit.
    * Proximity-based greedy miner slot assignment (replaces round-robin).
    * Self-destruct trap: a dying bot surrounded by 3+ enemies detonates.
    """

    TARGET_EXTRACTORS = 8
    TARGET_HEALERS = 6
    CHEAP_BUDGET = 100_000

    RETREAT_HP_FRAC = 0.30
    DESTRUCT_HP_FRAC = 0.10
    LOSING_CAPTURE = -0.65

    def __init__(self) -> None:
        self.mine_slots: list[Vec2] = []
        self.previous_tick = -1

    def __call__(self, state: GameState) -> FleetAction:
        conf = get_config()
        if not self.mine_slots:
            self.mine_slots = self._make_mine_slots(state, conf)
        if self.previous_tick >= 0 and state.tick > self.previous_tick + 1:
            pass
        self.previous_tick = state.tick
        if get_budget().remaining < self.CHEAP_BUDGET:
            return self._cheap_actions(state, conf)
        return self._coordinated_actions(state, conf)

    # ------------------------------------------------------------------ production

    def _production(self, state: GameState, conf: GameConfig, action: FleetAction) -> None:
        allies = list(state.fleet_me)
        enemies = list(state.fleet_other)
        count = _counts(allies)
        enemy_count = _counts(enemies)
        total = len(allies)

        # Need extra DPS to punch through enemy sustain when they field many healers.
        battle_bonus = max(0, enemy_count[BotClass.Healer] - 2) * 2

        if count[BotClass.Battle] < 2:
            next_class = BotClass.Battle
        elif count[BotClass.Extractor] < self.TARGET_EXTRACTORS and state.capture > -0.5:
            next_class = BotClass.Extractor
        elif count[BotClass.Battle] < 6 + battle_bonus:
            next_class = BotClass.Battle
        else:
            desired_healers = 2 if total < 18 else 4 if total < 25 else self.TARGET_HEALERS
            desired_battles = max(6 + battle_bonus, total - self.TARGET_EXTRACTORS - desired_healers + 1)
            if count[BotClass.Healer] < desired_healers:
                next_class = BotClass.Healer
            elif count[BotClass.Battle] < desired_battles:
                next_class = BotClass.Battle
            elif count[BotClass.Extractor] < self.TARGET_EXTRACTORS:
                next_class = BotClass.Extractor
            elif count[BotClass.Healer] < self.TARGET_HEALERS:
                next_class = BotClass.Healer
            else:
                next_class = BotClass.Battle

        # Skip miners when we are on the verge of losing the payload.
        if state.capture < self.LOSING_CAPTURE and next_class == BotClass.Extractor:
            next_class = BotClass.Battle

        action.fabricator_next = int(next_class)
        in_endgame = state.tick >= conf.max_ticks - conf.endgame_ticks
        action.rush_order = (
            not in_endgame
            and not state.fleet_me.is_full()
            and state.fabricator_me.tokens >= conf.fabricator.rush_cost
        )

    # --------------------------------------------------------------- geometry/move

    def _make_mine_slots(self, state: GameState, conf: GameConfig) -> list[Vec2]:
        centre = state.deposit_me.pos
        radius = conf.deposit.radius + conf.bot.radius + 0.18
        slots: list[Vec2] = []
        for degrees in (225, 270, 180, 315, 135, 0, 90, 45):
            candidate = centre + Vec2.from_angle_deg(degrees) * radius
            if point_free(candidate) and line_of_sight(candidate, centre):
                slots.append(candidate)
        if not slots:
            slots.append(centre + Vec2(0.0, radius))
        seed = slots[:]
        while len(slots) < self.TARGET_EXTRACTORS:
            slots.append(seed[len(slots) % len(seed)])
        return slots

    def _assign_miner_slots(self, extractors: list[BotState]) -> dict[int, Vec2]:
        """Greedy proximity assignment: closest-unoccupied-slot-first."""
        assignment: dict[int, Vec2] = {}
        available = list(range(len(self.mine_slots)))
        sorted_miners = sorted(
            extractors,
            key=lambda b: min(
                (b.pos.dist_sq(self.mine_slots[i]) for i in available), default=1e9
            ),
        )
        for bot in sorted_miners:
            if not available:
                break
            best_idx = min(available, key=lambda i: bot.pos.dist_sq(self.mine_slots[i]))
            assignment[bot.id] = self.mine_slots[best_idx]
            available.remove(best_idx)
        for bot in extractors:
            if bot.id not in assignment:
                assignment[bot.id] = min(self.mine_slots, key=lambda s: bot.pos.dist_sq(s))
        return assignment

    def _lead_pos(self, shooter: BotState, target: BotState, conf: GameConfig) -> Vec2:
        """Predict target position using velocity-based lead, scaled by distance."""
        dist = shooter.pos.dist(target.pos)
        lead_ticks = min(5.0, dist / max(0.01, conf.bot.blaster_range) * 6.0)
        return target.pos + target.vel * lead_ticks

    def _payload_frame(self, state: GameState) -> tuple[Vec2, Vec2, Vec2]:
        centre = state.payload_pos()
        ahead = payload_pos(_clamp(state.capture + 0.015, -1.0, 1.0))
        tangent = (ahead - centre).normalize_or_zero()
        if tangent.norm_sq() < 0.1:
            tangent = Vec2(1.0, 1.0).normalize_or_zero()
        normal = Vec2(-tangent.y, tangent.x)
        return centre, tangent, normal

    def _legal_payload_point(
        self, centre: Vec2, tangent: Vec2, normal: Vec2, forward: float, lateral: float
    ) -> Vec2:
        candidate = centre + tangent * forward + normal * lateral
        if point_free(candidate):
            return candidate
        candidate = centre + tangent * (forward * 0.65) + normal * (lateral * 0.65)
        if point_free(candidate):
            return candidate
        return centre - tangent * 0.35 - normal * (1.05 if lateral >= 0 else -1.05)

    def _payload_positions(self, state: GameState, count: int) -> list[Vec2]:
        centre, tangent, normal = self._payload_frame(state)
        specs: list[tuple[float, float]] = []
        for i in range(6):
            angle = math.radians(i * 60 + 30)
            specs.append((math.cos(angle) * 1.20, math.sin(angle) * 1.20))
        for i in range(12):
            angle = math.radians(i * 30 + 15)
            specs.append((math.cos(angle) * 2.02, math.sin(angle) * 2.02))
        return [
            self._legal_payload_point(centre, tangent, normal, fw, lat)
            for fw, lat in specs[: max(1, min(count, len(specs)))]
        ]

    def _miner_payload_positions(self, state: GameState, count: int) -> list[Vec2]:
        centre, tangent, normal = self._payload_frame(state)
        positions: list[Vec2] = []
        for i in range(max(1, count)):
            angle = math.radians(i * (360.0 / max(1, count)) + 7.5)
            positions.append(
                self._legal_payload_point(
                    centre, tangent, normal,
                    math.cos(angle) * 2.43, math.sin(angle) * 2.43,
                )
            )
        return positions

    def _spread_move(
        self, bot: BotState, target: Vec2, allies: list[BotState], conf: GameConfig
    ) -> MoveAction:
        desired = navigate_to(bot.pos, target)
        repulsion = Vec2()
        comfort = conf.bot.radius * 2.35
        for ally in allies:
            if ally.id == bot.id:
                continue
            delta = bot.pos - ally.pos
            distance = delta.norm()
            if 0.001 < distance < comfort:
                repulsion += delta.normalize_or_zero() * ((comfort - distance) / comfort)
            elif distance <= 0.001:
                angle = (bot.id * 137.5 + ally.id * 31.0) % 360.0
                repulsion += Vec2.from_angle_deg(angle)
        combined = desired + repulsion * 1.35
        return move_bot(combined.normalize_or_zero())

    def _shot_clear(
        self, state: GameState, conf: GameConfig, origin: Vec2, target: Vec2
    ) -> bool:
        if not line_of_sight(origin, target):
            return False
        blockers = (
            (state.payload_pos(), conf.payload.radius),
            (state.deposit_me.pos, conf.deposit.radius),
            (state.deposit_other.pos, conf.deposit.radius),
        )
        for centre, radius in blockers:
            if target.dist(centre) > radius + 0.05 and _disc_blocks_segment(
                origin, target, centre, radius + 0.02
            ):
                return False
        return True

    def _best_target(
        self,
        state: GameState,
        conf: GameConfig,
        shooter: BotState,
        enemies: list[BotState],
        payload: Vec2,
        reserved: set[int] | None = None,
    ) -> BotState | None:
        best = None
        best_score = -1e9
        for enemy in enemies:
            if enemy.invulnerable_until_tick > state.tick:
                continue
            if reserved is not None and enemy.id in reserved:
                continue
            predicted = self._lead_pos(shooter, enemy, conf)
            distance = shooter.pos.dist(predicted)
            if distance > conf.bot.blaster_range + conf.bot.speed:
                continue
            if reserved is not None and not self._shot_clear(state, conf, shooter.pos, predicted):
                continue
            score = 330.0 if enemy.pos.dist(payload) <= conf.payload.capture_radius else 0.0
            score += 285.0 if enemy.class_ == BotClass.Healer else 145.0
            score += 105.0 if enemy.class_ == BotClass.Extractor else 0.0
            score += (conf.bot.health - enemy.health) * 32.0
            if enemy.health <= conf.bot.blaster_damage + 0.05:
                score += 390.0
            nearby = sum(
                1 for other in enemies
                if other.id != enemy.id
                and other.pos.dist(enemy.pos) <= conf.bot.base_blaster_splash_radius * 2.2
            )
            score += nearby * 75.0
            score -= distance * 8.0
            if score > best_score:
                best_score, best = score, enemy
        return best

    # --------------------------------------------------------------- main controller

    def _coordinated_actions(self, state: GameState, conf: GameConfig) -> FleetAction:
        action = FleetAction.new()
        self._production(state, conf, action)

        allies = list(state.fleet_me)
        enemies = list(state.fleet_other)
        battles = [bot for bot in allies if bot.class_ == BotClass.Battle]
        healers = [bot for bot in allies if bot.class_ == BotClass.Healer]
        extractors = [bot for bot in allies if bot.class_ == BotClass.Extractor]
        payload = state.payload_pos()
        enemy_centre = _centroid(enemies, state.deposit_other.pos)
        endgame = state.tick >= conf.max_ticks - conf.endgame_ticks
        losing = state.capture < self.LOSING_CAPTURE

        slot_assignment = self._assign_miner_slots(extractors)
        payload_for_miners = self._miner_payload_positions(state, max(1, len(extractors)))
        for index, bot in enumerate(extractors):
            bot_action = action.bots[bot.id]
            if endgame or losing:
                target = payload_for_miners[index % len(payload_for_miners)]
                bot_action.move_action = self._spread_move(bot, target, allies, conf)
                bot_action.turn_action = turn_towards(enemy_centre)
                bot_action.special_action = SpecialAction.Extractor(mine=False)
            else:
                target = slot_assignment.get(bot.id, self.mine_slots[index % len(self.mine_slots)])
                bot_action.move_action = self._spread_move(bot, target, allies, conf)
                bot_action.turn_action = turn_towards(state.deposit_me.pos)
                in_range = bot.pos.dist(state.deposit_me.pos) <= conf.bot.base_extract_range
                can_see = line_of_sight(bot.pos, state.deposit_me.pos)
                bot_action.special_action = SpecialAction.Extractor(mine=in_range and can_see)

        self._control_healers(state, conf, action, allies, healers, payload, enemy_centre)
        fire_plans = self._control_battles(
            state, conf, action, allies, enemies, battles, healers, payload, enemy_centre, losing
        )
        self._assign_coordinated_fire(state, conf, action, fire_plans, enemies)
        return action

    # ---------------------------------------------------------------------- healers

    def _control_healers(
        self,
        state: GameState,
        conf: GameConfig,
        action: FleetAction,
        allies: list[BotState],
        healers: list[BotState],
        payload: Vec2,
        enemy_centre: Vec2,
    ) -> None:
        assigned: defaultdict[int, int] = defaultdict(int)
        retreating_ids = {
            bot.id for bot in allies
            if bot.class_ == BotClass.Battle
            and bot.health < self.RETREAT_HP_FRAC * conf.bot.health
        }
        candidates = [bot for bot in allies if bot.health < conf.bot.health - 0.02]

        for healer_index, healer in enumerate(healers):
            best = None
            best_score = -1e9
            for ally in candidates:
                if ally.id == healer.id or assigned[ally.id] >= int(conf.bot.heal_stack_cap):
                    continue
                missing = conf.bot.health - ally.health
                score = missing * 55.0
                score += 105.0 if ally.class_ == BotClass.Battle else 25.0
                score += max(0.0, 90.0 - ally.pos.dist(payload) * 35.0)
                score -= healer.pos.dist(ally.pos) * 9.0
                if ally.invulnerable_until_tick > state.tick:
                    score += 25.0
                # Retreating bots are actively seeking healing; prioritise them.
                if ally.id in retreating_ids:
                    score += 80.0
                if score > best_score:
                    best_score, best = score, ally

            bot_action = action.bots[healer.id]
            if best is None:
                away = (payload - enemy_centre).normalize_or_zero()
                flank = Vec2(-away.y, away.x) * (((healer_index % 3) - 1) * 0.72)
                target = payload + away * (1.25 + (healer_index // 3) * 0.58) + flank
                bot_action.move_action = self._spread_move(healer, target, allies, conf)
                bot_action.turn_action = turn_towards(payload)
                bot_action.special_action = SpecialAction.Healer(fire=False, target=0)
                continue

            heal_slot = assigned[best.id]
            assigned[best.id] += 1
            away = (best.pos - enemy_centre).normalize_or_zero()
            if away.norm_sq() < 0.1:
                away = (state.deposit_me.pos - best.pos).normalize_or_zero()
            side = Vec2(-away.y, away.x) * ((heal_slot - 1) * 0.48)
            follow = best.pos + away * min(conf.bot.base_heal_range * 0.62, 1.65) + side
            bot_action.move_action = self._spread_move(healer, follow, allies, conf)

            predicted = _future_pos(best)
            bot_action.turn_action = turn_towards(predicted)
            desired_angle = (predicted - healer.pos).angle_deg()
            turn = _clamp(
                diff_degrees(desired_angle, healer.angle),
                -conf.bot.turn_speed,
                conf.bot.turn_speed,
            )
            after_turn = healer.angle + turn
            in_arc = abs(diff_degrees(desired_angle, after_turn)) <= conf.bot.base_heal_arc_deg / 2
            can_heal = (
                healer.pos.dist(predicted) <= conf.bot.base_heal_range
                and line_of_sight(healer.pos, predicted)
                and in_arc
            )
            bot_action.special_action = SpecialAction.Healer(fire=can_heal, target=best.id)

    # ---------------------------------------------------------------------- battles

    def _control_battles(
        self,
        state: GameState,
        conf: GameConfig,
        action: FleetAction,
        allies: list[BotState],
        enemies: list[BotState],
        battles: list[BotState],
        healers: list[BotState],
        payload: Vec2,
        enemy_centre: Vec2,
        losing: bool,
    ) -> list[tuple[BotState, Vec2]]:
        fire_plans: list[tuple[BotState, Vec2]] = []
        if not battles:
            return fire_plans

        # Drop home defence when losing — every bot is needed at the objective.
        home_threats = sorted(
            (enemy for enemy in enemies if enemy.pos.dist(state.deposit_me.pos) < 11.5),
            key=lambda enemy: enemy.pos.dist_sq(state.deposit_me.pos),
        )
        defender_ids: set[int] = set()
        if home_threats and not losing:
            defenders = sorted(
                battles, key=lambda bot: bot.pos.dist_sq(state.deposit_me.pos)
            )[:2]
            defender_ids = {bot.id for bot in defenders}

        front = [bot for bot in battles if bot.id not in defender_ids]
        positions = self._payload_positions(state, max(1, len(front)))
        support_targets = [enemy for enemy in enemies if enemy.class_ != BotClass.Battle]
        support_targets.sort(key=lambda enemy: enemy.pos.dist_sq(payload))

        for index, bot in enumerate(battles):
            bot_action = action.bots[bot.id]

            # Self-destruct: dying bot surrounded by clustered enemies, no healer reachable.
            if bot.health <= self.DESTRUCT_HP_FRAC * conf.bot.health:
                nearby_enemies = [
                    e for e in enemies
                    if bot.pos.dist(e.pos) <= conf.bot.base_blaster_splash_radius * 1.8
                ]
                nearest_healer = (
                    min(healers, key=lambda h: bot.pos.dist_sq(h.pos)) if healers else None
                )
                healer_far = (
                    nearest_healer is None
                    or bot.pos.dist(nearest_healer.pos) > conf.bot.base_heal_range * 1.5
                )
                if len(nearby_enemies) >= 3 and healer_far:
                    bot_action.self_destruct = True
                    continue

            # HP-aware retreat: pull back toward the nearest healer instead of pushing forward.
            if bot.health < self.RETREAT_HP_FRAC * conf.bot.health and healers:
                nearest_healer = min(healers, key=lambda h: bot.pos.dist_sq(h.pos))
                bot_action.move_action = self._spread_move(
                    bot, nearest_healer.pos, allies, conf
                )
                tgt = self._best_target(state, conf, bot, enemies, payload)
                aim = self._lead_pos(bot, tgt, conf) if tgt else enemy_centre
                bot_action.turn_action = turn_towards(aim)
                bot_action.special_action = SpecialAction.Battle(fire=False)
                fire_plans.append((bot, nearest_healer.pos))
                continue

            if bot.id in defender_ids:
                threat = home_threats[bot.id % len(home_threats)]
                guard_dir = (threat.pos - state.deposit_me.pos).normalize_or_zero()
                destination = state.deposit_me.pos + guard_dir * 3.0
            elif losing:
                destination = positions[index % len(positions)]
            else:
                front_index = front.index(bot) if bot in front else index
                destination = positions[front_index % len(positions)]
                if (
                    len(battles) >= 13
                    and support_targets
                    and front_index >= max(9, len(front) - 4)
                    and state.capture > -0.45
                ):
                    quarry = support_targets[front_index % len(support_targets)]
                    if bot.pos.dist(quarry.pos) < 13.0:
                        destination = quarry.pos

            bot_action.move_action = self._spread_move(bot, destination, allies, conf)

            tgt = self._best_target(state, conf, bot, enemies, payload)
            aim = self._lead_pos(bot, tgt, conf) if tgt else enemy_centre
            bot_action.turn_action = turn_towards(aim)
            bot_action.special_action = SpecialAction.Battle(fire=False)
            fire_plans.append((bot, destination))

        return fire_plans

    def _assign_coordinated_fire(
        self,
        state: GameState,
        conf: GameConfig,
        action: FleetAction,
        fire_plans: list[tuple[BotState, Vec2]],
        enemies: list[BotState],
    ) -> None:
        reserved: set[int] = set()
        payload = state.payload_pos()

        shooters = [bot for bot, _ in fire_plans if bot.next_fire_tick <= state.tick]
        shooters.sort(
            key=lambda bot: min((bot.pos.dist_sq(e.pos) for e in enemies), default=1e9)
        )
        for shooter in shooters:
            target = self._best_target(state, conf, shooter, enemies, payload, reserved)
            if target is None:
                continue
            predicted = self._lead_pos(shooter, target, conf)
            if shooter.pos.dist(predicted) > conf.bot.blaster_range:
                continue
            if not self._shot_clear(state, conf, shooter.pos, predicted):
                continue

            action.bots[shooter.id].turn_action = turn_towards(predicted)
            desired = (predicted - shooter.pos).angle_deg()
            rotation = _clamp(
                diff_degrees(desired, shooter.angle),
                -conf.bot.turn_speed,
                conf.bot.turn_speed,
            )
            after_turn = shooter.angle + rotation
            distance = max(0.01, shooter.pos.dist(predicted))
            tolerance = max(
                1.0, math.degrees(math.asin(min(0.99, conf.bot.radius / distance)))
            )
            if abs(diff_degrees(desired, after_turn)) <= tolerance:
                action.bots[shooter.id].special_action = SpecialAction.Battle(fire=True)
                reserved.add(target.id)

    # -------------------------------------------------------------- budget fallback

    def _cheap_actions(self, state: GameState, conf: GameConfig) -> FleetAction:
        action = FleetAction.new()
        self._production(state, conf, action)
        allies = list(state.fleet_me)
        enemies = list(state.fleet_other)
        payload = state.payload_pos()

        for bot in allies:
            bot_action = action.bots[bot.id]
            if bot.class_ == BotClass.Extractor:
                target = min(self.mine_slots, key=lambda s: bot.pos.dist_sq(s))
                bot_action.move_action = move_bot(navigate_to(bot.pos, target))
                bot_action.turn_action = turn_towards(state.deposit_me.pos)
                bot_action.special_action = SpecialAction.Extractor(
                    mine=bot.pos.dist(state.deposit_me.pos) <= conf.bot.base_extract_range
                )
            elif bot.class_ == BotClass.Healer:
                wounded = min(
                    (ally for ally in allies if ally.id != bot.id),
                    key=lambda ally: ally.health,
                    default=None,
                )
                target = wounded.pos if wounded else payload
                bot_action.move_action = move_bot(navigate_to(bot.pos, target))
                bot_action.turn_action = turn_towards(target)
                can_heal = wounded is not None and bot.pos.dist(target) <= conf.bot.base_heal_range
                bot_action.special_action = SpecialAction.Healer(
                    fire=can_heal, target=wounded.id if wounded else 0
                )
            else:
                enemy = min(enemies, key=lambda other: bot.pos.dist_sq(other.pos), default=None)
                target_pos = enemy.pos if enemy else payload
                bot_action.move_action = move_bot(navigate_to(bot.pos, payload))
                bot_action.turn_action = turn_towards(target_pos)
                bot_action.special_action = SpecialAction.Battle(fire=False)
        return action


class ReferenceStrategy:
    """Deliberately simple active baseline used only by local A/B tests."""

    def __call__(self, state: GameState) -> FleetAction:
        conf = get_config()
        action = FleetAction.new()
        allies = list(state.fleet_me)
        enemies = list(state.fleet_other)
        count = _counts(allies)
        action.fabricator_next = int(
            BotClass.Extractor if count[BotClass.Extractor] < 3 else BotClass.Battle
        )
        action.rush_order = (
            state.tick < conf.max_ticks - conf.endgame_ticks
            and not state.fleet_me.is_full()
            and state.fabricator_me.tokens >= conf.fabricator.rush_cost
        )
        mine = state.deposit_me.pos + Vec2(0.0, conf.deposit.radius + conf.bot.radius + 0.2)
        payload = state.payload_pos()
        for bot in allies:
            bot_action = action.bots[bot.id]
            if bot.class_ == BotClass.Extractor:
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
