"""BSS Reg M-C(6마리 중 3마리 선출 싱글) 연결 테스트.

확인할 것:
1. 팀이 서버 검증(validator)을 통과하는지
2. 팀 프리뷰(선출) 단계가 정상 진행되는지
3. 메가진화 포켓몬(가브리아스·망나뇽)이 나와도 poke-env가 오류 없이 처리하는지
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poke_env.player import RandomPlayer  # noqa: E402
from teams import TEAM_A, TEAM_B  # noqa: E402

FORMAT = sys.argv[1] if len(sys.argv) > 1 else "gen9championsbssregmc"
N_BATTLES = int(sys.argv[2]) if len(sys.argv) > 2 else 10


async def main():
    p1 = RandomPlayer(battle_format=FORMAT, team=TEAM_A)
    p2 = RandomPlayer(battle_format=FORMAT, team=TEAM_B)
    await p1.battle_against(p2, n_battles=N_BATTLES)
    print(f"[{FORMAT}] 완료: {p1.n_finished_battles}판, A팀 승리: {p1.n_won_battles}판")


asyncio.run(main())
