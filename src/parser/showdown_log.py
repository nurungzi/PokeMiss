"""Showdown 배틀 로그 파서: 로그 → 턴별 상태 + 양측 행동.

입력으로 받을 수 있는 것
------------------------
- 리플레이 페이지에서 복사한 로그 텍스트 (|switch|..., |move|... 형식)
- 리플레이 JSON (replay.pokemonshowdown.com/<id>.json 의 "log")
- 리플레이 HTML 다운로드 파일 (<script class="battle-log-data">)
- 로컬 서버 로그 파일 (showdown/pokemon-showdown/logs/**/*.log.json)

결과 (ParsedBattle)
-------------------
- turns: TurnRecord 목록. 각 턴 시작 시점의 상태(state)와 그 턴에 양측이 고른 행동(actions)
- sets: 경기 전체에서 드러난 각 포켓몬의 기술·도구·특성 (복기 평가 때 '사후 정보'로 사용)
- to_rows(): 표(DataFrame)로 보기 좋은 형태

행동 판정 규칙
--------------
- 턴 안에서 그 쪽이 처음 한 |move| 또는 |switch| 를 그 턴의 선택으로 본다.
- 기절 뒤 교체, 유턴 등으로 인한 교체는 선택이 아니라 '교체 투입'으로 따로 기록한다.
- 반동으로 움직일 수 없는 턴, 2턴째 고정 기술([from] lockedmove)은 forced=True 로 표시한다.
- 상대보다 늦어서 기절해 행동하지 못한 경우는 kind="none" 이다.
"""
from __future__ import annotations

import copy
import html
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

BOOST_KEYS = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")


