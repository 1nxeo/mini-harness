"""
LEVEL 2 — 일하는 에이전트 (도구 4개 + 검증 게이트)
================================================================
Level 1에 세 가지가 추가된다.

  1) 쓰는 손      : write_file, run_bash  → 실제로 코드를 고칠 수 있다
  2) 샌드박스     : 임시 폴더 격리 + 위험 명령 차단
  3) 검증 게이트  : 모델이 "다 했다"고 해도 하네스가 직접 테스트를 돌려서 확인한다.
                    실패하면 그 실패 메시지를 그대로 되돌려 보내 다시 시킨다.

3번이 핵심이다. 모델의 자기 보고를 믿지 않고 기계적으로 확인하는 이 루프가
없으면 "고쳤습니다"라는 거짓 성공이 그대로 최종 결과가 된다.

실행:  python level2_agent.py            (가짜 모델)
       python level2_agent.py --real     (실제 API)
"""

import sys
from pathlib import Path

from model import AnthropicModel, FakeModel, Reply, ToolCall, assistant_message, tool_result_message
from tools import Sandbox, fresh_workdir, make_tools

SYSTEM = """당신은 파이썬 개발자입니다. 주어진 도구만으로 작업하세요.
규칙:
- 추측하지 말고 반드시 파일을 읽어서 확인하세요.
- 고치기 전에 먼저 `python3 -m unittest -q` 를 돌려 실패를 직접 확인하세요.
- 부분 수정에는 edit_file 을 쓰세요. write_file 은 파일 전체를 덮어쓰므로
  쓸 때는 반드시 완성된 전체 내용을 넘기세요.
- 수정 후 다시 테스트를 돌려 통과를 확인한 뒤에 마치세요."""

VERIFY_CMD = "python3 -m unittest -q"


