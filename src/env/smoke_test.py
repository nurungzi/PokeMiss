import asyncio
from poke_env.player import RandomPlayer

async def main():
    p1 = RandomPlayer(battle_format="gen9randombattle")
    p2 = RandomPlayer(battle_format="gen9randombattle")
    await p1.battle_against(p2, n_battles=10)
    print(f"완료: {p1.n_finished_battles}판, P1 승리: {p1.n_won_battles}판")

asyncio.run(main())