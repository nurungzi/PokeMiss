import asyncio
import sys
from poke_env.player import RandomPlayer

FORMAT = sys.argv[1] if len(sys.argv) > 1 else "gen9championsrandombattle"

async def main():
    p1 = RandomPlayer(battle_format=FORMAT)
    p2 = RandomPlayer(battle_format=FORMAT)
    await p1.battle_against(p2, n_battles=10)
    print(f"[{FORMAT}] 완료: {p1.n_finished_battles}판, P1 승리: {p1.n_won_battles}판")

asyncio.run(main())