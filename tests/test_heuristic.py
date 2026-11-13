"""휴리스틱 판단 로직 단위 테스트 (서버 없이 실행).

실행: python tests/test_heuristic.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "env")]

from agents.heuristic import HeuristicBrain  # noqa: E402
from env.champions_calc import Field, Mon  # noqa: E402

brain = HeuristicBrain()
F = Field()


def best(me, opp, moves, known=()):
    scores = brain.score_moves(me, opp, moves, F, F, list(known), turn=1, opp_alive=3)
    return max(scores, key=scores.get), scores


def test_avoids_immune_move():
    chomp = Mon("Garchomp", sp={"atk": 32, "spe": 32}, nature="Jolly")
    rotom = Mon("Rotom-Wash", sp={"hp": 32, "spa": 32})
    choice, scores = best(chomp, rotom, ["earthquake", "dragonclaw", "rockslide", "swordsdance"])
    assert scores["earthquake"] == 0 or choice != "earthquake", scores


def test_picks_super_effective():
    chomp = Mon("Garchomp", sp={"atk": 32, "spe": 32}, nature="Jolly")
    gambit = Mon("Kingambit", sp={"hp": 32, "atk": 32})
    choice, scores = best(chomp, gambit, ["earthquake", "dragonclaw", "rockslide"])
    assert choice == "earthquake", scores


def test_no_setup_when_threatened():
    gambit = Mon("Kingambit", sp={"hp": 32, "atk": 32}, nature="Adamant")
    chomp = Mon("Garchomp", sp={"atk": 32, "spe": 32}, nature="Jolly")
    choice, scores = best(gambit, chomp, ["kowtowcleave", "suckerpunch", "ironhead", "swordsdance"], ["earthquake"])
    assert choice != "swordsdance", scores


def test_finishes_with_priority():
    gambit = Mon("Kingambit", sp={"hp": 32, "atk": 32}, nature="Adamant")
    vol = Mon("Volcarona", sp={"spa": 32, "spe": 32}, nature="Timid")
    vol.hp = 20
    choice, scores = best(gambit, vol, ["kowtowcleave", "suckerpunch", "ironhead"], ["fierydance"])
    assert choice == "suckerpunch", scores


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"통과: {t.__name__}")
    print(f"전체 {len(tests)}개 테스트 통과")
