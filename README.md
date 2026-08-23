# harness — AI 에이전트 하네스 학습 레포

LLM에 손(도구)·기억(컨텍스트)·판단(검증)을 붙여 **실제로 일하는 에이전트**로 만드는 층,
즉 **하네스(harness)** 를 바닥부터 쌓아 올리며 배우는 자료입니다.

API 키 없이 전부 실행됩니다. 가짜 모델(`FakeModel`)이 정해진 시나리오를 뱉어주므로
비용·네트워크 없이 하네스의 '배관'만 따로 관찰할 수 있습니다.

## 무엇부터 볼까

| 파일 | 용도 |
|---|---|
| **[mini-harness/LEARN.md](mini-harness/LEARN.md)** | **학습 노트.** 개념이 처음이면 여기부터. 용어집·플래시카드·확인문제 포함 |
| [mini-harness/README.md](mini-harness/README.md) | 원본 상세 가이드 (17장 구성, 실습과제 10개) |

## 바로 실행

```bash
cd mini-harness

python3 level0_bare.py         # 하네스 없음 — 모델은 아무것도 못 한다
python3 level1_loop.py         # 루프 + 읽는 손 하나  ← 하네스의 심장
python3 level2_agent.py        # 쓰는 손 + 샌드박스 + 검증 게이트
python3 level3_production.py   # 압축 · 예산 · 재시도 · 서브에이전트
python3 validate.py            # 하네스를 검증하는 하네스
```

추가 설치 없습니다(표준 라이브러리만, Python 3.9+).
실제 모델을 붙일 때만 `pip install anthropic` + `ANTHROPIC_API_KEY` 가 필요합니다.

```bash
python3 level2_agent.py --real
```

## 레벨 구성

```
Level 0  하네스 없음        모델을 한 번 부르고 끝. 파일을 볼 수 없다.
   ↓
Level 1  + 루프, 읽는 손    모델이 "이 도구 불러줘" → 하네스가 실행 → 결과를 되먹임
   ↓
Level 2  + 쓰는 손, 검증    "다 했다"는 말을 믿지 않고 하네스가 직접 테스트를 돌린다
   ↓
Level 3  + 실무 장치        컨텍스트 압축 / 예산 상한 / 재시도 / 서브에이전트 / 트레이스
```

## 핵심 문장 하나

> 모델은 "무엇을 할지" 말만 하고, **실제 실행은 전부 하네스가 한다.**
> 그리고 모델의 "다 했습니다"는 기계적으로 검증된다.

## 구성

```
mini-harness/
├── LEARN.md               학습 노트 (초심자용)
├── README.md              원본 상세 가이드
├── model.py               모델 어댑터 (가짜/진짜 공통 인터페이스)
├── tools.py               도구 + 샌드박스
├── level0_bare.py         하네스 없음
├── level1_loop.py         루프의 탄생
├── level2_agent.py        검증 게이트
├── level3_production.py   압축·예산·재시도·서브에이전트
├── validate.py            자체 검증 (검사 13종)
└── demo_project/          에이전트가 고칠 대상 (원본은 항상 '버그 있는 상태')
```