def to_id(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------
@dataclass
class MonState:
    species: str                      # 현재 모습 (메가진화 후면 메가 이름)
    base_species: str                 # 처음 등장한 종
    hp: float = 1.0                   # 남은 HP 비율 (0~1)
    status: Optional[str] = None
    boosts: dict = field(default_factory=dict)
    item: Optional[str] = None        # 드러난 도구 ("" = 도구 없음 확인)
    ability: Optional[str] = None
    moves: list = field(default_factory=list)   # 지금까지 드러난 기술
    fainted: bool = False
    mega: bool = False


@dataclass
class SideState:
    name: str = ""
    rating: Optional[int] = None
    preview: list = field(default_factory=list)       # 팀 프리뷰 6마리
    active: Optional[str] = None                      # 활성 포켓몬 키 (닉네임)
    mons: dict = field(default_factory=dict)          # 닉네임 → MonState
    conditions: list = field(default_factory=list)    # 리플렉터, 스텔스록 등
    mega_used: bool = False

    def active_mon(self) -> Optional[MonState]:
        return self.mons.get(self.active) if self.active else None


@dataclass
class Action:
    kind: str = "none"          # move / switch / cant / none
    name: str = ""              # 기술 이름 또는 교체해 들어온 포켓몬
    mega: bool = False
    forced: bool = False        # 고정 기술 등 선택이 아닌 행동
    user: str = ""              # 행동한 포켓몬 (교체면 나간 포켓몬)


@dataclass
class TurnRecord:
    turn: int
    state: dict                                   # {"p1": SideState 복사본, "p2": ..., "weather": ...}
    actions: dict = field(default_factory=lambda: {"p1": Action(), "p2": Action()})
    events: list = field(default_factory=list)    # 그 턴에 일어난 일 (데미지, 급소, 빗나감 ...)
    replacements: dict = field(default_factory=dict)  # 턴 끝 교체 투입 {side: [포켓몬...]}


@dataclass
class ParsedBattle:
    format: str = ""
    players: dict = field(default_factory=dict)
    ratings: dict = field(default_factory=dict)
    winner: Optional[str] = None                  # "p1" / "p2" / "tie" / None
    preview: dict = field(default_factory=dict)
    leads: dict = field(default_factory=dict)
    brought: dict = field(default_factory=dict)   # 실제로 나온 포켓몬
    turns: list = field(default_factory=list)
    sets: dict = field(default_factory=dict)      # side → 종 → {"moves", "item", "ability"}

    def to_rows(self) -> list[dict]:
        """턴 x 진영 한 줄씩. pandas.DataFrame(battle.to_rows()) 로 바로 표가 된다."""
        rows = []
        for t in self.turns:
            for side in ("p1", "p2"):
                foe = "p2" if side == "p1" else "p1"
                me = t.state[side].active_mon()
                op = t.state[foe].active_mon()
                act = t.actions[side]
                rows.append({
                    "turn": t.turn, "side": side, "player": self.players.get(side, ""),
                    "active": me.species if me else None, "hp": round(me.hp, 3) if me else None,
                    "status": me.status if me else None, "boosts": dict(me.boosts) if me else {},
                    "opp_active": op.species if op else None, "opp_hp": round(op.hp, 3) if op else None,
                    "opp_status": op.status if op else None,
                    "action": act.kind, "choice": act.name, "mega": act.mega, "forced": act.forced,
                    "weather": t.state.get("weather"),
                    "won": (self.winner == side) if self.winner in ("p1", "p2") else None,
                })
        return rows

    def summary(self) -> str:
        lines = [f"{self.format}: {self.players.get('p1')} vs {self.players.get('p2')} → 승자 "
                 f"{self.players.get(self.winner, self.winner)} / {len(self.turns)}턴"]
        for t in self.turns:
            parts = []
            for side in ("p1", "p2"):
                a = t.actions[side]
                me = t.state[side].active_mon()
                who = me.species if me else "?"
                hp = f"{me.hp:.0%}" if me else ""
                desc = {"move": f"{a.name}{' (메가)' if a.mega else ''}", "switch": f"교체 → {a.name}",
                        "cant": f"행동 불가({a.name})", "none": "행동 못 함"}[a.kind]
                parts.append(f"{side} {who}({hp}): {desc}{' [고정]' if a.forced else ''}")
            lines.append(f"  턴 {t.turn:>2} | " + " | ".join(parts))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 입력 읽기
# ---------------------------------------------------------------------------
def load_log(source: str | Path) -> str:
    """파일 경로 또는 로그 문자열을 받아 프로토콜 텍스트로 돌려준다."""
    text = str(source)
    p = Path(text) if len(text) < 500 and "\n" not in text else None
    if p is not None and p.exists():
        text = p.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
        log = data.get("log", "")
        return "\n".join(log) if isinstance(log, list) else log
    m = re.search(r'<script[^>]*class="battle-log-data"[^>]*>(.*?)</script>', text, re.S)
    if m:
        return html.unescape(m.group(1))
    return text


# ---------------------------------------------------------------------------
# 파서
# ---------------------------------------------------------------------------
def _ident(token: str) -> tuple[str, str]:
    """'p1a: Garchomp' → ('p1', 'Garchomp')"""
    side, _, name = token.partition(": ")
    return side[:2], name.strip()


def _species_from_details(details: str) -> str:
    return details.split(",")[0].strip()


def _hp(token: str) -> tuple[Optional[float], Optional[str], bool]:
    """'55/100 brn' → (0.55, 'brn', False) / '0 fnt' → (0.0, None, True)"""
    tok = token.split()
    if not tok:
        return None, None, False
    if tok[0] == "0" or (len(tok) > 1 and tok[1] == "fnt"):
        return 0.0, None, True
    cur, _, mx = tok[0].partition("/")
    try:
        hp = int(cur) / int(mx) if mx else None
    except ValueError:
        hp = None
    status = tok[1] if len(tok) > 1 else None
    return hp, status, False


class _Parser:
    def __init__(self):
        self.b = ParsedBattle()
        self.sides = {"p1": SideState(), "p2": SideState()}
        self.weather: Optional[str] = None
        self.cur: Optional[TurnRecord] = None
        self.faint_in_turn: set = set()
        self.pending_mega: dict = {}

    # ---- 포켓몬 찾기 ----
    def mon(self, ident: str, details: Optional[str] = None) -> tuple[str, MonState]:
        side, name = _ident(ident)
        s = self.sides[side]
        if name not in s.mons:
            sp = _species_from_details(details) if details else name
            s.mons[name] = MonState(species=sp, base_species=sp)
        return side, s.mons[name]

    def snapshot(self) -> dict:
        return {"p1": copy.deepcopy(self.sides["p1"]), "p2": copy.deepcopy(self.sides["p2"]),
                "weather": self.weather}

    def event(self, *items):
        if self.cur is not None:
            self.cur.events.append(list(items))

    def _reveal_from(self, parts: list[str], ident: Optional[str]):
        """'[from] item: Life Orb', '[from] ability: Levitate' 같은 꼬리표로 도구·특성 공개."""
        of = next((p[5:].strip() for p in parts if p.startswith("[of] ")), None)
        for p in parts:
            if p.startswith("[from] item: "):
                target = of or ident
                if target:
                    _, m = self.mon(target)
                    m.item = p[len("[from] item: "):]
            elif p.startswith("[from] ability: "):
                target = of or ident
                if target:
                    _, m = self.mon(target)
                    m.ability = p[len("[from] ability: "):]

    # ---- 행동 기록 ----
    def set_action(self, side: str, action: Action):
        if self.cur is None:
            return
        if self.cur.actions[side].kind == "none" and side not in self.faint_in_turn:
            self.cur.actions[side] = action

    def handle(self, parts: list[str]):
        cmd = parts[1] if len(parts) > 1 else ""
        a = parts[2:]
        if cmd == "player" and len(a) >= 2 and a[1]:
            s = self.sides[a[0]]
            s.name = a[1]
            if len(a) >= 4 and a[3].isdigit():
                s.rating = int(a[3])
        elif cmd == "tier":
            self.b.format = a[0]
        elif cmd == "poke":
            self.sides[a[0]].preview.append(_species_from_details(a[1]))
        elif cmd in ("switch", "drag", "replace"):
            side, m = self.mon(a[0], a[1])
            s = self.sides[side]
            old = s.active_mon()
            if old is not None and cmd != "replace":
                old.boosts = {}
            if cmd != "replace":
                m.species = _species_from_details(a[1])
            hp, st, fnt = _hp(a[2]) if len(a) > 2 else (None, None, False)
            if hp is not None:
                m.hp = hp
            m.status = st
            prev_active = s.active
            s.active = _ident(a[0])[1]
            if self.cur is None:
                self.b.leads[side] = m.species
            elif cmd == "switch":
                if self.cur.actions[side].kind == "none" and side not in self.faint_in_turn and prev_active:
                    self.set_action(side, Action("switch", m.species, user=s.mons[prev_active].species))
                else:
                    self.cur.replacements.setdefault(side, []).append(m.species)
            self.event("switch", side, m.species)
        elif cmd == "detailschange":
            _, m = self.mon(a[0])
            m.species = _species_from_details(a[1])
        elif cmd == "-formechange":
            _, m = self.mon(a[0])
            m.species = a[1]
        elif cmd == "-mega":
            side, m = self.mon(a[0])
            m.mega = True
            if len(a) > 2 and a[2]:
                m.item = a[2]
            self.sides[side].mega_used = True
            self.pending_mega[side] = True
        elif cmd == "move":
            side, m = self.mon(a[0])
            move = a[1]
            tags = a[3:] if len(a) > 3 else []
            called = any(t.startswith("[from]") and "lockedmove" not in t for t in tags)
            locked = any("lockedmove" in t for t in tags)
            if not called and move not in m.moves and move != "Struggle":
                m.moves.append(move)
            if not called:
                self.set_action(side, Action("move", move, mega=self.pending_mega.pop(side, False),
                                             forced=locked, user=m.species))
            self.event("move", side, m.species, move, *[t for t in tags if t in ("[miss]", "[still]")])
        elif cmd == "cant":
            side, m = self.mon(a[0])
            reason = a[1] if len(a) > 1 else ""
            if len(a) > 2 and a[2] and a[2] not in m.moves:
                m.moves.append(a[2])
            self.set_action(side, Action("cant", reason, mega=self.pending_mega.pop(side, False),
                                         forced=reason == "recharge", user=m.species))
            self.event("cant", side, m.species, reason)
        elif cmd in ("-damage", "-heal", "-sethp"):
            side, m = self.mon(a[0])
            before = m.hp
            hp, st, fnt = _hp(a[1])
            if hp is not None:
                m.hp = hp
            if st:
                m.status = st
            self._reveal_from(a[2:], a[0])
            self.event("damage" if cmd == "-damage" else "heal", side, m.species, round(before - m.hp, 3),
                       next((t for t in a[2:] if t.startswith("[from]")), ""))
        elif cmd == "faint":
            side, m = self.mon(a[0])
            m.fainted = True
            m.hp = 0.0
            self.faint_in_turn.add(side)
            self.event("faint", side, m.species)
        elif cmd == "-status":
            side, m = self.mon(a[0])
            m.status = a[1]
            self._reveal_from(a[2:], a[0])
            self.event("status", side, m.species, a[1])
        elif cmd == "-curestatus":
            _, m = self.mon(a[0])
            m.status = None
        elif cmd in ("-boost", "-unboost"):
            side, m = self.mon(a[0])
            n = int(a[2]) if len(a) > 2 and a[2].lstrip("-").isdigit() else 1
            m.boosts[a[1]] = max(-6, min(6, m.boosts.get(a[1], 0) + (n if cmd == "-boost" else -n)))
            self.event(cmd[1:], side, m.species, a[1], n)
        elif cmd == "-setboost":
            _, m = self.mon(a[0])
            m.boosts[a[1]] = int(a[2])
        elif cmd in ("-clearboost", "-clearallboost"):
            targets = [self.sides[s].active_mon() for s in ("p1", "p2")] if cmd == "-clearallboost" else [self.mon(a[0])[1]]
            for t in targets:
                if t:
                    t.boosts = {}
        elif cmd == "-clearnegativeboost":
            _, m = self.mon(a[0])
            m.boosts = {k: v for k, v in m.boosts.items() if v > 0}
        elif cmd == "-item":
            _, m = self.mon(a[0])
            m.item = a[1]
            self._reveal_from(a[2:], None)
        elif cmd == "-enditem":
            _, m = self.mon(a[0])
            m.item = ""   # 소모·제거됨
            self.event("enditem", _ident(a[0])[0], m.species, a[1])
        elif cmd == "-ability":
            _, m = self.mon(a[0])
            m.ability = a[1]
        elif cmd == "-weather":
            self.weather = None if a[0] == "none" else a[0]
        elif cmd == "-sidestart":
            side = a[0][:2]
            cond = a[1].replace("move: ", "")
            if cond not in self.sides[side].conditions:
                self.sides[side].conditions.append(cond)
        elif cmd == "-sideend":
            side = a[0][:2]
            cond = a[1].replace("move: ", "")
            if cond in self.sides[side].conditions:
                self.sides[side].conditions.remove(cond)
        elif cmd in ("-crit", "-miss", "-supereffective", "-resisted", "-immune", "-fail"):
            target = a[1] if cmd == "-miss" and len(a) > 1 else (a[0] if a else "")
            if target:
                side, m = self.mon(target)
                self.event(cmd[1:], side, m.species)
        elif cmd == "turn":
            self.close_turn()
            self.cur = TurnRecord(int(a[0]), self.snapshot())
            self.faint_in_turn = set()
            self.pending_mega = {}
        elif cmd == "win":
            self.close_turn()
            name = a[0]
            self.b.winner = next((s for s in ("p1", "p2") if self.sides[s].name == name), name)
        elif cmd == "tie":
            self.close_turn()
            self.b.winner = "tie"

    def close_turn(self):
        if self.cur is not None:
            self.b.turns.append(self.cur)
            self.cur = None

    def finish(self) -> ParsedBattle:
        self.close_turn()
        b = self.b
        for side in ("p1", "p2"):
            s = self.sides[side]
            b.players[side] = s.name
            b.ratings[side] = s.rating
            b.preview[side] = s.preview
            b.brought[side] = [m.base_species for m in s.mons.values()]
            b.sets[side] = {m.species: {"moves": list(m.moves), "item": m.item, "ability": m.ability}
                            for m in s.mons.values()}
        return b


def parse_log(source: str | Path) -> ParsedBattle:
    """로그(문자열 또는 파일 경로)를 읽어 ParsedBattle을 돌려준다."""
    text = load_log(source)
    p = _Parser()
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|split|"):
            # 서버 로그: 다음 줄은 정확한 HP(비공개), 그다음 줄이 공개용 → 공개용만 쓴다
            i += 2
            line = lines[i].strip() if i < len(lines) else ""
        if line.startswith("|"):
            try:
                p.handle(line.split("|"))
            except (IndexError, ValueError):
                pass  # 알 수 없는 형식의 줄은 건너뛴다
        i += 1
    return p.finish()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python src/parser/showdown_log.py <로그 파일 또는 리플레이 JSON/HTML>")
        sys.exit(1)
    battle = parse_log(sys.argv[1])
    print(battle.summary())
    print("\n드러난 세트:")
    for side in ("p1", "p2"):
        for sp, s in battle.sets[side].items():
            print(f"  {side} {sp}: {', '.join(s['moves'])} / 도구 {s['item']} / 특성 {s['ability']}")
