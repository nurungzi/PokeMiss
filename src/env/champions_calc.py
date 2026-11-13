"""포켓몬 챔피언스(레벨 50 싱글) 능력치·데미지 계산 모듈.

Showdown의 champions 모드 코드(sim/battle-actions.ts, data/mods/champions/scripts.ts)를
그대로 옮겨 왔고, tests/test_champions_calc.py에서 실제 Showdown 시뮬레이터 결과와 비교해 검증한다.

핵심 공식
---------
능력치 (레벨 50, 개체값 31 고정, 노력치 대신 능력 포인트 SP: 능력치당 최대 32)
    HP      = 종족값 + SP + 75
    그 외   = (종족값 + SP + 20) x 성격 보정(1.1 / 0.9, 내림)

데미지 (Showdown 9세대 순서)
    기본 = floor(floor(floor(22 x 위력 x 공격 / 방어) / 50)) + 2
    → 날씨 → 급소(x1.5) → 난수(85~100%) → 자속(x1.5) → 상성 → 화상(x0.5)
    → 최종 보정(생명의구슬, 멀티스케일, 벽 등) → 결과
    보정값은 모두 4096 기준 정수 연산(pokeRound)으로 처리해 Showdown과 1의 자리까지 맞춘다.

지원 범위
---------
자주 쓰이는 특성·도구·기술 효과만 구현했다. 구현하지 않은 효과가 걸린 경우
DamageResult.notes에 "미지원"으로 표시되고, 그 효과를 뺀 근사값을 돌려준다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

STATS = ("hp", "atk", "def", "spa", "spd", "spe")
LEVEL = 50
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "processed" / "champions"


def to_id(text: Optional[str]) -> str:
    """Showdown ID 규칙: 영문 소문자와 숫자만 남긴다. ('Rotom-Wash' -> 'rotomwash')"""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# ---------------------------------------------------------------------------
# Showdown 정수 연산
# ---------------------------------------------------------------------------
def _mod4096(num: int, den: int = 1) -> int:
    """배율을 4096 기준 정수로 바꾼다. (1.5 -> 6144, [5324, 4096] -> 5324)"""
    return (num * 4096) // den


def apply_mod(value: int, mod4096: int) -> int:
    """battle.modify / finalModify: 4096 기준 배율을 적용하고 0.5는 내림하는 반올림."""
    return (value * mod4096 + 2047) // 4096


def chain(mods: list[int]) -> int:
    """battle.chainModify: 여러 배율(4096 기준)을 하나로 합친다."""
    m = 4096
    for nxt in mods:
        m = (m * nxt + 2048) >> 12
    return m


def frac(num: int, den: int = 1) -> int:
    return _mod4096(num, den)


X1_5, X2, X0_5, X0_75 = frac(3, 2), frac(2), frac(1, 2), frac(3, 4)


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
class ChampionsData:
    """tools/export_champions_data.js가 만든 JSON을 읽는다."""

    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR):
        d = Path(data_dir)
        if not (d / "species.json").exists():
            raise FileNotFoundError(
                f"{d}에 챔피언스 데이터가 없습니다. 먼저 `node tools/export_champions_data.js`를 실행하세요."
            )
        load = lambda name: json.loads((d / f"{name}.json").read_text(encoding="utf-8"))
        self.species: dict = load("species")
        self.moves: dict = load("moves")
        self.items: dict = load("items")
        self.abilities: dict = load("abilities")
        self.typechart: dict = load("typechart")
        self.natures: dict = load("natures")
        self.meta: dict = load("meta")
        lp = d / "learnsets.json"
        self.learnsets: dict = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}

    def get_species(self, name: str) -> dict:
        sid = to_id(name)
        if sid not in self.species:
            raise KeyError(f"챔피언스 데이터에 없는 포켓몬: {name}")
        return self.species[sid]

    def get_move(self, name: str) -> dict:
        mid = to_id(name)
        if mid not in self.moves:
            raise KeyError(f"챔피언스 데이터에 없는 기술: {name}")
        return {"id": mid, **self.moves[mid]}

    def effectiveness(self, atk_type: str, def_types: list[str]) -> float:
        m = 1.0
        for t in def_types:
            m *= self.typechart[atk_type][t]
        return m


_DEFAULT_DATA: Optional[ChampionsData] = None


def default_data() -> ChampionsData:
    global _DEFAULT_DATA
    if _DEFAULT_DATA is None:
        _DEFAULT_DATA = ChampionsData()
    return _DEFAULT_DATA


# ---------------------------------------------------------------------------
# 능력치
# ---------------------------------------------------------------------------
def calc_stat(stat: str, base: int, sp: int = 0, nature: str = "serious", data: Optional[ChampionsData] = None) -> int:
    """챔피언스 레벨 50 능력치 하나를 계산한다."""
    if not 0 <= sp <= 32:
        raise ValueError(f"능력 포인트는 0~32 사이여야 합니다: {stat}={sp}")
    if stat == "hp":
        return base + sp + 75
    value = base + sp + 20
    nat = (data or default_data()).natures.get(to_id(nature), {})
    if nat.get("plus") == stat:
        value = ((value * 110) % 65536) // 100
    elif nat.get("minus") == stat:
        value = ((value * 90) % 65536) // 100
    return value


def calc_stats(species: str, sp: Optional[dict] = None, nature: str = "serious",
               data: Optional[ChampionsData] = None) -> dict:
    """포켓몬의 6개 능력치를 계산한다. sp 예: {"atk": 32, "spe": 32, "hp": 2}"""
    data = data or default_data()
    base = data.get_species(species)["baseStats"]
    sp = sp or {}
    total = sum(sp.values())
    if total > 66:
        raise ValueError(f"능력 포인트 합계는 66 이하여야 합니다 (현재 {total})")
    return {s: calc_stat(s, base[s], sp.get(s, 0), nature, data) for s in STATS}


def infer_spread(species: str, stats: dict, data: Optional[ChampionsData] = None) -> tuple[dict, str]:
    """실제 능력치에서 능력 포인트와 성격을 거꾸로 추정한다. (메가진화 후 능력치 계산용)

    반환: (sp 딕셔너리, 성격 이름). 정확히 맞는 조합이 없으면 가장 가까운 값을 쓴다.
    """
    data = data or default_data()
    base = data.get_species(species)["baseStats"]
    sp: dict = {}
    plus = minus = None
    for stat in STATS:
        if stat not in stats or stats[stat] is None:
            continue
        target = stats[stat]
        if stat == "hp":
            sp["hp"] = max(0, min(32, target - base["hp"] - 75))
            continue
        best = None
        for mult, tag in ((1.0, None), (1.1, "plus"), (0.9, "minus")):
            for v in range(33):
                raw = base[stat] + v + 20
                val = raw if tag is None else ((raw * (110 if tag == "plus" else 90)) % 65536) // 100
                err = abs(val - target)
                if best is None or err < best[0]:
                    best = (err, v, tag)
        sp[stat] = best[1]
        if best[2] == "plus":
            plus = stat
        elif best[2] == "minus":
            minus = stat
    nature = "serious"
    for nid, n in data.natures.items():
        if n.get("plus") == plus and n.get("minus") == minus and plus and minus:
            nature = nid
            break
    return {k: v for k, v in sp.items() if v}, nature


def boosted(stat_value: int, stage: int) -> int:
    """랭크 보정 (-6 ~ +6). +1 = x1.5, -1 = x2/3 (내림)"""
    stage = max(-6, min(6, stage))
    if stage >= 0:
        return stat_value * (2 + stage) // 2
    return stat_value * 2 // (2 - stage)


# ---------------------------------------------------------------------------
# 배틀 상태 표현
# ---------------------------------------------------------------------------
@dataclass
class Mon:
    """계산에 필요한 포켓몬 한 마리의 상태."""

    species: str
    sp: dict = field(default_factory=dict)          # 능력 포인트
    nature: str = "serious"
    ability: Optional[str] = None                   # None이면 첫 번째 특성
    item: Optional[str] = None
    boosts: dict = field(default_factory=dict)      # {"atk": 2, "spe": -1}
    status: Optional[str] = None                    # brn / par / psn / tox / slp / frz
    hp: Optional[int] = None                        # 현재 HP (None이면 가득)
    fainted_allies: int = 0                         # 대장의패기(Supreme Overlord)용 쓰러진 아군 수
    stats_override: Optional[dict] = None           # 실제 능력치를 알 때 (내 포켓몬) 직접 지정
    data: Optional[ChampionsData] = field(default=None, repr=False)

    def __post_init__(self):
        self.data = self.data or default_data()
        self.species_data = self.data.get_species(self.species)
        if self.stats_override:
            self.stats = dict(self.stats_override)
        else:
            self.stats = calc_stats(self.species, self.sp, self.nature, self.data)
        if self.ability is None:
            self.ability = self.species_data["abilities"][0]
        self.ability_id = to_id(self.ability)
        self.item_id = to_id(self.item)
        if self.hp is None:
            self.hp = self.stats["hp"]

    @property
    def max_hp(self) -> int:
        return self.stats["hp"]

    @property
    def types(self) -> list[str]:
        return self.species_data["types"]

    @property
    def name(self) -> str:
        return self.species_data["name"]

    def has_item_removable(self) -> bool:
        """떨어뜨리기로 뺏을 수 있는 도구인지 (메가스톤은 불가)"""
        if not self.item_id:
            return False
        item = self.data.items.get(self.item_id, {})
        return not item.get("megaStone")


@dataclass
class Field:
    """날씨와 방어하는 쪽의 벽."""

    weather: Optional[str] = None       # sun / rain / sand / snow
    reflect: bool = False               # 방어 측 리플렉터
    light_screen: bool = False          # 방어 측 빛의장막
    aurora_veil: bool = False           # 방어 측 오로라베일


_WEATHER_ALIASES = {
    "sun": "sun", "sunnyday": "sun", "desolateland": "sun",
    "rain": "rain", "raindance": "rain", "primordialsea": "rain",
    "sand": "sand", "sandstorm": "sand",
    "snow": "snow", "snowscape": "snow", "hail": "snow",
}


@dataclass
class DamageResult:
    attacker: str
    defender: str
    move: str
    rolls: list[int]                    # 16개 난수별 데미지 (작은 값 → 큰 값)
    defender_hp: int                    # 방어 측 현재 HP
    defender_max_hp: int
    hits: int = 1                       # 연속기 타수 (rolls는 1타 기준)
    notes: list[str] = field(default_factory=list)

    @property
    def min(self) -> int:
        return min(self.rolls) * self.hits if self.rolls else 0

    @property
    def max(self) -> int:
        return max(self.rolls) * self.hits if self.rolls else 0

    @property
    def min_percent(self) -> float:
        return round(100 * self.min / self.defender_max_hp, 1)

    @property
    def max_percent(self) -> float:
        return round(100 * self.max / self.defender_max_hp, 1)

    def ko_chance(self, n_hits: int = 1) -> float:
        """n번 맞혔을 때 현재 HP를 다 깎을 확률 (회복·급소 무시, 난수 16가지 균등)."""
        if not self.rolls:
            return 0.0
        per_use = self.rolls
        dist = {0: 1.0}
        for _ in range(n_hits * self.hits):
            nxt: dict[int, float] = {}
            for total, p in dist.items():
                for r in per_use:
                    nxt[total + r] = nxt.get(total + r, 0.0) + p / len(per_use)
            dist = nxt
        return sum(p for total, p in dist.items() if total >= self.defender_hp)

    def summary(self) -> str:
        if not self.rolls:
            return f"{self.attacker}의 {self.move} → {self.defender}: 데미지 없음"
        ko1 = self.ko_chance(1)
        ko_text = "확정 1타" if ko1 >= 1 else (f"1타 확률 {ko1:.0%}" if ko1 > 0 else
                  ("확정 2타" if self.ko_chance(2) >= 1 else f"2타 확률 {self.ko_chance(2):.0%}"))
        return (f"{self.attacker}의 {self.move} → {self.defender}: "
                f"{self.min}~{self.max} ({self.min_percent}~{self.max_percent}%) / {ko_text}")


# ---------------------------------------------------------------------------
# 데미지 계산
# ---------------------------------------------------------------------------
_TYPE_BOOST_ITEMS = {
    "charcoal": "Fire", "mysticwater": "Water", "miracleseed": "Grass", "magnet": "Electric",
    "nevermeltice": "Ice", "blackbelt": "Fighting", "poisonbarb": "Poison", "softsand": "Ground",
    "sharpbeak": "Flying", "twistedspoon": "Psychic", "silverpowder": "Bug", "hardstone": "Rock",
    "spelltag": "Ghost", "dragonfang": "Dragon", "blackglasses": "Dark", "metalcoat": "Steel",
    "silkscarf": "Normal", "fairyfeather": "Fairy",
}
_RESIST_BERRIES = {
    "occaberry": "Fire", "passhoberry": "Water", "wacanberry": "Electric", "rindoberry": "Grass",
    "yacheberry": "Ice", "chopleberry": "Fighting", "kebiaberry": "Poison", "shucaberry": "Ground",
    "cobaberry": "Flying", "payapaberry": "Psychic", "tangaberry": "Bug", "chartiberry": "Rock",
    "kasibberry": "Ghost", "habanberry": "Dragon", "colburberry": "Dark", "babiriberry": "Steel",
    "roseliberry": "Fairy", "chilanberry": "Normal",
}
_ATE_ABILITIES = {"aerilate": "Flying", "pixilate": "Fairy", "refrigerate": "Ice",
                  "galvanize": "Electric", "dragonize": "Dragon"}
_ABILITY_IMMUNITY = {
    "levitate": "Ground", "earthEater": "Ground", "eartheater": "Ground",
    "flashfire": "Fire", "wellbakedbody": "Fire",
    "waterabsorb": "Water", "stormdrain": "Water", "dryskin": "Water",
    "voltabsorb": "Electric", "lightningrod": "Electric", "motordrive": "Electric",
    "sapsipper": "Grass",
}
_PINCH_ABILITIES = {"blaze": "Fire", "torrent": "Water", "overgrow": "Grass", "swarm": "Bug"}

# 위력 계산식이 따로 있는 기술 중 구현한 것
_SUPPORTED_BP_CALLBACKS = {
    "hex", "acrobatics", "eruption", "waterspout", "dragonenergy", "storedpower", "powertrip",
    "heavyslam", "heatcrash", "lowkick", "grassknot", "gyroball", "electroball", "reversal", "flail",
    "hardpress", "tripleaxel", "triplekick",
}
# 계산에 영향을 주지만 아직 구현하지 않은 특성 (근사값 경고용)
_UNSUPPORTED_ABILITIES = {
    "analytic", "rivalry", "flareboost", "toxicboost", "sheerforce_secondary_flag_check",
    "parentalbond", "skilllink", "stakeout", "neuroforce", "megasol", "protosynthesis", "quarkdrive",
    "wonderguard", "dauntlessshield", "intrepidsword",
}


def _weight_bp(target_kg: float) -> int:
    for limit, bp in ((200, 120), (100, 100), (50, 80), (25, 60), (10, 40)):
        if target_kg >= limit:
            return bp
    return 20


def _effective_speed(mon: Mon, weather: Optional[str]) -> int:
    spe = boosted(mon.stats["spe"], mon.boosts.get("spe", 0))
    mods = []
    a, it = mon.ability_id, mon.item_id
    if (a == "swiftswim" and weather == "rain") or (a == "chlorophyll" and weather == "sun") or \
            (a == "sandrush" and weather == "sand") or (a == "slushrush" and weather == "snow"):
        mods.append(X2)
    if it == "choicescarf":
        mods.append(X1_5)
    if mon.status == "par" and a != "quickfeet":
        mods.append(X0_5)
    return apply_mod(spe, chain(mods)) if mods else spe


def effective_speed(mon: Mon, fld: Optional[Field] = None) -> int:
    """실제 행동 순서 비교에 쓰는 스피드 (랭크·마비·스카프·날씨 특성 반영)."""
    weather = _WEATHER_ALIASES.get(to_id(fld.weather)) if fld and fld.weather else None
    return _effective_speed(mon, weather)


def calc_damage(attacker: Mon, defender: Mon, move_name: str, fld: Optional[Field] = None,
                crit: bool = False) -> DamageResult:
    """attacker가 defender에게 move를 썼을 때의 데미지 16가지를 계산한다."""
    data = attacker.data
    fld = fld or Field()
    weather = _WEATHER_ALIASES.get(to_id(fld.weather)) if fld.weather else None
    # 구름제거·에어록 같은 날씨 무효 특성
    if {attacker.ability_id, defender.ability_id} & {"cloudnine", "airlock"}:
        weather = None
    move = data.get_move(move_name)
    mid = move["id"]
    notes: list[str] = []
    res = DamageResult(attacker.name, defender.name, move["name"], [], defender.hp, defender.max_hp, 1, notes)

    category = move["category"]
    if category == "Status":
        notes.append("변화기")
        return res
    a_ab, d_ab = attacker.ability_id, defender.ability_id
    if crit and d_ab in {"battlearmor", "shellarmor"} and a_ab not in {"moldbreaker", "teravolt", "turboblaze"}:
        crit = False
        notes.append("급소 무효 (특성)")
    if d_ab == "disguise" and defender.species_data["name"] == "Mimikyu":
        if crit:
            crit = False
        notes.append("탈(Disguise)이 남아 있으면 첫 공격은 데미지 0 — 아래 수치는 탈이 벗겨진 뒤 기준")
    for unsupported in (a_ab, d_ab):
        if unsupported in _UNSUPPORTED_ABILITIES:
            notes.append(f"미지원 특성: {unsupported} (근사값)")

    # ---- 기술 타입 결정 ----
    move_type = move["type"]
    ate_boost = False
    if a_ab in _ATE_ABILITIES and move_type == "Normal" and mid not in {
            "judgment", "multiattack", "naturalgift", "revelationdance", "technoblast", "terrainpulse", "weatherball"}:
        move_type, ate_boost = _ATE_ABILITIES[a_ab], True
    if mid == "weatherball" and weather:
        move_type = {"sun": "Fire", "rain": "Water", "sand": "Rock", "snow": "Ice"}[weather]
    if mid == "ragingbull":
        move_type = {"Tauros-Paldea-Combat": "Fighting", "Tauros-Paldea-Blaze": "Fire",
                     "Tauros-Paldea-Aqua": "Water"}.get(attacker.species_data["name"], move_type)
    if a_ab == "liquidvoice" and "sound" in move.get("flags", []):
        move_type = "Water"
    if a_ab == "normalize" and mid not in {"hiddenpower", "weatherball", "naturalgift", "technoblast", "judgment", "multiattack", "terrainpulse"}:
        ate_boost = move_type != "Normal" or ate_boost
        move_type = "Normal"

    # 킬가르도: 공격할 때는 블레이드폼 능력치로 계산 (배틀스위치)
    if a_ab == "stancechange" and attacker.species_data["name"] == "Aegislash":
        attacker = Mon("Aegislash-Blade", sp=attacker.sp, nature=attacker.nature, ability=attacker.ability,
                       item=attacker.item, boosts=attacker.boosts, status=attacker.status,
                       hp=attacker.hp, fainted_allies=attacker.fainted_allies, data=data)

    # ---- 면역 ----
    eff = data.effectiveness(move_type, defender.types)
    if mid == "freezedry" and "Water" in defender.types:
        eff = eff / data.typechart["Ice"]["Water"] * 2
    if eff == 0 and a_ab in {"scrappy", "mindseye"} and move_type in {"Normal", "Fighting"} and "Ghost" in defender.types:
        eff = data.effectiveness(move_type, [t for t in defender.types if t != "Ghost"])
    if eff == 0:
        notes.append("효과가 없음 (타입 면역)")
        return res
    if _ABILITY_IMMUNITY.get(d_ab) == move_type and a_ab not in {"moldbreaker", "teravolt", "turboblaze"}:
        notes.append(f"효과가 없음 (특성: {defender.ability})")
        return res
    if defender.item_id == "airballoon" and move_type == "Ground":
        notes.append("효과가 없음 (풍선)")
        return res

    # ---- 고정 데미지 기술 ----
    if mid in {"seismictoss", "nightshade"}:
        res.rolls = [LEVEL] * 16
        return res
    if mid in {"superfang", "ruination", "naturesmadness"}:
        res.rolls = [max(1, defender.hp // 2)] * 16
        return res
    if mid == "fling":
        notes.append("미지원: 내던지기 (도구에 따라 위력 결정)")
        return res
    if move.get("hasDamageCallback") or move.get("ohko") or move.get("damage"):
        notes.append(f"미지원: 고정 데미지 기술 {move['name']}")
        return res

    # ---- 위력 ----
    bp = move["basePower"]
    if move.get("hasBasePowerCallback"):
        if mid not in _SUPPORTED_BP_CALLBACKS:
            notes.append(f"미지원: 위력 변동 기술 {move['name']} (기본 위력으로 근사)")
        elif mid == "hex":
            bp = bp * 2 if defender.status else bp
        elif mid == "acrobatics":
            bp = bp * 2 if not attacker.item_id else bp
        elif mid in {"eruption", "waterspout", "dragonenergy"}:
            bp = bp * attacker.hp // attacker.max_hp
        elif mid in {"storedpower", "powertrip"}:
            bp = bp + 20 * sum(v for v in attacker.boosts.values() if v > 0)
        elif mid in {"heavyslam", "heatcrash"}:
            ratio = attacker.species_data["weightkg"] / max(defender.species_data["weightkg"], 0.1)
            bp = 120 if ratio >= 5 else 100 if ratio >= 4 else 80 if ratio >= 3 else 60 if ratio >= 2 else 40
        elif mid in {"lowkick", "grassknot"}:
            bp = _weight_bp(defender.species_data["weightkg"])
        elif mid == "gyroball":
            bp = min(150, 25 * _effective_speed(defender, weather) // max(1, _effective_speed(attacker, weather)) + 1)
        elif mid == "electroball":
            ratio = _effective_speed(attacker, weather) // max(1, _effective_speed(defender, weather))
            bp = 150 if ratio >= 4 else 120 if ratio >= 3 else 80 if ratio >= 2 else 60 if ratio >= 1 else 40
        elif mid == "hardpress":
            bp = ((100 * (100 * (defender.hp * 4096 // defender.max_hp)) + 2047) // 4096) // 100 or 1
        elif mid in {"tripleaxel", "triplekick"}:
            notes.append(f"{move['name']}: 1타 기준 (타마다 위력이 커짐)")
        elif mid in {"reversal", "flail"}:
            r = 48 * attacker.hp // attacker.max_hp
            bp = 200 if r <= 1 else 150 if r <= 4 else 100 if r <= 9 else 80 if r <= 16 else 40 if r <= 32 else 20
    if mid == "weatherball" and weather:
        bp *= 2
    if mid == "ficklebeam":
        notes.append("미지원: 변덕빔은 30% 확률로 위력 2배 (기본 위력 기준)")
    bp = max(1, bp)

    # 위력 보정 (onBasePower)
    bp_mods: list[int] = []
    flags = set(move.get("flags", []))
    has_secondary = bool(move.get("secondaries"))
    if a_ab == "technician" and bp <= 60:
        bp_mods.append(X1_5)
    if a_ab == "toughclaws" and "contact" in flags:
        bp_mods.append(frac(5325, 4096))
    if a_ab == "sharpness" and "slicing" in flags:
        bp_mods.append(X1_5)
    if a_ab == "strongjaw" and "bite" in flags:
        bp_mods.append(X1_5)
    if a_ab == "megalauncher" and "pulse" in flags:
        bp_mods.append(X1_5)
    if a_ab == "ironfist" and "punch" in flags:
        bp_mods.append(frac(4915, 4096))
    if a_ab == "reckless" and move.get("recoil"):
        bp_mods.append(frac(4915, 4096))
    if a_ab == "punkrock" and "sound" in flags:
        bp_mods.append(frac(5325, 4096))
    if a_ab == "sheerforce" and has_secondary:
        bp_mods.append(frac(5325, 4096))
    if a_ab == "sandforce" and weather == "sand" and move_type in {"Rock", "Ground", "Steel"}:
        bp_mods.append(frac(5325, 4096))
    if ate_boost:
        bp_mods.append(frac(4915, 4096))
    auras = {attacker.ability_id, defender.ability_id}
    if (move_type == "Fairy" and "fairyaura" in auras) or (move_type == "Dark" and "darkaura" in auras):
        bp_mods.append(3072 if "aurabreak" in auras else 5448)
    if mid in {"solarbeam", "solarblade"} and weather in {"rain", "sand", "snow"}:
        bp_mods.append(X0_5)
    if a_ab == "supremeoverlord" and attacker.fainted_allies:
        bp_mods.append([4096, 4506, 4915, 5325, 5734, 6144][min(5, attacker.fainted_allies)])
    if d_ab == "dryskin" and move_type == "Fire":
        bp_mods.append(frac(5, 4))
    if attacker.item_id in _TYPE_BOOST_ITEMS and _TYPE_BOOST_ITEMS[attacker.item_id] == move_type:
        bp_mods.append(frac(4915, 4096))
    if attacker.item_id == "punchingglove" and "punch" in flags:
        bp_mods.append(frac(4506, 4096))
    if mid == "knockoff" and defender.has_item_removable():
        bp_mods.append(X1_5)
    if mid == "facade" and attacker.status and attacker.status != "slp":
        bp_mods.append(X2)
    if mid == "brine" and defender.hp * 2 <= defender.max_hp:
        bp_mods.append(X2)
    if mid == "venoshock" and defender.status in {"psn", "tox"}:
        bp_mods.append(X2)
    if bp_mods:
        bp = max(1, apply_mod(bp, chain(bp_mods)))

    # ---- 공격·방어 실수치 ----
    physical = category == "Physical"
    atk_mon = defender if move.get("overrideOffensivePokemon") == "target" else attacker
    atk_stat = move.get("overrideOffensiveStat") or ("atk" if physical else "spa")
    def_stat = move.get("overrideDefensiveStat") or ("def" if physical else "spd")
    atk_boost = atk_mon.boosts.get(atk_stat, 0)
    def_boost = defender.boosts.get(def_stat, 0)
    if d_ab == "unaware":
        atk_boost = 0
    if a_ab == "unaware" or move.get("ignoreDefensive"):
        def_boost = 0
    if crit:
        atk_boost = max(0, atk_boost)
        def_boost = min(0, def_boost)
    attack = boosted(atk_mon.stats[atk_stat], atk_boost)
    defense = boosted(defender.stats[def_stat], def_boost)

    # 공격 보정 (ModifyAtk/SpA, 공격 측 + 방어 측 특성)
    stat_for_mods = "atk" if physical else "spa"
    a_mods: list[int] = []
    if stat_for_mods == "atk" and a_ab in {"hugepower", "purepower"}:
        a_mods.append(X2)
    if stat_for_mods == "atk" and a_ab == "guts" and attacker.status:
        a_mods.append(X1_5)
    if stat_for_mods == "atk" and a_ab == "hustle":
        a_mods.append(X1_5)
    if stat_for_mods == "spa" and a_ab == "solarpower" and weather == "sun":
        a_mods.append(X1_5)
    if a_ab in _PINCH_ABILITIES and _PINCH_ABILITIES[a_ab] == move_type and attacker.hp * 3 <= attacker.max_hp:
        a_mods.append(X1_5)
    if a_ab == "steelworker" and move_type == "Steel":
        a_mods.append(X1_5)
    if a_ab == "dragonsmaw" and move_type == "Dragon":
        a_mods.append(X1_5)
    if a_ab == "transistor" and move_type == "Electric":
        a_mods.append(frac(5325, 4096))
    if a_ab == "rockypayload" and move_type == "Rock":
        a_mods.append(X1_5)
    if a_ab == "firemane" and move_type == "Fire":
        a_mods.append(X1_5)
    if a_ab == "waterbubble" and move_type == "Water":
        a_mods.append(X2)
    if a_ab == "gorillatactics" and stat_for_mods == "atk":
        a_mods.append(X1_5)
    if attacker.item_id == "choiceband" and stat_for_mods == "atk":
        a_mods.append(X1_5)
    if attacker.item_id == "choicespecs" and stat_for_mods == "spa":
        a_mods.append(X1_5)
    if a_ab not in {"moldbreaker", "teravolt", "turboblaze"}:
        if d_ab == "thickfat" and move_type in {"Fire", "Ice"}:
            a_mods.append(X0_5)
        if d_ab in {"heatproof", "waterbubble"} and move_type == "Fire":
            a_mods.append(X0_5)
        if d_ab == "purifyingsalt" and move_type == "Ghost":
            a_mods.append(X0_5)
    if a_mods:
        attack = apply_mod(attack, chain(a_mods))

    # 방어 보정 (ModifyDef/SpD)
    d_mods: list[int] = []
    if weather == "sand" and def_stat == "spd" and "Rock" in defender.types:
        d_mods.append(X1_5)
    if weather == "snow" and def_stat == "def" and "Ice" in defender.types:
        d_mods.append(X1_5)
    if a_ab not in {"moldbreaker", "teravolt", "turboblaze"}:
        if d_ab == "furcoat" and def_stat == "def":
            d_mods.append(X2)
        if d_ab == "marvelscale" and def_stat == "def" and defender.status:
            d_mods.append(X1_5)
    if defender.item_id == "assaultvest" and def_stat == "spd":
        d_mods.append(X1_5)
    if d_mods:
        defense = apply_mod(defense, chain(d_mods))

    attack, defense = max(1, attack), max(1, defense)

    # ---- 기본 데미지 ----
    base = (((2 * LEVEL // 5 + 2) * bp * attack) // defense) // 50

    # ---- modifyDamage ----
    base += 2
    if weather == "sun" and d_ab != "utilityumbrella" and defender.item_id != "utilityumbrella":
        if move_type == "Fire":
            base = apply_mod(base, X1_5)
        elif move_type == "Water":
            base = apply_mod(base, X0_5)
    elif weather == "rain" and defender.item_id != "utilityumbrella":
        if move_type == "Water":
            base = apply_mod(base, X1_5)
        elif move_type == "Fire":
            base = apply_mod(base, X0_5)
    if crit:
        base = base * 3 // 2

    # STAB
    stab_types = set(attacker.types)
    is_stab = move_type in stab_types or a_ab in {"protean", "libero"}
    stab = X1_5 if is_stab else 4096
    if is_stab and a_ab == "adaptability":
        stab = X2

    # 상성 단계 (+1 = 2배, -1 = 0.5배)
    type_mod = 0
    m = eff
    while m >= 2:
        type_mod += 1
        m /= 2
    while 0 < m <= 0.5:
        type_mod -= 1
        m *= 2

    # 최종 보정 (ModifyDamage: 공격 측 도구·특성, 방어 측 특성·열매, 벽)
    final_mods: list[int] = []
    if attacker.item_id == "lifeorb":
        final_mods.append(frac(5324, 4096))
    if attacker.item_id == "expertbelt" and type_mod > 0:
        final_mods.append(frac(4915, 4096))
    if a_ab == "tintedlens" and type_mod < 0:
        final_mods.append(X2)
    if a_ab == "sniper" and crit:
        final_mods.append(X1_5)
    if a_ab not in {"moldbreaker", "teravolt", "turboblaze"}:
        if d_ab in {"multiscale", "shadowshield"} and defender.hp >= defender.max_hp:
            final_mods.append(X0_5)
        if d_ab in {"filter", "solidrock", "prismarmor"} and type_mod > 0:
            final_mods.append(X0_75)
        if d_ab == "fluffy":
            if "contact" in flags:
                final_mods.append(X0_5)
            if move_type == "Fire":
                final_mods.append(X2)
        if d_ab == "auraguard" and "contact" in flags:
            final_mods.append(X0_5)
        if d_ab == "icescales" and category == "Special":
            final_mods.append(X0_5)
        if d_ab == "punkrock" and "sound" in flags:
            final_mods.append(X0_5)
    berry = _RESIST_BERRIES.get(defender.item_id)
    if berry == move_type and (type_mod > 0 or berry == "Normal"):
        final_mods.append(X0_5)
    screen = (fld.aurora_veil or (fld.reflect and physical) or (fld.light_screen and not physical))
    if screen and not crit and a_ab != "infiltrator":
        final_mods.append(X0_5)
    final_mod = chain(final_mods) if final_mods else 4096

    burned = attacker.status == "brn" and physical and a_ab != "guts" and mid != "facade"

    rolls = []
    for r in range(16):
        d = (base * (100 - r)) // 100
        d = apply_mod(d, stab)
        for _ in range(type_mod):
            d *= 2
        for _ in range(-type_mod):
            d //= 2
        if burned:
            d = apply_mod(d, X0_5)
        d = apply_mod(d, final_mod)
        d = max(1, d) & 0xFFFF
        rolls.append(d)
    res.rolls = sorted(rolls)

    multihit = move.get("multihit")
    if isinstance(multihit, int):
        res.hits = multihit
    elif isinstance(multihit, list):
        res.hits = multihit[1] if a_ab == "skilllink" else multihit[0]
        notes.append(f"연속기: {multihit[0]}~{multihit[1]}타 (최소 타수 {res.hits}로 계산)")
    return res


if __name__ == "__main__":
    # 간단 사용 예: python src/env/champions_calc.py
    chomp = Mon("Garchomp-Mega", sp={"atk": 32, "spe": 32, "hp": 2}, nature="Jolly")
    pex = Mon("Toxapex", sp={"hp": 32, "def": 32, "spd": 2}, nature="Bold", item="Rocky Helmet")
    print(chomp.name, chomp.stats)
    print(pex.name, pex.stats)
    print(calc_damage(chomp, pex, "Earthquake").summary())
    print(calc_damage(chomp, pex, "Earthquake", Field(weather="sand")).summary())