def run_agent(model, task: str, workdir: Path, max_steps: int = 20, max_verify: int = 3):
    """
    전체 흐름 지도 (아래 [STEP n] 주석과 번호가 맞물린다)
    ------------------------------------------------------------
        [STEP 0] 준비: 샌드박스 + 도구 + 대화 기록 초기화
             │
             ▼
        ┌──> [STEP 1] 모델 호출 → 답변을 대화에 기록
        │         │
        │         ▼
        │    [STEP 2] 모델이 도구를 불렀나?
        │         │
        │         ├── 안 불렀다 (= "다 했어요" 선언)
        │         │      └─> [STEP 3] 검증 게이트: 하네스가 직접 테스트 실행
        │         │             ├─ 통과      → return True   (진짜 종료)
        │         │             ├─ 실패+한도  → return False  (포기)
        │         │             └─ 실패      → 에러를 대화에 붙이고 ─┐
        │         │                                                │
        │         └── 불렀다                                        │
        │                └─> [STEP 4] 하네스가 도구를 실제로 실행     │
        │                      └─> [STEP 5] 실행 결과를 대화에 붙임  │
        │                                                          │
        └──────────────────────────────────────────────────────────┘
                        (max_steps 를 넘기면 [STEP 6] 강제 종료)

    핵심: 모델은 "무엇을 할지" 말만 하고, 실제 실행은 전부 하네스가 한다.
         그리고 모델의 "다 했다"는 말은 [STEP 3]에서 기계적으로 검증된다.
    """
    # ── [STEP 0] 준비 ────────────────────────────────────────────
    sandbox = Sandbox(workdir)                      # 작업 폴더 밖 접근을 막는 관문
    tools = {t.name: t for t in make_tools(sandbox)}  # {"read_file": Tool, ...} 형태
    messages = [{"role": "user", "content": task}]  # 대화 기록. 매 호출마다 통째로 모델에 보낸다
    verify_rounds = 0                               # 검증에 실패한 '총' 횟수 (리셋하지 않음)
    step = 0                                        # 모델을 부른 횟수

    while step < max_steps:
        step += 1

        # ── [STEP 1] 모델 호출 ───────────────────────────────────
        #   지금까지의 대화 전체 + 쓸 수 있는 도구 목록을 넘긴다.
        #   모델은 "이 도구를 이 인자로 불러줘"라고 '요청'만 할 뿐,
        #   직접 실행하지는 못한다.
        reply = model.call(messages, tools=list(tools.values()))
        #   모델의 답변을 대화에 기록. 이걸 빼먹으면 모델은 자기가 방금
        #   무슨 도구를 불렀는지 다음 턴에 기억하지 못한다.
        messages.append(assistant_message(reply))

        if reply.text:
            print(f"\n\033[36m[{step}] 모델\033[0m {reply.text}")

        # ── [STEP 2] 갈림길: 모델이 도구를 불렀는가? ──────────────
        # ── 모델이 도구를 부르지 않았다 = 스스로 끝났다고 판단했다
        if not reply.wants_tools:
            # ── [STEP 3] 검증 게이트 ─────────────────────────────
            #   Level 1은 여기서 그냥 끝냈다("모델이 끝났다니 끝").
            #   Level 2는 그 말을 안 믿고 하네스가 직접 테스트를 돌린다.
            #   이 블록이 Level 2의 존재 이유다.

            # verify_rounds 는 일부러 리셋하지 않는다. '검증 실패 총 횟수'에
            # 하드 상한을 두는 편이, 도구를 조금씩 부르며 무한히 버티는
            # 패턴을 막는 데 안전하다.
            verify_rounds += 1
            ok, out = verify(sandbox)  # ← 실제로 `python3 -m unittest -q` 실행

            # (3-a) 통과 → 여기가 유일한 '성공 종료' 출구
            if ok:
                print(f"\n\033[32m✅ 검증 통과\033[0m (모델 호출 {step}회)")
                return True, messages

            # (3-b) 실패했는데 재시도 한도까지 썼다 → 포기하고 종료
            if verify_rounds >= max_verify:
                print(f"\n\033[31m❌ 검증 실패 — 재시도 한도({max_verify}) 초과\033[0m")
                return False, messages

            # (3-c) 실패 → 에러 메시지를 '사용자가 말한 것처럼' 대화에 붙이고
            #       루프 맨 위로 돌아간다. 모델은 다음 턴에 이 구체적인
            #       실패 출력을 보고 자기가 뭘 놓쳤는지 알아낸다.
            # 실패를 모델에게 그대로 되먹임한다. 이게 자기수정의 연료다.
            print(f"\n\033[33m⟳ 검증 실패 → 모델에게 되돌려 보냄 ({verify_rounds}/{max_verify})\033[0m")
            messages.append({"role": "user", "content":
                f"아직 테스트가 실패합니다. `{VERIFY_CMD}` 결과:\n\n{out}\n\n원인을 다시 찾아 고치세요."})
            continue  # → [STEP 1]로 되돌아감

        # ── [STEP 4] 도구 실행 ───────────────────────────────────
        #   여기 오면 모델이 도구를 부른 것이다. 한 턴에 여러 개를
        #   부를 수 있으므로 for 문으로 전부 처리한다.
        # ── 도구 실행
        results = []
        for call in reply.tool_calls:
            # 로그용 축약. 인자가 파일 전체 내용이면 너무 길어서 60자로 자른다.
            preview = {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                       for k, v in call.args.items()}
            print(f"\033[90m[{step}] 🔧 {call.name} {preview}\033[0m")

            # (4-a) 모델이 말한 이름의 도구를 찾는다
            # ★ 도구 조회와 도구 실행을 같은 try 에 넣으면 안 된다.
            #   도구 '안에서' 난 KeyError 가 "없는 도구입니다"로 오보된다.
            tool = tools.get(call.name)
            if tool is None:
                # 모델이 없는 도구를 지어냈다. 죽이지 않고 '결과'로 알려준다.
                out, err = f"없는 도구입니다: {call.name}. 사용 가능: {', '.join(tools)}", True
            else:
                # (4-b) 실제 실행. 여기서만 진짜 파일이 바뀌고 셸이 돈다.
                try:
                    out, err = str(tool.run(**call.args)), False
                except Exception as e:
                    # 실패도 '결과'다. 예외로 루프를 죽이지 않고 모델에게 넘겨
                    # 스스로 고쳐보게 한다. (샌드박스 차단도 여기로 들어온다)
                    out, err = f"{type(e).__name__}: {e}", True
            if err:
                print(f"\033[31m      ↳ {out}\033[0m")
            results.append((call.id, out[:6000], err))  # 결과는 잘라서 넣는다(컨텍스트 보호)

        # ── [STEP 5] 실행 결과를 대화에 붙인다 ───────────────────
        #   call.id 로 "어떤 요청에 대한 결과인지" 짝을 맞춘다.
        #   이걸 붙여야 모델이 다음 턴에서 도구 결과를 읽을 수 있다.
        messages.append(tool_result_message(results))
        # → while 문 맨 위 [STEP 1]로 되돌아간다

    # ── [STEP 6] 안전장치 ────────────────────────────────────────
    #   모델이 끝없이 도구만 부르며 맴돌 때 루프를 강제로 끊는다.
    #   하네스에 상한이 없으면 토큰과 시간이 무한히 새어 나간다.
    print(f"\n\033[31m❌ 최대 스텝({max_steps}) 초과\033[0m")
    return False, messages


