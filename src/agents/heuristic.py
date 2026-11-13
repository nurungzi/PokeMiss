"""챔피언스 데미지 공식을 쓰는 휴리스틱 봇 (베이스라인).

판단 순서
---------
1. 팀 프리뷰: 내 6마리 x 상대 6마리 상성 점수 → 상위 3마리 선출, 1위를 선봉으로
2. 매 턴 각 기술 점수 계산
   - 공격기: 기대 데미지(상대 남은 HP 대비 %) x 명중률 + 쓰러뜨릴 확률 보너스
             상대가 먼저 움직여 나를 쓰러뜨릴 수 있으면 그 확률만큼 감점 (선공기는 예외)
   - 변화기: 랭크업·회복·상태이상·스텔스록을 상황 규칙으로 점수화
3. 불리한 대면이면 교체 (연속 교체는 하지 않음)
4. 메가진화가 가능하면 항상 메가진화

기술 점수와 교체 판단 근거를 last_decision에 남겨서, 나중에 복기 해설·디버깅에 쓴다.
"""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Optional

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from poke_env.player import Player  # noqa: E402

from agents.adapter import (  # noqa: E402
    assumed_sp, field_for, mega_species_for, opponent_mon, own_mon, species_id, speed_range,
)
from env.champions_calc import (  # noqa: E402
    ChampionsData, Field, Mon, calc_damage, default_data, effective_speed, to_id,
)

# 상대 기술을 모를 때 쓰는 타입별 대표 기술 (물리, 특수)
REPRESENTATIVE_MOVES = {
    "Normal": ("doubleedge", "hypervoice"), "Fire": ("flareblitz", "flamethrower"),
    "Water": ("liquidation", "surf"), "Grass": ("powerwhip", "energyball"),
    "Electric": ("wildcharge", "thunderbolt"), "Ice": ("iciclecrash", "icebeam"),
    "Fighting": ("closecombat", "aurasphere"), "Poison": ("gunkshot", "sludgebomb"),
    "Ground": ("earthquake", "earthpower"), "Flying": ("bravebird", "airslash"),
    "Psychic": ("zenheadbutt", "psychic"), "Bug": ("xscissor", "bugbuzz"),
    "Rock": ("stoneedge", "powergem"), "Ghost": ("shadowclaw", "shadowball"),
    "Dragon": ("dragonclaw", "dragonpulse"), "Dark": ("crunch", "darkpulse"),
    "Steel": ("ironhead", "flashcannon"), "Fairy": ("playrough", "moonblast"),
}
SELF_DROP_MOVES = {"overheat", "dracometeor", "leafstorm", "closecombat", "superpower", "makeitrain",
                   "fleurcannon", "psychoboost", "vcreate", "headlongrush", "armorcannon"}
HAZARDS = {"stealthrock": 32, "spikes": 22, "toxicspikes": 20, "stickyweb": 25}


@dataclass
class Decision:
    """한 턴의 판단 기록 (복기·디버깅용)."""

    turn: int
    action: str
    reason: str
    scores: dict = field(default_factory=dict)


