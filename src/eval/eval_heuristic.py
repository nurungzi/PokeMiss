"""휴리스틱 봇 승률 측정 (로컬 Showdown 서버 필요).

사용법 (project 폴더, 서버 실행 중):
    python src/eval/eval_heuristic.py                 # 상대 3종 x 200판
    python src/eval/eval_heuristic.py --n 100 --opponents random

공정하게 비교하려고 판수의 절반은 A팀, 나머지 절반은 B팀을 휴리스틱 봇이 쓴다.
결과는 data/results/heuristic_eval.json에 저장된다.
"""
import argparse
import asyncio
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "env")]

from poke_env.player import MaxBasePowerPlayer, RandomPlayer, SimpleHeuristicsPlayer  # noqa: E402

from agents.heuristic import HeuristicPlayer  # noqa: E402
from teams import TEAM_A, TEAM_B  # noqa: E402

FORMAT = "gen9championsbssregmc"
OPPONENTS = {"random": RandomPlayer, "maxbp": MaxBasePowerPlayer, "simple": SimpleHeuristicsPlayer}
LABELS = {"random": "랜덤 봇", "maxbp": "최고 위력 봇", "simple": "poke-env 기본 휴리스틱 봇"}


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """승률 95% 신뢰구간 (윌슨 방식)."""
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


async def run_matchup(opp_key: str, n: int) -> dict:
    wins = 0
    played = 0
    for my_team, opp_team in ((TEAM_A, TEAM_B), (TEAM_B, TEAM_A)):
        bot = HeuristicPlayer(battle_format=FORMAT, team=my_team, max_concurrent_battles=10)
        opp = OPPONENTS[opp_key](battle_format=FORMAT, team=opp_team, max_concurrent_battles=10)
        await bot.battle_against(opp, n_battles=n // 2)
        wins += bot.n_won_battles
        played += bot.n_finished_battles
        fallbacks = sum(1 for ds in bot.decisions.values() for d in ds if d.action == "random")
        if fallbacks:
            print(f"  ⚠ 계산 오류로 무작위 선택한 턴: {fallbacks}")
    lo, hi = wilson(wins, played)
    return {"opponent": opp_key, "battles": played, "wins": wins, "win_rate": wins / max(1, played),
            "ci95": [round(lo, 3), round(hi, 3)]}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="상대별 판수 (짝수)")
    ap.add_argument("--opponents", default="random,maxbp,simple")
    args = ap.parse_args()
    results = []
    for key in args.opponents.split(","):
        t0 = time.time()
        r = await run_matchup(key.strip(), args.n)
        results.append(r)
        print(f"vs {LABELS[r['opponent']]:<22} {r['wins']:>4}/{r['battles']}  승률 {r['win_rate']:.1%}  "
              f"(95% 신뢰구간 {r['ci95'][0]:.1%}~{r['ci95'][1]:.1%}, {time.time() - t0:.0f}초)")
    out = ROOT / "data" / "results" / "heuristic_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"format": FORMAT, "date": datetime.now().isoformat(timespec="seconds"),
                               "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    asyncio.run(main())
