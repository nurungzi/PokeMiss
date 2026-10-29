# PokeMiss

> 포켓몬 챔피언스 랭크 배틀 복기 코치 AI — 강화학습(RL) + LLM

"왜 졌는지, 어느 턴에 무엇을 잘못 골랐는지" 모르는 플레이어를 위해, 경기 후 턴별 선택을 RL 에이전트가 평가하고 LLM이 이유를 설명해 주는 복기 서비스입니다.

## 핵심 기능 (계획)

- 배틀 로그 입력 → 상태 변환 → RL 평가(턴별 점수) → LLM 해설 → 복기 리포트
- 결과 화면: 턴별 타임라인, 실수 Top 3, 대안 행동, 종합 코멘트
- 경기 중 실시간 추천 없음 — 경기 후 복기만 제공

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| 배틀 환경 | Pokémon Showdown (로컬 서버), poke-env |
| 강화학습 | Stable-Baselines3, sb3-contrib (MaskablePPO), PyTorch |
| 데이터 분석 | pandas, scikit-learn (클러스터링), matplotlib / seaborn |
| 해설 | LLM (BYOK) + 템플릿 해설 |
| 데모 | Streamlit |

## 진행 상황

- [x] 기획 및 서비스 컨셉 정리
- [ ] 개발 환경 세팅 및 Showdown 연결 테스트
- [ ] 데이터 수집 · EDA · 클러스터링
- [ ] 휴리스틱 베이스라인 봇
- [ ] DQN / PPO 학습
- [ ] LLM 해설
- [ ] Streamlit 웹 데모

## 폴더 구조

```
PokeMiss/
├── data/          # raw(원본, 미업로드) / processed(가공 데이터)
├── notebooks/     # EDA · 실험 노트북
├── src/
│   ├── env/       # Showdown 연결, 상태·행동 변환
│   ├── agents/    # 휴리스틱 봇, DQN, PPO
│   ├── eval/      # 복기 평가
│   └── llm/       # 해설 생성
├── app/           # Streamlit 데모
└── models/        # 학습된 모델 (미업로드)
```

## 실행 방법

추후 작성 예정.
