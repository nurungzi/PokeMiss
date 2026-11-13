"""champions_calc 검증 테스트.

실행 (project 폴더에서):
    python tests/test_champions_calc.py
    (pytest가 있으면 python -m pytest tests 도 가능)

damage_vectors.json은 Showdown 시뮬레이터(champions 모드, 레벨 50)가 실제로 계산한
데미지 16가지를 무작위 상황 1,186개에 대해 저장한 것이다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "env"))

import champions_calc as cc  # noqa: E402

DATA = cc.ChampionsData(ROOT / "data" / "processed" / "champions")


def test_stat_formula():
    # 시뮬레이터에서 확인한 값
    pex = cc.calc_stats("Toxapex", {"hp": 32, "def": 32, "spd": 2}, "Bold", DATA)
    assert pex["hp"] == 157 and pex["def"] == 224
    chomp = cc.calc_stats("Garchomp", {"atk": 32, "spe": 32, "hp": 2}, "Jolly", DATA)
    assert chomp["atk"] == 182 and chomp["spe"] == 169


def test_stat_point_limits():
    for bad in ({"atk": 33}, {"atk": 32, "spe": 32, "hp": 3}):
        try:
            cc.calc_stats("Garchomp", bad, "Jolly", DATA)
        except ValueError:
            continue
        raise AssertionError(f"잘못된 능력 포인트가 통과됨: {bad}")


def test_boost_and_immunity():
    assert cc.boosted(100, 1) == 150 and cc.boosted(100, -1) == 66 and cc.boosted(100, 6) == 400
    chomp = cc.Mon("Garchomp", data=DATA)
    rotom = cc.Mon("Rotom-Wash", data=DATA)
    assert cc.calc_damage(chomp, rotom, "Earthquake").rolls == []        # 부유
    corv = cc.Mon("Corviknight", data=DATA)
    assert cc.calc_damage(chomp, corv, "Earthquake").rolls == []         # 비행 타입


def test_against_showdown_simulator():
    vectors = json.loads((ROOT / "tests" / "damage_vectors.json").read_text(encoding="utf-8"))["cases"]
    failures = []
    for i, c in enumerate(vectors):
        a = cc.Mon(data=DATA, **c["attacker"])
        d = cc.Mon(data=DATA, **c["defender"])
        got = cc.calc_damage(a, d, c["move"], cc.Field(**c["field"]), crit=c["crit"]).rolls
        if got != c["expected"]:
            failures.append((i, c["attacker"]["species"], c["move"], c["defender"]["species"], got[:2], c["expected"][:2]))
    assert not failures, f"{len(failures)}/{len(vectors)}건 불일치, 예: {failures[:5]}"
    print(f"  Showdown 시뮬레이터와 {len(vectors)}건 모두 일치")


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"통과: {t.__name__}")
    print(f"전체 {len(tests)}개 테스트 통과")
