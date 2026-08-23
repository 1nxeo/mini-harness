"""
validate.py — 하네스 자체를 검증하는 하네스
================================================================
"검증 없는 하네스는 신뢰할 수 없다"는 원칙을 이 레포 자신에게 적용한다.
여기서 검사하는 것은 하네스가 만들어내는 **대화 히스토리가 실제 API에
유효한 모양인가**, 그리고 **샌드박스가 실제로 가두는가**다.

실행:  python3 validate.py
"""

import itertools
import subprocess
import sys
import tempfile
from pathlib import Path

from level3_production import Budget, Harness, est_tokens, make_subagent_tool, verify_tests
from level3_production import MAIN_SCRIPT, SUB_SCRIPT, TASK
from model import FakeModel, Reply, assistant_message, tool_result_message
from tools import Sandbox, fresh_workdir, make_tools

FAILS = []


def check(cond, label, detail=""):
    print(f"  {'✅' if cond else '❌'} {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def blocks(msg, t):
    c = msg.get("content")
    if not isinstance(c, list):
        return []
    return [b for b in c if isinstance(b, dict) and b.get("type") == t]


# ──────────────────────────────────────────────────────────────
def validate_history(messages, label):
    """실제 Anthropic API가 강제하는 불변식들을 확인한다."""
    ok = True

    if not messages or messages[0].get("role") != "user":
        check(False, f"{label}: 첫 메시지는 user")
        ok = False

    for i, m in enumerate(messages):
        c = m.get("content")
        if c == [] or c == "":
            check(False, f"{label}: 빈 content (idx {i})")
            ok = False
        for b in (c if isinstance(c, list) else []):
            if isinstance(b, dict) and b.get("type") == "tool_result" and not b.get("content"):
                check(False, f"{label}: 빈 tool_result content (idx {i})")
                ok = False

    # tool_use 하나하나는 '바로 다음' 메시지에 같은 id의 tool_result 가 있어야 한다
    for i, m in enumerate(messages):
        ids = {b["id"] for b in blocks(m, "tool_use")}
        if not ids:
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else {}
        answered = {b["tool_use_id"] for b in blocks(nxt, "tool_result")}
        if ids - answered:
            check(False, f"{label}: 응답 없는 tool_use {ids - answered} (idx {i})")
            ok = False

    # 고아 tool_result (짝인 tool_use 가 앞에 없음)
    for i, m in enumerate(messages):
        got = {b["tool_use_id"] for b in blocks(m, "tool_result")}
        if not got:
            continue
        prev = messages[i - 1] if i else {}
        avail = {b["id"] for b in blocks(prev, "tool_use")}
        if got - avail:
            check(False, f"{label}: 고아 tool_result {got - avail} (idx {i})")
            ok = False

    # 같은 역할이 연속되는 모양 (직접 API는 병합하지만 엄격한 검증기는 거절)
    for i in range(1, len(messages)):
        if messages[i].get("role") == messages[i - 1].get("role"):
            check(False, f"{label}: 연속 {messages[i]['role']} 턴 (idx {i})")
            ok = False

    if ok:
        check(True, f"{label}: 히스토리 {len(messages)}개 메시지 모두 유효")
    return ok


# ──────────────────────────────────────────────────────────────
print("\n[1] 샌드박스 격리 — 파일 도구")
root = Path(tempfile.mkdtemp()) / "proj"
root.mkdir(parents=True)
(root.parent / "proj_secret").mkdir()
(root.parent / "proj_secret" / "leak.txt").write_text("SECRET", encoding="utf-8")
(root / "ok.txt").write_text("fine", encoding="utf-8")
sb = Sandbox(root)

try:
    sb.resolve("ok.txt")
    check(True, "정상 경로는 통과")
except PermissionError:
    check(False, "정상 경로는 통과")

for bad in ["../proj_secret/leak.txt",      # ★ startswith 버그가 있으면 여기서 뚫린다
            "../../etc/passwd", "/etc/passwd", "sub/../../proj_secret/leak.txt"]:
    try:
        sb.resolve(bad)
        check(False, f"탈출 차단: {bad}", "통과되어 버렸다")
    except PermissionError:
        check(True, f"탈출 차단: {bad}")

print("\n[2] 위험 명령 차단 (deny-list, 근본적으로 불완전함을 전제)")
for cmd in ["rm -rf .", "rm --recursive --force x", "cat ../proj_secret/leak.txt",
            "find . -delete"]:
    try:
        sb.check_command(cmd)
        check(False, f"차단: {cmd!r}", "통과되어 버렸다")
    except PermissionError:
        check(True, f"차단: {cmd!r}")
try:
    sb.check_command("python3 -m unittest -q")
    check(True, "정상 명령은 통과")
except PermissionError:
    check(False, "정상 명령은 통과")

print("\n[3] 메시지 변환 — 빈 값이 API 거절 모양을 만들지 않는지")
check(assistant_message(Reply())["content"] != [], "빈 Reply → 빈 content 아님")
check(tool_result_message([("t1", "", False)])["content"][0]["content"] != "",
      "빈 도구 출력 → 빈 tool_result content 아님")
check("is_error" not in tool_result_message([("t1", "ok", False)])["content"][0],
      "성공 시 is_error 키 없음")
check(tool_result_message([("t1", "boom", True)])["content"][0]["is_error"] is True,
      "실패 시 is_error=True")

print("\n[4] 정상 실행 후 히스토리 유효성 (압축 없음)")
wd = fresh_workdir(Path(__file__).parent / "demo_project")
h = Harness(FakeModel(list(MAIN_SCRIPT)), wd, label="t", budget=Budget(80),
            compact_at=10_000, retry_sleep=0)
h.tools["spawn_subagent"] = make_subagent_tool(h, lambda p: FakeModel(list(SUB_SCRIPT)))
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    ok, _ = h.run(TASK, verify_fn=verify_tests)
check(ok, "압축 없이 작업 성공")
validate_history(h.messages, "무압축")

print("\n[5] 압축 스트레스 — keep_tail 을 홀짝/경계로 흔들어도 짝이 안 깨지는가")
#     ★ 이게 이 레포에서 가장 미묘한 버그가 숨는 자리다.
#       tail 을 그냥 messages[-N:] 으로 자르면 N이 홀수일 때
#       tool_result 로 시작하는 구간이 남아 실제 API에서 400 이 난다.
bad = []
for keep_tail, compact_at in itertools.product([2, 3, 4, 5, 6, 7, 8, 9], [4, 5, 6, 7, 10, 12]):
    if compact_at <= keep_tail:
        continue
    wd2 = fresh_workdir(Path(__file__).parent / "demo_project")
    hh = Harness(FakeModel(list(MAIN_SCRIPT)), wd2, label="s", budget=Budget(200),
                 compact_at=compact_at, keep_tail=keep_tail, retry_sleep=0)
    hh.tools["spawn_subagent"] = make_subagent_tool(hh, lambda p: FakeModel(list(SUB_SCRIPT)))
    with contextlib.redirect_stdout(io.StringIO()):
        okk, _ = hh.run(TASK, verify_fn=verify_tests)
        before = list(FAILS)
        valid = validate_history(hh.messages, f"kt={keep_tail},ca={compact_at}")
        del FAILS[len(before):]          # 개별 실패는 여기서 집계만 하고 되돌린다
    if not (okk and valid):
        bad.append((keep_tail, compact_at))
check(not bad, f"압축 조합 전수 검사 ({len(bad)}개 실패)", str(bad[:5]))

print("\n[6] 예산 — 재시도가 예산을 빠져나가지 않는가")
wd3 = fresh_workdir(Path(__file__).parent / "demo_project")
b = Budget(max_requests=100)
hb = Harness(FakeModel(list(MAIN_SCRIPT), flaky_at={1, 2}, flaky_times=2), wd3,
             label="b", budget=b, retry_sleep=0)
hb.tools["spawn_subagent"] = make_subagent_tool(hb, lambda p: FakeModel(list(SUB_SCRIPT)))
with contextlib.redirect_stdout(io.StringIO()):
    hb.run(TASK, verify_fn=verify_tests)
check(b.requests > hb.turns, f"실제 요청({b.requests}) > 루프 턴({hb.turns}) — 재시도가 계상됨")

print("\n[7] 예산 소진 시 폭주하지 않고 멈추는가")
wd4 = fresh_workdir(Path(__file__).parent / "demo_project")
tiny = Budget(max_requests=3)
ht = Harness(FakeModel(list(MAIN_SCRIPT)), wd4, label="x", budget=tiny, retry_sleep=0)
ht.tools["spawn_subagent"] = make_subagent_tool(ht, lambda p: FakeModel(list(SUB_SCRIPT)))
with contextlib.redirect_stdout(io.StringIO()):
    okx, msgx = ht.run(TASK, verify_fn=verify_tests)
check((not okx) and tiny.requests <= 3, f"예산 3회에서 정지 (요청 {tiny.requests}회)", msgx)

print("\n[8] 서브 에이전트가 부모 예산을 공유하는가")
wd5 = fresh_workdir(Path(__file__).parent / "demo_project")
shared = Budget(max_requests=100)
hs = Harness(FakeModel(list(MAIN_SCRIPT)), wd5, label="p", budget=shared, retry_sleep=0)
hs.tools["spawn_subagent"] = make_subagent_tool(hs, lambda p: FakeModel(list(SUB_SCRIPT)))
with contextlib.redirect_stdout(io.StringIO()):
    hs.run(TASK, verify_fn=verify_tests)
check(shared.requests > hs.turns, f"부모 턴 {hs.turns} < 공유 예산 요청 {shared.requests} (서브 포함)")

print("\n[9] 토큰 추정 — 한글을 4배 과소평가하지 않는가")
kor = [{"role": "user", "content": "한" * 1000}]
eng = [{"role": "user", "content": "a" * 1000}]
check(est_tokens(kor) > est_tokens(eng) * 2,
      f"한글 {est_tokens(kor)} > 영문 {est_tokens(eng)} × 2")

print("\n[10] 데모 프로젝트 원본이 그대로인가 (재현성)")
r = subprocess.run("python3 -m unittest -q", shell=True, capture_output=True, text=True,
                   cwd=Path(__file__).parent / "demo_project")
check(r.returncode != 0, "원본 demo_project 는 여전히 실패 상태")

print("\n[11] 도구 출력 잘림 — max_out 초과 시 '잘렸다'고 반드시 말하는가")
#     ★ 조용한 잘림은 모델에게 하는 거짓말이다.
#       잘린 줄 모르면 모델은 그게 파일 전체인 줄 알고 판단한다.
#       ("함수가 없네? 새로 만들자" → 이미 아래쪽에 있는 함수를 중복 구현)
#       그래서 '잘렸다는 사실'은 내용만큼이나 중요한 정보다.
MAX_OUT = 200                       # 테스트를 빠르게 하려고 일부러 작게 잡는다
MARK = "생략됨"                      # tools._truncate 가 붙이는 표시
ttools = {t.name: t for t in make_tools(Sandbox(root), max_out=MAX_OUT)}

big_text = "\n".join(f"line {i} needle" for i in range(300))   # 200자를 한참 넘김
(root / "big.txt").write_text(big_text, encoding="utf-8")
(root / "small.txt").write_text("tiny needle", encoding="utf-8")

# (a) 큰 출력 → 잘림 표시가 있어야 하고, 실제로 짧아져 있어야 한다
#     _truncate 를 통과하는 도구 전부를 돌린다. 하나라도 빠뜨리면
#     그 도구만 조용히 자르는 상태가 되어도 아무도 모른다.
for name, args in [("read_file", {"path": "big.txt"}),
                   ("grep", {"path": "big.txt", "pattern": "needle"}),
                   ("run_bash", {"command": "cat big.txt"})]:
    out = str(ttools[name].run(**args))
    check(MARK in out, f"{name}: 큰 출력에 잘림 표시가 붙는다",
          f"표시 없이 {len(out)}자만 돌려줬다 — 조용한 잘림")
    check(len(out) < len(big_text), f"{name}: 실제로 잘려서 짧아졌다",
          f"{len(out)}자 (원본 {len(big_text)}자)")

# (b) 작은 출력 → 표시가 붙으면 안 된다 (거짓 경고 방지)
#     이게 없으면 "항상 표시를 붙인다"는 엉터리 구현도 (a)를 통과해버린다.
for name, args in [("read_file", {"path": "small.txt"}),
                   ("grep", {"path": "small.txt", "pattern": "needle"}),
                   ("run_bash", {"command": "cat small.txt"})]:
    out = str(ttools[name].run(**args))
    check(MARK not in out, f"{name}: 작은 출력엔 잘림 표시가 없다",
          "안 잘렸는데 잘렸다고 말한다")

print("\n[12] 예산 소진이 '우아하게' 끝나는가 — 마지막 보고를 남기는가")
#     ★ 그냥 실패로 끝내면 그때까지 태운 토큰이 통째로 버려진다.
#       예비분 1건으로 '어디까지 했는지'를 받아두면 사람이 이어받을 수 있다.
#       단, 그 1건 때문에 총액을 넘거나 히스토리가 깨지면 得보다 失이 크다.
bad_budget = []
for mx in [2, 3, 4, 5, 6, 8, 12]:       # 경계값들을 흔들어 본다
    wd6 = fresh_workdir(Path(__file__).parent / "demo_project")
    bg = Budget(max_requests=mx)
    hg = Harness(FakeModel(list(MAIN_SCRIPT)), wd6, label="g", budget=bg, retry_sleep=0)
    hg.tools["spawn_subagent"] = make_subagent_tool(hg, lambda p: FakeModel(list(SUB_SCRIPT)))
    with contextlib.redirect_stdout(io.StringIO()):
        okg, msgg = hg.run(TASK, verify_fn=verify_tests)
        before = list(FAILS)
        valid = validate_history(hg.messages, f"budget={mx}")
        del FAILS[len(before):]          # 개별 실패는 집계만 하고 되돌린다
    if bg.requests > mx or not valid:    # 총액 초과 또는 히스토리 파손
        bad_budget.append((mx, bg.requests, valid))

check(not bad_budget, "예산 소진 후에도 총액 준수 + 히스토리 유효", str(bad_budget[:3]))

# 실제로 '보고'가 담겨 오는지 (그냥 실패 문구만 있는 게 아니라)
wd7 = fresh_workdir(Path(__file__).parent / "demo_project")
bg2 = Budget(max_requests=3)
hg2 = Harness(FakeModel(list(MAIN_SCRIPT)), wd7, label="g2", budget=bg2, retry_sleep=0)
hg2.tools["spawn_subagent"] = make_subagent_tool(hg2, lambda p: FakeModel(list(SUB_SCRIPT)))
with contextlib.redirect_stdout(io.StringIO()):
    _, msg2 = hg2.run(TASK, verify_fn=verify_tests)
check("[중단 보고]" in msg2, "중단 시 보고가 결과에 포함된다", msg2)
check(bg2.requests == 3, f"예비분까지 정확히 소진 (요청 {bg2.requests}/3)")

# 예비분이 이미 바닥난 경우(hard_exhausted) 보고를 시도하지 않고 조용히 끝나는가
spent = Budget(max_requests=2)
spent.requests = 2                       # 예비분까지 소진된 상태를 강제로 만든다
hg3 = Harness(FakeModel([Reply(text="이건 불려선 안 된다")]),
              fresh_workdir(Path(__file__).parent / "demo_project"),
              label="g3", budget=spent, retry_sleep=0)
with contextlib.redirect_stdout(io.StringIO()):
    _, msg3 = hg3.run(TASK, verify_fn=verify_tests)
check(spent.requests == 2, f"예비분 없으면 추가 호출 안 함 (요청 {spent.requests}/2)")

print("\n[13] 모델 요약 압축 — 예산에서 깎이는가, 실패해도 살아남는가")
#     ★ 요약도 API 호출이다. 예산에서 안 깎으면 총액 통제에 구멍이 난다
#       (압축이 잦을수록 조용히 새어 나간다).
SUMMARY_TEXT = "- 원인은 0으로 나누기\n- average() 는 수정 완료\n- 남은 일: percent()"


def _summarizer():
    return FakeModel([Reply(text=SUMMARY_TEXT)] * 20)


def _run_compact(model_compact, summary_model, budget=None):
    wd = fresh_workdir(Path(__file__).parent / "demo_project")
    hh = Harness(FakeModel(list(MAIN_SCRIPT)), wd, label="mc",
                 budget=budget or Budget(max_requests=60),
                 compact_at=10, keep_tail=6, retry_sleep=0,
                 model_compact=model_compact, summary_model=summary_model)
    hh.tools["spawn_subagent"] = make_subagent_tool(hh, lambda p: FakeModel(list(SUB_SCRIPT)))
    with contextlib.redirect_stdout(io.StringIO()):
        okc, _ = hh.run(TASK, verify_fn=verify_tests)
    return hh, okc


b_rule = Budget(max_requests=60)
h_rule, ok_rule = _run_compact(False, None, b_rule)
b_model = Budget(max_requests=60)
h_model, ok_model = _run_compact(True, _summarizer(), b_model)

n_compact = sum(1 for r in h_model.trace if r["kind"] == "compact")
check(ok_rule and ok_model, "두 압축 방식 모두 작업 성공")
check(n_compact > 0, f"모델 압축이 실제로 발동함 ({n_compact}회)")
# 요약 호출 수만큼 예산이 더 나가야 한다. 같으면 어딘가에서 안 깎고 있는 것이다.
check(b_model.requests == b_rule.requests + n_compact,
      f"요약 호출이 예산에 계상됨 (모델 {b_model.requests} = 규칙 {b_rule.requests} + 요약 {n_compact})")
check(SUMMARY_TEXT.splitlines()[0] in h_model.summary, "모델이 쓴 요약이 실제로 반영됨")
validate_history(h_model.messages, "모델압축")

# 압축 전/후 토큰이 트레이스에 남아 '얼마나 줄었는지' 확인 가능한가
comp = [r for r in h_model.trace if r["kind"] == "compact"]
check(all(r.get("tokens_before", 0) > r.get("tokens_after", 0) for r in comp),
      "압축 후 토큰이 실제로 줄었다",
      str([(r.get("tokens_before"), r.get("tokens_after")) for r in comp]))


# ★ 요약이 실패해도 작업 전체가 죽으면 안 된다 → 규칙 기반으로 내려앉아야 한다
class _BrokenSummarizer:
    def call(self, messages, tools, system=None):
        raise RuntimeError("요약 모델 장애(가짜)")


h_broken, ok_broken = _run_compact(True, _BrokenSummarizer())
check(ok_broken, "요약 모델이 죽어도 작업은 성공 (규칙 기반 폴백)")
check(any(r["kind"] == "compact_fallback" for r in h_broken.trace),
      "폴백이 트레이스에 기록됨")

# 예산이 없으면 모델을 부르지 않고 규칙 기반으로 내려앉는가
tightb = Budget(max_requests=6)
h_tight, _ = _run_compact(True, _summarizer(), tightb)
check(tightb.requests <= 6, f"예산 부족 시에도 총액 준수 (요청 {tightb.requests}/6)")

print("\n" + "=" * 60)
if FAILS:
    print(f"❌ 실패 {len(FAILS)}건:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("✅ 전부 통과")
