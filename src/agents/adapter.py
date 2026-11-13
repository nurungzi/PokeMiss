"""poke-env의 배틀 상태를 champions_calc 계산용 객체(Mon, Field)로 바꾸는 어댑터.

휴리스틱 봇, 이후 DQN 상태 벡터, 복기 평가가 모두 이 변환을 같이 쓴다.

상대 포켓몬은 능력치를 알 수 없으므로 아래처럼 가정한다.
- 능력 포인트: HP 32 + 더 높은 공격 능력치(공격/특수공격) 32 + 스피드 2, 성격 보정 없음
- 특성: 드러났으면 그것, 아니면 첫 번째 특성
- 도구: 드러났으면 그것, 아니면 없음 (메가진화형은 메가스톤)
- 스피드는 범위(최소~최대)로 따로 다룬다 → speed_range()
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from env.champions_calc import (  # noqa: E402
    ChampionsData, Field, Mon, calc_stat, default_data, infer_spread, to_id,
)

_STATUS = {"BRN": "brn", "PAR": "par", "PSN": "psn", "TOX": "tox", "SLP": "slp", "FRZ": "frz"}
_UNKNOWN_ITEMS = {"", "unknownitem", "unknown_item", None}


def species_id(pokemon, data: ChampionsData) -> str:
    """poke-env 종 ID를 챔피언스 데이터 ID로 맞춘다. 없으면 기본 종으로 대체.

    주의: poke-env는 상대가 메가진화해도 species를 기본 종으로 남겨 두고 종족값만 바꾼다.
    그래서 종족값을 비교해 메가 폼을 찾아낸다.
    """
    sid = to_id(pokemon.species)
    if sid in data.species:
        bs = getattr(pokemon, "base_stats", None)
        if bs and dict(bs) != data.species[sid]["baseStats"]:
            name = data.species[sid]["name"]
            for fid, f in data.species.items():
                if f["baseSpecies"] == name and f["isMega"] and f["baseStats"] == dict(bs):
                    return fid
        return sid
    base = to_id(getattr(pokemon, "base_species", "") or "")
    if base in data.species:
        return base
    raise KeyError(f"챔피언스 데이터에 없는 포켓몬: {pokemon.species}")


def _status(pokemon) -> Optional[str]:
    st = getattr(pokemon, "status", None)
    if st is None:
        return None
    return _STATUS.get(st.name)


def _boosts(pokemon) -> dict:
    return {k: v for k, v in (pokemon.boosts or {}).items() if k in ("atk", "def", "spa", "spd", "spe") and v}


def mega_species_for(pokemon, data: ChampionsData) -> Optional[str]:
    """지닌 메가스톤으로 메가진화하면 될 종 ID (없으면 None)."""
    item = to_id(pokemon.item)
    if item in _UNKNOWN_ITEMS:
        return None
    stone = data.items.get(item, {}).get("megaStone")
    if not stone:
        return None
    base_name = data.species.get(species_id(pokemon, data), {}).get("name")
    target = stone.get(base_name) if isinstance(stone, dict) else None
    return to_id(target) if target and to_id(target) in data.species else None


def own_mon(pokemon, data: Optional[ChampionsData] = None, as_mega: bool = False) -> Mon:
    """내 포켓몬: 서버가 알려 준 실제 능력치를 그대로 쓴다."""
    data = data or default_data()
    sid = species_id(pokemon, data)
    stats = {k: v for k, v in (pokemon.stats or {}).items() if v is not None}
    stats["hp"] = pokemon.max_hp
    mega = mega_species_for(pokemon, data) if as_mega else None
    if mega:
        # 메가진화 전 능력치에서 능력 포인트·성격을 추정 → 메가 종족값으로 다시 계산
        sp, nature = infer_spread(sid, stats, data)
        base = data.species[mega]["baseStats"]
        new_stats = {s: calc_stat(s, base[s], sp.get(s, 0), nature, data) for s in base}
        new_stats["hp"] = stats["hp"]  # HP는 메가진화로 바뀌지 않음
        return Mon(mega, ability=data.species[mega]["abilities"][0], item=pokemon.item,
                   boosts=_boosts(pokemon), status=_status(pokemon), hp=pokemon.current_hp,
                   stats_override=new_stats, data=data)
    ability = pokemon.ability if pokemon.ability else None
    return Mon(sid, ability=ability, item=pokemon.item or None, boosts=_boosts(pokemon),
               status=_status(pokemon), hp=pokemon.current_hp, stats_override=stats, data=data)


def assumed_sp(species: str, data: ChampionsData) -> dict:
    base = data.species[species]["baseStats"]
    offense = "atk" if base["atk"] >= base["spa"] else "spa"
    return {"hp": 32, offense: 32, "spe": 2}


def opponent_mon(pokemon, data: Optional[ChampionsData] = None) -> Mon:
    """상대 포켓몬: 드러난 정보 + 가정값."""
    data = data or default_data()
    sid = species_id(pokemon, data)
    sp = assumed_sp(sid, data)
    abilities = data.species[sid]["abilities"]
    ability = pokemon.ability if pokemon.ability and to_id(pokemon.ability) in data.abilities else abilities[0]
    item = pokemon.item if to_id(pokemon.item) not in _UNKNOWN_ITEMS else None
    if item is None and data.species[sid].get("requiredItem"):
        item = data.species[sid]["requiredItem"]
    mon = Mon(sid, sp=sp, ability=ability, item=item, boosts=_boosts(pokemon), status=_status(pokemon), data=data)
    frac = pokemon.current_hp_fraction if pokemon.max_hp else 1.0
    mon.hp = max(0, round(mon.max_hp * frac))
    return mon


def speed_range(species: str, data: Optional[ChampionsData] = None) -> tuple[int, int]:
    """상대 스피드 실수치의 가능한 범위 (포인트 0·하락 성격 ~ 포인트 32·상승 성격)."""
    data = data or default_data()
    base = data.species[to_id(species)]["baseStats"]["spe"]
    lo = ((base + 20) * 90) // 100
    hi = ((base + 32 + 20) * 110) // 100
    return lo, hi


def field_for(battle, defender_is_opponent: bool) -> Field:
    """날씨 + 방어하는 쪽의 벽."""
    weather = None
    for w in (battle.weather or {}):
        weather = w.name.lower()
    sides = battle.opponent_side_conditions if defender_is_opponent else battle.side_conditions
    names = {sc.name for sc in (sides or {})}
    return Field(weather=weather, reflect="REFLECT" in names, light_screen="LIGHT_SCREEN" in names,
                 aurora_veil="AURORA_VEIL" in names)
