"""
LEVEL 1 — 도구 1개 + 루프
================================================================
하네스의 심장인 while 문이 처음 등장한다. 핵심은 딱 이 4단계의 반복:

    1. 모델을 부른다
    2. 모델이 "이 도구 불러줘"라고 하면
    3. **하네스가** 실제로 실행한다        ← 모델이 하는 게 아니다
    4. 결과를 대화에 붙여서 다시 1번으로

실행:  python level1_loop.py

관찰 포인트
-----------
Level 0과 모델은 똑같다. 달라진 건 read_file 이라는 손 하나와
루프 하나뿐인데, 모델이 실제 파일 내용을 근거로 답하기 시작한다.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from model import FakeModel, Reply, ToolCall, assistant_message, tool_result_message

ROOT = Path(__file__).parent


# ──────────────────────────────────────────────────────────────
# 도구 = (모델에게 보여줄 설명) + (실제로 실행할 파이썬 함수)
# ──────────────────────────────────────────────────────────────
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable

    @property
    def schema(self):
        """모델에게 넘기는 부분. 여기 설명이 부실하면 모델은 도구를 잘못 쓴다."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def _read(path: str) -> str:
    # ⚠️ 이 도구에는 샌드박스가 없다. pathlib 은 오른쪽 인자가 절대경로면
    #    그쪽이 이기므로 read_file("/etc/passwd") 가 그냥 된다.
    #    "손을 달아주는 것"만 보여주는 단계이고, 격리는 Level 2에서 붙인다.
    text = (ROOT / path).read_text(encoding="utf-8")
    # 빈 문자열을 tool_result 로 돌려주면 실제 API가 거절한다("non-empty content").
    return text or "(빈 파일)"


read_file = Tool(
    name="read_file",
    description="프로젝트 안의 텍스트 파일을 읽어 내용을 반환한다.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "프로젝트 기준 상대 경로"}},
        "required": ["path"],
    },
    run=_read,
)

TOOLS = {t.name: t for t in [read_file]}


# ──────────────────────────────────────────────────────────────
# 에이전트 루프 — 이 20줄이 '챗봇'과 '에이전트'를 가른다
# ──────────────────────────────────────────────────────────────
def run(model, task: str, max_steps: int = 10):
    messages = [{"role": "user", "content": task}]

    for step in range(1, max_steps + 1):
        reply = model.call(messages, tools=list(TOOLS.values()))
        messages.append(assistant_message(reply))

        if reply.text:
            print(f"\n[{step}] 모델: {reply.text}")

        if not reply.wants_tools:
            print("\n모델이 더 할 일이 없다고 했습니다. 루프 종료.")
            return messages

        results = []
        for call in reply.tool_calls:
            print(f"[{step}] 도구 실행: {call.name}({call.args})")
            tool = TOOLS.get(call.name)
            if tool is None:                            # 없는 도구도 '결과'로 알려준다
                results.append((call.id, f"없는 도구입니다: {call.name}", True))
                continue
            try:
                out = tool.run(**call.args)
                err = False
            except Exception as e:                      # 실패도 결과다. 모델에게 알려준다
                out, err = f"{type(e).__name__}: {e}", True
                print(f"      → 실패: {out}")
            results.append((call.id, str(out)[:4000], err))

        messages.append(tool_result_message(results))

    print(f"\n최대 스텝({max_steps}) 도달. 강제 종료.")
    return messages


# ──────────────────────────────────────────────────────────────
SCRIPT = [
    Reply(text="테스트 파일부터 보겠습니다.",
          tool_calls=[ToolCall("c1", "read_file", {"path": "demo_project/test_calc.py"})]),
    Reply(text="이제 구현 코드를 보겠습니다.",
          tool_calls=[ToolCall("c2", "read_file", {"path": "demo_project/calc.py"})]),
    Reply(text="없는 파일도 한번 읽어보겠습니다.",
          tool_calls=[ToolCall("c3", "read_file", {"path": "demo_project/nope.py"})]),
    Reply(text=(
        "원인을 찾았습니다. average() 는 빈 리스트에서 len()==0 이 되어 ZeroDivisionError,\n"
        "percent() 는 whole==0 에서 같은 문제가 납니다. 테스트는 두 경우 모두 0을 기대합니다.\n"
        "다만 저에게는 파일을 '쓰는' 도구가 없어서 실제로 고칠 수는 없습니다."
    )),
]

if __name__ == "__main__":
    run(FakeModel(SCRIPT), "demo_project 의 테스트가 왜 실패하는지 파일을 읽어서 알려줘.")
    print("\n실제로 바뀐 파일: 없음   ← 읽는 손만 있고 쓰는 손이 없다 → Level 2로")