class HeuristicBrain:
    """poke-env와 무관한 판단 로직. Mon/Field만 있으면 동작한다."""

    def __init__(self, data: Optional[ChampionsData] = None):
        self.data = data or default_data()

    # ---------------- 데미지 추정 ----------------
    def expected_damage(self, attacker: Mon, defender: Mon, move_id: str, fld: Field) -> tuple[float, float]:
        """(기대 데미지, 이번 공격으로 쓰러뜨릴 확률) — 명중률·연속기 반영."""
        move = self.data.moves.get(move_id)
        if not move or move["category"] == "Status":
            return 0.0, 0.0
        res = calc_damage(attacker, defender, move_id, fld)
        if not res.rolls:
            return 0.0, 0.0
        acc = 1.0 if move.get("accuracy") is None else move["accuracy"] / 100
        if attacker.ability_id == "noguard" or defender.ability_id == "noguard":
            acc = 1.0
        if attacker.ability_id == "compoundeyes":
            acc = min(1.0, acc * 1.3)
        dmg = mean(res.rolls) * res.hits
        return min(dmg, defender.hp) * acc, res.ko_chance(1) * acc

    def threat_moves(self, attacker: Mon, known_moves: list[str]) -> list[str]:
        """상대가 쓸 수 있다고 볼 공격기: 드러난 기술 + 자속 대표 기술."""
        moves = [m for m in known_moves if m in self.data.moves and self.data.moves[m]["category"] != "Status"]
        if len(known_moves) < 4:
            physical = attacker.stats["atk"] >= attacker.stats["spa"]
            for t in attacker.types:
                rep = REPRESENTATIVE_MOVES.get(t)
                if rep:
                    mid = rep[0] if physical else rep[1]
                    if mid in self.data.moves and mid not in moves:
                        moves.append(mid)
        return moves

    def best_threat(self, attacker: Mon, defender: Mon, known_moves: list[str], fld: Field) -> tuple[float, float, str]:
        """attacker가 defender에게 줄 수 있는 최대 기대 데미지와 그때의 1타 확률."""
        best = (0.0, 0.0, "")
        for m in self.threat_moves(attacker, known_moves):
            dmg, ko = self.expected_damage(attacker, defender, m, fld)
            if (ko, dmg) > (best[1], best[0]):
                best = (dmg, ko, m)
        return best

    # ---------------- 행동 순서 ----------------
    def p_opponent_first(self, me: Mon, opp: Mon, move_priority: int, opp_priority: int = 0,
                         fld: Optional[Field] = None, opp_speed_known: Optional[int] = None) -> float:
        """상대가 나보다 먼저 행동할 확률 (스피드 범위가 겹치면 50%)."""
        if move_priority != opp_priority:
            return 0.0 if move_priority > opp_priority else 1.0
        my_spe = effective_speed(me, fld)
        if opp_speed_known is not None:
            lo = hi = opp_speed_known
        else:
            lo, hi = speed_range(opp.species, self.data)
            probe = Mon(opp.species, boosts=opp.boosts, status=opp.status, ability=opp.ability,
                        item=opp.item, data=self.data)
            scale = effective_speed(probe, fld) / max(1, probe.stats["spe"])
            lo, hi = int(lo * scale), int(hi * scale)
        if my_spe > hi:
            return 0.0
        if my_spe < lo:
            return 1.0
        return 0.5

    # ---------------- 기술 점수 ----------------
    def score_moves(self, me: Mon, opp: Mon, moves: list[str], fld_opp: Field, fld_me: Field,
                    opp_known_moves: list[str], turn: int, opp_alive: int) -> dict:
        threat_dmg, threat_ko, _ = self.best_threat(opp, me, opp_known_moves, fld_me)
        threat_pct = 100 * threat_dmg / max(1, me.max_hp)
        my_hp_frac = me.hp / max(1, me.max_hp)
        scores = {}
        attack_scores = {}
        for mid in moves:
            move = self.data.moves.get(mid)
            if move and move["category"] != "Status":
                dmg, ko = self.expected_damage(me, opp, mid, fld_opp)
                attack_scores[mid] = 100 * dmg / max(1, opp.max_hp) + 60 * ko
        best_attack = max(attack_scores.values(), default=0.0)
        for mid in moves:
            move = self.data.moves.get(mid)
            if move is None:
                scores[mid] = 0.0
                continue
            if move["category"] != "Status":
                dmg, ko = self.expected_damage(me, opp, mid, fld_opp)
                score = attack_scores[mid]
                p_first = self.p_opponent_first(me, opp, move["priority"], 0, fld_me)
                score *= 1 - 0.8 * p_first * threat_ko    # 먼저 맞고 쓰러지면 기술이 안 나감
                acc = 100 if move.get("accuracy") is None else move["accuracy"]
                score += 0.02 * acc                        # 동점이면 명중률 높은 기술
                if move["priority"] > 0 and ko > 0.5:
                    score += 5                             # 마무리는 선공기로
                if "charge" in move.get("flags", []):     # 모으는 턴이 필요한 기술 (날씨로 바로 쓰는 경우 제외)
                    w = (fld_opp.weather or "").lower()
                    instant = (mid == "electroshot" and w in {"rain", "raindance", "primordialsea"}) or \
                              (mid in {"solarbeam", "solarblade"} and w in {"sun", "sunnyday", "desolateland"})
                    if not instant and me.item_id != "powerherb":
                        score *= 0.5
                if mid in SELF_DROP_MOVES and ko < 0.9:
                    score -= 8
                if move.get("recoil") or mid in {"flareblitz", "bravebird", "doubleedge", "wildcharge", "headsmash"}:
                    score -= 4 * (1 - ko)
                scores[mid] = round(score, 1)
                continue
            scores[mid] = round(self._score_status(me, opp, mid, move, threat_pct, threat_ko,
                                                   my_hp_frac, turn, opp_alive, best_attack), 1)
        return scores

    def _score_status(self, me: Mon, opp: Mon, mid: str, move: dict, threat_pct: float, threat_ko: float,
                      my_hp_frac: float, turn: int, opp_alive: int, best_attack: float = 0.0) -> float:
        boosts = move.get("boosts") if move.get("target") == "self" else None
        boosts = boosts or move.get("selfBoost")
        if boosts and all(v > 0 for v in boosts.values()):
            already = sum(max(0, me.boosts.get(k, 0)) for k in boosts)
            # 상대가 나를 거의 못 때리고, 지금 공격으로는 한 번에 못 잡을 때만 랭크업
            if my_hp_frac > 0.6 and threat_pct < 30 and threat_ko < 0.05 and already < 2 and best_attack < 100:
                return best_attack * 1.15 + 5
            return 0.0
        if move.get("heal") or mid in {"roost", "recover", "slackoff", "softboiled", "moonlight", "synthesis",
                                       "morningsun", "shoreup", "milkdrink"}:
            if my_hp_frac < 0.5 and threat_pct < 45:
                return 65.0
            return 0.0
        status = move.get("status")
        if status and not opp.status:
            if opp.ability_id in {"magicbounce", "purifyingsalt", "comatose"}:
                return 0.0
            types = set(opp.types)
            if status == "brn" and "Fire" not in types:
                return 55.0 if opp.stats["atk"] > opp.stats["spa"] else 20.0
            if status in {"tox", "psn"} and not types & {"Poison", "Steel"}:
                return 40.0
            if status == "par" and not types & {"Electric"} and not (mid == "thunderwave" and "Ground" in types):
                return 45.0 if self.p_opponent_first(me, opp, 0) > 0 else 20.0
            if status == "slp":
                return 55.0
            return 0.0
        if mid in HAZARDS and turn <= 3 and opp_alive >= 2:
            return float(HAZARDS[mid])
        return 0.0

    # ---------------- 교체 ----------------
    def matchup(self, mine: Mon, opp: Mon, my_moves: list[str], opp_known: list[str],
                fld_opp: Field, fld_me: Field) -> float:
        """양수일수록 내게 유리한 대면. (내가 줄 데미지% - 받을 데미지%)"""
        offense = 0.0
        for m in my_moves:
            dmg, _ = self.expected_damage(mine, opp, m, fld_opp)
            offense = max(offense, 100 * dmg / max(1, opp.max_hp))
        threat, _, _ = self.best_threat(opp, mine, opp_known, fld_me)
        return offense - 100 * threat / max(1, mine.max_hp)


