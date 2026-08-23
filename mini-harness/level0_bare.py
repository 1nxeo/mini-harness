"""
LEVEL 0 — 하네스 없음
================================================================
모델을 그냥 한 번 부른다. 이게 전부다.

실행:  python level0_bare.py            (가짜 모델 — 키 없이 실행됨)
       python level0_bare.py --real     (실제 API — ANTHROPIC_API_KEY 필요)

관찰 포인트
-----------
모델은 demo_project/calc.py 를 '볼 수 없다'.
그래서 코드를 보지 않은 채로 그럴듯한 일반론을 뱉는다.
이게 "손이 없는" 상태다.
"""

import sys

from model import AnthropicModel, FakeModel, Reply

TASK = "demo_project 의 테스트가 실패한다. 원인을 찾아서 고쳐줘."


def main():
    if "--real" in sys.argv:
        model = AnthropicModel(system="당신은 파이썬 개발자입니다.")
    else:
        model = FakeModel([
            Reply(text=(
                "테스트 실패의 흔한 원인은 다음과 같습니다:\n"
                "1. ZeroDivisionError — 0으로 나누는 경우\n"
                "2. import 경로 문제\n"
                "3. 부동소수점 비교 오차\n"
                "코드를 붙여주시면 더 정확히 봐드릴 수 있습니다."
            ))
        ])

    reply = model.call([{"role": "user", "content": TASK}], tools=[])

    print("=" * 60)
    print("요청:", TASK)
    print("=" * 60)
    print(reply.text)
    print("=" * 60)
    print("모델 호출 횟수: 1")
    print("실제로 바뀐 파일: 없음   ← 손이 없으니 아무것도 못 한다")


if __name__ == "__main__":
    main()
