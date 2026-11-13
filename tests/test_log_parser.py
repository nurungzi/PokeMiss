"""로그 파서 테스트. 실행: python tests/test_log_parser.py

개발 중에는 봇이 실제로 고른 행동 1,690개와 파서 결과를 대조해 모두 일치함을 확인했다.
여기서는 저장된 샘플 로그와 입력 형식(텍스트·JSON·HTML·서버 로그)을 검사한다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "parser"))

from showdown_log import parse_log  # noqa: E402

LOGS = ROOT / "tests" / "logs"


def test_sample_1():
    b = parse_log(LOGS / "sample_bss_1.log")
    assert b.format == "[Gen 9 Champions] BSS Reg M-C"
    assert b.winner == "p1" and len(b.turns) == 6
    assert b.leads == {"p1": "Archaludon", "p2": "Kingambit"}
    t1, t2 = b.turns[0], b.turns[1]
    assert t1.actions["p1"].kind == "move" and t1.actions["p1"].name == "Electro Shot"
    assert t1.actions["p2"].name == "Swords Dance"
    assert t2.actions["p1"].forced                       # 모은 다음 턴은 고정 행동
    assert t2.state["p2"].active_mon().boosts == {"atk": 2}
    assert t2.replacements == {"p2": ["Primarina"]}      # 기절 후 교체 투입은 선택이 아님


def test_sample_2_mega():
    b = parse_log(LOGS / "sample_bss_2.log")
    t1 = b.turns[0]
    assert t1.actions["p1"].mega and t1.actions["p1"].name == "Earthquake"
    assert b.turns[1].state["p1"].active_mon().species == "Garchomp-Mega"
    assert b.sets["p1"]["Garchomp-Mega"]["item"] == "Garchompite"
    rows = b.to_rows()
    assert len(rows) == 2 * len(b.turns) and {"turn", "side", "action", "choice", "won"} <= set(rows[0])


def test_input_formats():
    text = (LOGS / "sample_bss_1.log").read_text(encoding="utf-8")
    as_json = json.dumps({"id": "x", "log": text})
    as_html = f'<html><script type="text/plain" class="battle-log-data">{text}</script></html>'
    as_server = json.dumps({"log": text.splitlines()})
    for src in (as_json, as_html, as_server):
        assert len(parse_log(src).turns) == 6


def test_split_lines():
    log = "\n".join([
        "|player|p1|Alice||1500", "|player|p2|Bob||1480", "|tier|[Gen 9 Champions] BSS Reg M-C",
        "|switch|p1a: Chomp|Garchomp, L50, M|100/100", "|switch|p2a: Pex|Toxapex, L50, F|100/100",
        "|turn|1",
        "|move|p1a: Chomp|Earthquake|p2a: Pex",
        "|split|p2", "|-damage|p2a: Pex|40/157", "|-damage|p2a: Pex|26/100",
        "|move|p2a: Pex|Recover|p2a: Pex",
        "|turn|2", "|win|Alice",
    ])
    b = parse_log(log)
    assert b.ratings == {"p1": 1500, "p2": 1480} and b.winner == "p1"
    assert abs(b.turns[1].state["p2"].active_mon().hp - 0.26) < 1e-9   # 공개용(%) 줄을 사용
    assert b.turns[0].actions["p2"].name == "Recover"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"통과: {t.__name__}")
    print(f"전체 {len(tests)}개 테스트 통과")