class HeuristicPlayer(Player):
    """poke-env 플레이어. 서버 대전과 오프라인 시뮬레이터 모두에서 쓴다."""

    def __init__(self, *args, data: Optional[ChampionsData] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.brain = HeuristicBrain(data)
        self.data = self.brain.data
        self.last_decision: Optional[Decision] = None
        self.decisions: dict[str, list[Decision]] = {}
        self._last_was_switch: dict[str, bool] = {}

    # ---------- 공통 ----------
    def _record(self, battle, action: str, reason: str, scores: Optional[dict] = None):
        d = Decision(battle.turn, action, reason, scores or {})
        self.last_decision = d
        self.decisions.setdefault(battle.battle_tag, []).append(d)

    @staticmethod
    def _opp_known_moves(opp_pokemon) -> list[str]:
        return [to_id(m) for m in (opp_pokemon.moves or {})]

    def _my_moves(self, pokemon) -> list[str]:
        return [to_id(m) for m in (pokemon.moves or {})]

    # ---------- 팀 프리뷰 ----------
    def teampreview(self, battle):
        mine = list(battle.team.values())
        opps = list(battle.teampreview_opponent_team or [])
        if not opps:
            return self.random_teampreview(battle)
        fld = Field()
        scores = []
        for i, p in enumerate(mine):
            try:
                me = own_mon(p, self.data, as_mega=mega_species_for(p, self.data) is not None)
                total = 0.0
                for o in opps:
                    om = opponent_mon(o, self.data)
                    total += self.brain.matchup(me, om, self._my_moves(p), [], fld, fld)
                scores.append((total / len(opps), i + 1))
            except Exception:
                scores.append((-999.0, i + 1))
        scores.sort(reverse=True)
        picked = [idx for _, idx in scores[:3]]
        rest = [idx for _, idx in scores[3:]]
        for idx in picked:
            mine[idx - 1]._selected_in_teampreview = True
        self._record(battle, "teampreview " + "".join(map(str, picked)), "상성 점수 상위 3마리 선출",
                     {mine[i - 1].species: round(s, 1) for s, i in scores})
        return "/team " + "".join(str(i) for i in picked + rest)

    # ---------- 매 턴 ----------
    def choose_move(self, battle):
        try:
            return self._choose(battle)
        except Exception as e:  # 계산 실패 시에도 대전이 멈추지 않도록
            self._record(battle, "random", f"오류로 무작위 선택: {e!r}")
            return self.choose_random_move(battle)

    def _bench_choice(self, battle, opp: Mon) -> tuple[Optional[object], float]:
        opp_known = self._opp_known_moves(battle.opponent_active_pokemon)
        best, best_score = None, -1e9
        for p in battle.available_switches:
            mine = own_mon(p, self.data)
            fld_opp, fld_me = field_for(battle, True), field_for(battle, False)
            score = self.brain.matchup(mine, opp, self._my_moves(p), opp_known, fld_opp, fld_me)
            score += 20 * mine.hp / max(1, mine.max_hp)  # 체력 많은 쪽 선호
            if score > best_score:
                best, best_score = p, score
        return best, best_score

    def _choose(self, battle):
        tag = battle.battle_tag
        opp_p = battle.opponent_active_pokemon
        if opp_p is None:
            return self.choose_random_move(battle)
        opp = opponent_mon(opp_p, self.data)

        if battle.force_switch or not battle.available_moves:
            if battle.available_switches:
                target, s = self._bench_choice(battle, opp)
                self._record(battle, f"switch {target.species}", f"기절/강제 교체: 상성 점수 {s:.0f}")
                self._last_was_switch[tag] = True
                return self.create_order(target)
            return self.choose_random_move(battle)

        me_p = battle.active_pokemon
        mega = bool(battle.can_mega_evolve)
        me = own_mon(me_p, self.data, as_mega=mega)
        fld_opp, fld_me = field_for(battle, True), field_for(battle, False)
        moves = [to_id(m.id) for m in battle.available_moves]
        opp_known = self._opp_known_moves(opp_p)
        opp_alive = 3 - sum(1 for p in battle.opponent_team.values() if p.fainted)
        scores = self.brain.score_moves(me, opp, moves, fld_opp, fld_me, opp_known, battle.turn, opp_alive)
        best_mid = max(scores, key=scores.get)
        best_score = scores[best_mid]

        # 교체 판단
        if battle.available_switches and not battle.trapped and not self._last_was_switch.get(tag):
            threat_dmg, threat_ko, _ = self.brain.best_threat(opp, me, opp_known, fld_me)
            p_first = self.brain.p_opponent_first(me, opp, 0, 0, fld_me)
            _, my_ko = self.brain.expected_damage(me, opp, best_mid, fld_opp)
            danger = threat_ko > 0.6 and p_first >= 0.5 and my_ko < 0.5
            useless = best_score < 20 and 100 * threat_dmg / max(1, me.max_hp) > 45
            if danger or useless:
                target, s = self._bench_choice(battle, opp)
                if target is not None:
                    incoming, in_ko, _ = self.brain.best_threat(opp, own_mon(target, self.data), opp_known, fld_me)
                    if in_ko < 0.5 and s > self.brain.matchup(me, opp, moves, opp_known, fld_opp, fld_me) + 10:
                        why = "먼저 맞으면 쓰러질 위험" if danger else "줄 수 있는 데미지가 너무 적음"
                        self._record(battle, f"switch {target.species}", why, scores)
                        self._last_was_switch[tag] = True
                        return self.create_order(target)

        self._last_was_switch[tag] = False
        move_obj = next(m for m in battle.available_moves if to_id(m.id) == best_mid)
        self._record(battle, f"move {best_mid}{' +mega' if mega else ''}", f"최고 점수 {best_score}", scores)
        return self.create_order(move_obj, mega=mega)