def verify(sandbox: Sandbox):
    """하네스가 직접 돌리는 검증. 모델의 말이 아니라 종료 코드를 믿는다."""
    import subprocess
    try:
        r = subprocess.run(VERIFY_CMD, shell=True, cwd=sandbox.root,
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        # ★ 여기를 안 감싸면, 에이전트가 무한루프 테스트를 써넣는 순간
        #   하네스 자체가 죽는다. 검증기는 절대 예외를 밖으로 흘리지 않는다.
        return False, "검증 명령이 60초 안에 끝나지 않았습니다(무한 루프 의심)."
    return r.returncode == 0, (r.stdout + r.stderr).strip()


# ──────────────────────────────────────────────────────────────
# 가짜 모델 시나리오
#   일부러 '절반만 고치는' 실수를 넣었다. 검증 게이트가 그걸 잡아내고
#   두 번째 시도에서 완전히 고치는 과정을 볼 수 있다.
# ──────────────────────────────────────────────────────────────
HALF_FIX = '''"""에이전트가 고쳐야 할 대상 코드."""


def average(nums):
    if not nums:
        return 0
    return sum(nums) / len(nums)


def percent(part, whole):
    return part / whole * 100
'''

FULL_FIX = '''"""에이전트가 고쳐야 할 대상 코드."""


def average(nums):
    if not nums:
        return 0
    return sum(nums) / len(nums)


def percent(part, whole):
    if whole == 0:
        return 0
    return part / whole * 100
'''

SCRIPT = [
    Reply(text="먼저 폴더 구조를 봅니다.",
          tool_calls=[ToolCall("a1", "list_dir", {"path": "."})]),
    Reply(text="테스트를 돌려 실패를 직접 확인합니다.",
          tool_calls=[ToolCall("a2", "run_bash", {"command": "python3 -m unittest -q"})]),
    Reply(text="구현 코드를 읽습니다.",
          tool_calls=[ToolCall("a3", "grep", {"path": "calc.py", "pattern":"return"})]),
    Reply(text="average() 의 빈 리스트 처리를 추가합니다.",
          tool_calls=[ToolCall("a4", "write_file", {"path": "calc.py", "content": HALF_FIX})]),
    Reply(text="고쳤습니다. 완료했습니다."),          # ← 거짓 성공 선언. 검증 게이트가 잡는다.
    Reply(text="percent() 도 같은 문제였네요. 함께 고칩니다.",
          tool_calls=[ToolCall("a5", "write_file", {"path": "calc.py", "content": FULL_FIX})]),
    Reply(text="테스트를 다시 돌립니다.",
          tool_calls=[ToolCall("a6", "run_bash", {"command": "python3 -m unittest -q"})]),
    Reply(text="4개 테스트 모두 통과했습니다."),
    # 위험한 명령을 시도하는 경우도 넣어두면 샌드박스 동작을 볼 수 있다
]

TASK = "테스트가 실패합니다. 원인을 찾아 코드를 고치고, 테스트가 통과하는지 확인하세요."

if __name__ == "__main__":
    workdir = fresh_workdir(Path(__file__).parent / "demo_project")
    print(f"작업 폴더(임시 복사본): {workdir}\n원본 demo_project 는 건드리지 않습니다.")

    model = (AnthropicModel(system=SYSTEM) if "--real" in sys.argv else FakeModel(SCRIPT))
    ok, messages = run_agent(model, TASK, workdir)

    print(f"\n최종 결과: {'성공' if ok else '실패'}")
    print(f"대화에 쌓인 메시지 수: {len(messages)}   ← 이게 컨텍스트다. Level 3의 관리 대상.")
    print(f"고쳐진 파일 확인:  cat {workdir}/calc.py")
