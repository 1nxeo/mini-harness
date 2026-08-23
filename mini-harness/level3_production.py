"""
LEVEL 3 — 실무형 하네스
================================================================
Level 2의 루프에 실제 제품에서 반드시 필요한 것들을 붙인다.

  A. 컨텍스트 압축   : 대화가 길어지면 중간을 요약으로 갈아치운다
                       ★ 이때 tool_use/tool_result 짝을 절대 갈라놓지 않는다
  B. 예산 한도       : 실제 API 요청 수(재시도 포함)에 상한을 둔다
  C. 재시도          : '기다리면 될 수도 있는' 오류만 백오프 후 재시도
  D. 서브 에이전트   : 탐색은 하위 에이전트에게 맡기고 '결론만' 받는다
                       ★ 예산은 부모와 공유한다
  E. 트레이스 로그   : 무슨 도구를 왜 불렀는지 JSONL로 남긴다
  F. 안전            : 파일 도구 경로 격리 + 위험 명령 차단 (tools.py)

실행:  python3 level3_production.py            (가짜 모델, 오프라인)
       python3 level3_production.py --real     (실제 API)
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from model import (AnthropicModel, FakeModel, Reply, ToolCall, assistant_message,
                   is_retryable, tool_result_message)
from tools import Sandbox, Tool, fresh_workdir, make_tools

VERIFY_CMD = "python3 -m unittest -q"


# ──────────────────────────────────────────────────────────────
# 토큰 추정
# ──────────────────────────────────────────────────────────────
def est_tokens(messages) -> int:
    """거친 추정치.

    ★ 주의: 흔히 쓰는 "글자수 / 4" 는 영어·코드에서만 맞는다.
      한글은 대략 글자당 1토큰에 가까우므로 한글 프롬프트에서 /4 를 쓰면
      실제의 1/4 로 과소평가되어 토큰 가드가 사실상 작동하지 않는다.
      그래서 ASCII와 비ASCII를 나눠 센다.
      시스템 프롬프트·도구 스키마·응답은 여기 포함되지 않으니 여전히 하한선이다.
      실무에서는 응답의 usage 값(Reply.usage)을 누적해서 쓰는 것이 정답이다.
    """
    s = json.dumps(messages, ensure_ascii=False)
    ascii_n = sum(1 for ch in s if ord(ch) < 128)
    return ascii_n // 4 + (len(s) - ascii_n)


# ──────────────────────────────────────────────────────────────
# B. 예산 — 부모와 자식이 '같은 객체'를 공유하는 것이 핵심
# ──────────────────────────────────────────────────────────────
class Budget:
    """★ 서브 에이전트에게 별도 예산을 주면 총액 통제가 사라진다.
    (부모 25회 × 자식 8회 = 최악 200회) 그래서 객체 하나를 물려준다.
    또 '루프 반복 횟수'가 아니라 '실제 API 요청 횟수'를 센다 —
    재시도 3회는 청구서에 3번 찍히기 때문이다.
    """

    def __init__(self, max_requests: int = 40, max_context_tokens: int = 40_000,
                 reserve: int = 1):
        self.max_requests = max_requests
        self.max_context_tokens = max_context_tokens
        # ★ 예비분: 총액에서 이만큼은 '마지막 보고'용으로 떼어 둔다.
        #   작업용 예산이 먼저 바닥나고, 그 뒤에도 딱 이만큼은 남아 있어서
        #   "여기까지 했고 뭐가 남았다"를 물어볼 수 있다. (run() 의 [STEP 1] 참고)
        self.reserve = reserve
        self.requests = 0          # 재시도까지 포함한 실제 요청 수
        self.tokens_in = 0         # 실제 usage 누적 (real 모드에서만 채워진다)
        self.tokens_out = 0

    def charge_request(self):
        self.requests += 1

    @property
    def exhausted(self) -> bool:
        """작업용 예산이 소진되었는가. 예비분은 아직 남아 있다."""
        return self.requests >= max(0, self.max_requests - self.reserve)

    @property
    def hard_exhausted(self) -> bool:
        """예비분까지 전부 소진. 이제는 정말 한 건도 더 못 부른다.

        서브 에이전트가 먼저 예비분을 써버린 경우 부모가 여기 걸린다
        (예산을 공유하므로). 그때는 보고 없이 조용히 끝낸다.
        """
        return self.requests >= self.max_requests

    def note_usage(self, usage):
        if usage:
            self.tokens_in += usage.get("input", 0)
            self.tokens_out += usage.get("output", 0)

    def summary(self) -> str:
        s = f"API 요청 {self.requests}/{self.max_requests}회"
        if self.tokens_in or self.tokens_out:
            s += f" · 실제 토큰 in {self.tokens_in} / out {self.tokens_out}"
        return s


# ──────────────────────────────────────────────────────────────
# 메시지 블록 헬퍼 (압축의 정확성이 여기에 달려 있다)
# ──────────────────────────────────────────────────────────────
def has_block(msg, block_type: str) -> bool:
    c = msg.get("content")
    if not isinstance(c, list):
        return False
    return any(getattr(b, "type", None) == block_type
               or (isinstance(b, dict) and b.get("type") == block_type) for b in c)


def plain_text(msg) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


class Harness:
    def __init__(self, model, workdir: Path, *, system="", label="main",
                 budget=None, compact_at=14, keep_tail=8,
                 extra_tools=None, trace=None, retries=3, retry_sleep=1.0,
                 model_compact=False, summary_model=None):
        self.model = model
        # ★ 요약은 본 작업보다 훨씬 쉬운 일이라, 실무에서는 더 싸고 빠른
        #   모델(예: Haiku)을 따로 붙이는 게 정석이다. 그래서 주입 가능하게 뒀다.
        #   안 넘기면 본 모델을 그대로 쓴다.
        self.summary_model = summary_model or model
        self.model_compact = model_compact    # True 면 압축을 모델에게 맡긴다
        self.sandbox = Sandbox(workdir)
        self.tools = {t.name: t for t in make_tools(self.sandbox)}
        for t in (extra_tools or []):
            self.tools[t.name] = t
        self.system = system                 # ★ 컨텍스트 구성은 하네스의 책임이다
        self.label = label
        self.budget = budget or Budget()
        # ★ 압축 주기 = compact_at - keep_tail. 이 간격이 너무 좁으면
        #   두세 턴마다 압축이 터지면서(thrashing) 방금 읽은 파일 내용이
        #   결론을 내기 직전에 사라진다. 간격은 최소 몇 턴은 되게 잡는다.
        self.compact_at = compact_at         # 이 개수를 넘으면 압축
        self.keep_tail = keep_tail           # 압축 후 남길 최근 메시지 수
        self.retries = max(1, retries)
        self.retry_sleep = retry_sleep       # 데모에서 짧게 줄이기 위한 계수
        self.messages = []
        self.turns = 0                       # 루프 반복 횟수(≠ API 요청 수)
        self.trace = trace if trace is not None else []
        self.task = ""
        self.summary = ""                    # 누적 요약 (압축 때마다 덧붙는다)

    # ── E. 트레이스 ────────────────────────────────────────────
    def log(self, kind, **kw):
        self.trace.append({"agent": self.label, "turn": self.turns, "kind": kind, **kw})

    # ── A. 컨텍스트 압축 ──────────────────────────────────────
    def compact(self):
        """중간을 요약으로 대체한다. 세 가지를 반드시 지킨다.

        1) tool_use / tool_result 짝을 갈라놓지 않는다.
           남길 구간이 tool_result 로 시작하면 그 짝인 tool_use 가 이미
           잘려나간 상태다 → 실제 API에서 400
           ("tool_result block missing corresponding tool_use").
           그래서 짝이 맞을 때까지 시작점을 앞으로 민다.
        2) 요약을 별도 user 메시지로 끼우지 않고 최초 지시 안에 접어 넣는다.
           (user 턴이 연속되는 모양은 엄격한 검증기에서 문제가 된다)
        3) 요약은 누적한다. 이전 요약을 버리면 압축이 반복될수록
           초반 정보가 영구히 사라진다.

        압축의 그림 (messages 를 세 토막으로 나눈다)
        ------------------------------------------------------------
            [0]        [1 : start]              [start : ]
            head       dropped                  tail
            최초지시   버리고 요약으로 대체      그대로 유지(최근 대화)
              │            │                       │
              └────────────┴──> 새 head 하나로 합침 ┴──> 결과: [새 head] + tail

        아래 (a)~(e) 의 경계 계산은 _split_for_compact() 로 뽑아냈다
        (모델 요약 버전과 공유해야 하므로. 복사해두면 반드시 어긋난다).
        """
        split = self._split_for_compact()
        if not split:
            return False
        dropped, tail, carried = split

        # 버리는 구간에서 '어떤 도구를 썼는지'만 건져내 요약 재료로 쓴다
        used = []
        for m in dropped:
            c = m.get("content")
            if isinstance(c, list):
                for b in c:
                    name = b.get("name") if isinstance(b, dict) else getattr(b, "name", None)
                    btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                    if btype == "tool_use" and name:
                        used.append(name)

        # (3) 누적 요약
        piece = f"- 생략된 메시지 {len(dropped)}개 / 도구: {' → '.join(used) or '없음'}"
        if carried:
            piece += "\n- 그 사이 받은 지시/피드백: " + " | ".join(c[:200] for c in carried)

        return self._apply_compact(piece, dropped, tail, how="규칙")

    # ── A-1. 압축 경계 계산 (두 압축 방식이 공유) ─────────────
    def _split_for_compact(self):
        """(dropped, tail, carried) 를 돌려준다. 압축할 게 없으면 None.

        ★ 이 레포에서 가장 미묘한 버그가 숨는 자리다(validate.py [5] 참고).
          tail 을 그냥 messages[-N:] 으로 자르면 tool_use/tool_result 짝이
          갈라져 실제 API에서 400 이 난다.
        """
        # (a) 너무 짧으면 압축할 게 없다
        if len(self.messages) <= self.keep_tail + 1:
            return None

        # (b) 일단 '뒤에서 keep_tail 개'를 남길 후보 경계로 잡는다
        start = len(self.messages) - self.keep_tail

        # (c) 경계 보정 ①: tool_result 로 시작하면 짝인 tool_use 가 잘려나간 상태다.
        #     짝이 맞을 때까지 경계를 뒤로 밀어 고아 tool_result 를 없앤다.
        # (1) 고아 tool_result 로 시작하지 않도록 시작점을 뒤로 민다
        while start < len(self.messages) and has_block(self.messages[start], "tool_result"):
            start += 1
        # (d) 경계 보정 ②: user 메시지로 시작하면 새 head(user)와 연속돼버린다.
        #     그 내용은 버리지 않고 carried 에 담아 요약으로 흡수한다.
        # user 로 시작하면 head(user)와 연속되므로, 그 내용은 요약으로 흡수한다
        carried = []
        while start < len(self.messages) and self.messages[start].get("role") == "user":
            t = plain_text(self.messages[start])
            if t:
                carried.append(t)
            start += 1

        tail = self.messages[start:]
        # (e) 꼬리 보정: 답 없는 tool_use 로 끝나도 같은 짝 위반이다. 뒤에서 잘라낸다.
        # 응답 없는 tool_use 로 끝나면 잘라낸다 (반대 방향의 같은 위반)
        while tail and has_block(tail[-1], "tool_use") and not has_block(tail[-1], "tool_result"):
            if len(tail) == 1:
                tail = []
                break
            tail = tail[:-1]

        # 경계 보정을 마친 뒤 실제로 버릴 구간이 확정된다
        dropped = self.messages[1:start]
        if not dropped or not tail:   # 보정 결과 버릴 게 없어졌으면 압축 불가
            return None

        return dropped, tail, carried

    # ── A-2. 압축의 공통 뒷부분 ───────────────────────────────
    def _apply_compact(self, piece: str, dropped, tail, how: str) -> bool:
        """요약문(piece)을 받아 실제로 메시지를 갈아끼운다.

        요약을 '무엇으로 만들었는지'(규칙 / 모델)만 다르고, 여기서부터는
        완전히 같다. compact() 와 compact_with_model() 이 공유한다.
        """
        before = est_tokens(self.messages)          # ← 압축 전 크기

        # (3) 요약은 누적한다. 이전 요약을 버리면 초반 정보가 영구히 사라진다.
        self.summary = (self.summary + "\n" + piece).strip()

        # (2) 요약을 최초 지시에 접어 넣은 단일 user 메시지로 head 를 재구성
        head = {"role": "user", "content":
                f"{self.task}\n\n[지금까지의 경과 요약]\n{self.summary}"}
        self.messages = [head] + tail

        after = est_tokens(self.messages)           # ← 압축 후 크기
        saved = before - after
        pct = (saved / before * 100) if before else 0

        self.log("compact", how=how, dropped=len(dropped), kept=len(tail),
                 tokens_before=before, tokens_after=after)
        print(f"\033[35m  ⤵ [{self.label}] 압축({how}): {len(dropped)}개 → 요약, 최근 {len(tail)}개 유지"
              f" | 토큰 {before} → {after} ({saved:+d}, {pct:.0f}% 절감)"
              f" | 다음 압축까지 {self.compact_at - len(tail) - 1}개 여유\033[0m")
        return True

    # ── A-3. 모델에게 요약을 맡기는 압축 ──────────────────────
    def compact_with_model(self) -> bool:
        """결정적 요약 대신 모델이 쓴 요약으로 중간을 대체한다.

        규칙 기반 요약은 "메시지 12개 / 도구: read_file → run_bash" 처럼
        '무슨 일이 있었는지'만 남기고 '무엇을 알아냈는지'는 버린다.
        모델 요약은 내용을 읽고 결론을 남길 수 있다는 게 차이다.

        ★ 대신 대가가 있다 — 요약도 API 호출이다. 그래서:
          1) 예산에서 반드시 차감한다 (안 그러면 총액 통제가 뚫린다)
          2) 예산이 없으면 규칙 기반으로 조용히 내려앉는다
          3) 요약 실패가 작업 전체를 죽이면 안 되므로 실패 시에도 폴백한다
        """
        split = self._split_for_compact()
        if not split:
            return False
        dropped, tail, carried = split

        # (1)(2) 예산이 없으면 모델을 부르지 않는다. 압축은 포기할 수 없는
        #        작업이므로(안 하면 컨텍스트가 터진다) 규칙 기반으로 대체한다.
        if self.budget.exhausted:
            return self.compact()

        prompt = (
            "다음은 어떤 작업 에이전트의 대화 기록 일부다. 이 구간은 곧 삭제되고 "
            "너의 요약만 남는다. 나중에 작업을 이어가는 데 꼭 필요한 것만 적어라.\n"
            "- 확인된 사실(파일 내용, 테스트 결과, 원인 등)을 우선한다\n"
            "- 이미 시도해서 실패한 방법도 적는다(같은 삽질 방지)\n"
            "- 불릿 5줄 이내, 군더더기 없이\n\n"
            f"[원래 작업]\n{self.task}\n\n"
            f"[삭제될 구간]\n{self._render_for_summary(dropped)}"
        )
        if carried:
            prompt += "\n\n[그 사이 받은 지시/피드백]\n" + "\n".join(c[:300] for c in carried)

        self.budget.charge_request()   # ★ 요약도 청구서에 찍힌다
        try:
            # ★ 도구를 안 넘긴다([]). 요약에 도구가 필요할 리 없고,
            #   도구를 부르면 그걸 실행할 자리도 없다.
            # ★ self.messages 가 아니라 '새로 만든 단발 대화'를 넘긴다.
            #   기존 히스토리를 재사용하면 tool_use/tool_result 짝 문제를
            #   여기서도 신경 써야 하는데, 그럴 이유가 없다.
            reply = self.summary_model.call([{"role": "user", "content": prompt}], [],
                                            system=None)
            self.budget.note_usage(reply.usage)
        except Exception as e:
            # (3) 요약 실패로 작업 전체를 죽이지 않는다
            self.log("compact_fallback", error=str(e))
            print(f"\033[33m  ↯ 모델 요약 실패({e}) → 규칙 기반으로 대체\033[0m")
            return self.compact()

        piece = (reply.text or "").strip()
        if not piece:
            return self.compact()

        return self._apply_compact(piece, dropped, tail, how="모델")

    def _render_for_summary(self, msgs) -> str:
        """메시지 블록들을 모델이 읽을 수 있는 평문 대화록으로 편다.

        raw_content(실제 API 응답)는 dict 가 아니라 객체라서 두 경우를 모두 다룬다.
        """
        def field(b, key):
            return b.get(key) if isinstance(b, dict) else getattr(b, key, None)

        lines = []
        for m in msgs:
            role = m.get("role")
            c = m.get("content")
            if isinstance(c, str):
                lines.append(f"[{role}] {c}")
                continue
            for b in (c if isinstance(c, list) else []):
                t = field(b, "type")
                if t == "text":
                    lines.append(f"[{role}] {field(b, 'text') or ''}")
                elif t == "tool_use":
                    args = json.dumps(field(b, "input") or {}, ensure_ascii=False)
                    lines.append(f"[{role}] 도구호출 {field(b, 'name')} {args[:200]}")
                elif t == "tool_result":
                    lines.append(f"[도구결과] {str(field(b, 'content'))[:400]}")
        return "\n".join(lines)

    # ── C. 재시도 ─────────────────────────────────────────────
    def call_model(self):
        """모델 호출 1회 = 여기서 최대 self.retries 번까지 시도한다.

        흐름:
            시도 1 → 성공하면 즉시 return
                   → 실패: '기다리면 나을 오류'인가?
                        아니오(400/401 등) → 그대로 예외를 올려보냄(즉시 포기)
                        예(429/503 등)     → 지수 백오프로 대기 후 시도 2 …
        """
        # ★ 예산은 여기서 깎는다. 루프 밖에서 세면 재시도가 예산을 빠져나간다.
        last = None
        for attempt in range(1, self.retries + 1):
            if self.budget.exhausted:
                raise RuntimeError(f"예산 소진: {self.budget.summary()}")
            self.budget.charge_request()   # ← 시도할 때마다 차감(재시도도 청구된다)
            try:
                reply = self.model.call(self.messages, list(self.tools.values()),
                                        system=self.system or None)
                self.budget.note_usage(reply.usage)
                return reply
            except Exception as e:
                last = e
                if attempt == self.retries or not is_retryable(e):
                    raise                     # ★ 400/401/TypeError 는 재시도해도 안 낫는다
                # 지수 백오프: 1배 → 2배 → 4배 … 로 대기 시간을 늘린다
                wait = (2 ** (attempt - 1)) * self.retry_sleep
                print(f"\033[33m  ↻ 일시적 오류({e}) — {wait:.1f}초 후 재시도 "
                      f"{attempt}/{self.retries - 1}\033[0m")
                self.log("retry", attempt=attempt, error=str(e), wait=wait)
                time.sleep(wait)
        raise last if last else RuntimeError("재시도 실패")

    # ── 예산 소진 시 마지막 보고 ──────────────────────────────
    def _final_report(self) -> str:
        """예비 요청 1건으로 '지금까지 한 일 + 남은 일'을 받아온다.

        ★ 왜 필요한가: 그냥 실패로 끝내면 그때까지 태운 토큰이 통째로 버려진다.
          어디까지 갔고 무엇이 확인됐는지만 남아도 사람이 이어받거나,
          다음 실행이 같은 삽질을 반복하지 않을 수 있다.
        """
        FALLBACK = "마지막 보고를 받지 못했습니다."

        # 예비분까지 다 썼으면(서브 에이전트가 먼저 쓴 경우 등) 조용히 포기한다.
        if self.budget.hard_exhausted:
            return FALLBACK

        ask = ("예산이 소진되어 여기서 중단합니다. 도구는 더 이상 쓸 수 없습니다.\n"
               "지금까지 한 일, 확인된 사실, 남은 일을 간결히 정리해 보고하세요.")

        # ★ 새 user 메시지를 덧붙이면 안 된다.
        #   이 시점의 마지막 메시지는 대개 '도구 결과'(role=user)라서,
        #   그냥 append 하면 user 턴이 연속되는 모양이 된다 (validate.py 가 잡는 위반).
        #   그래서 compact() 가 요약을 head 에 접어 넣듯, 마지막 user 메시지 '안에' 넣는다.
        last = self.messages[-1] if self.messages else None
        if last and last.get("role") == "user":
            c = last.get("content")
            if isinstance(c, list):
                # tool_result 블록들 뒤에 text 블록을 하나 더 얹는다 (API가 허용하는 모양)
                last["content"] = c + [{"type": "text", "text": ask}]
            else:
                last["content"] = f"{c}\n\n{ask}"
        else:
            self.messages.append({"role": "user", "content": ask})

        self.budget.charge_request()
        try:
            # ★ 도구를 넘기지 않는다([]). 도구가 보이면 모델은 또 도구를 부르려 하는데
            #   그걸 실행할 예산이 없다. 여기서 원하는 건 '텍스트 보고' 하나뿐이다.
            # ★ call_model() 을 쓰지 않는다. 그 안의 exhausted 가드에 막히고,
            #   재시도가 예비분을 넘겨 쓰기 때문이다. 실패하면 그냥 포기한다.
            reply = self.model.call(self.messages, [], system=self.system or None)
            self.budget.note_usage(reply.usage)
        except Exception as e:
            self.log("final_report", ok=False, error=str(e))
            return FALLBACK

        # ★ assistant_message(reply) 를 그대로 쓰면 안 된다.
        #   도구를 안 넘겼어도 모델이 tool_use 를 뱉을 수 있는데(가짜 모델은 물론,
        #   실제로도 캐시된 의도가 남는 경우가 있다), 그걸 그대로 붙이면
        #   '응답 없는 tool_use' 가 히스토리 끝에 남아 다음 호출이 400 이 된다.
        #   여기서 원하는 건 텍스트뿐이므로 텍스트 블록만 남긴다.
        text = reply.text or FALLBACK
        self.messages.append({"role": "assistant",
                              "content": [{"type": "text", "text": text}]})
        self.log("final_report", ok=True)
        return text

    # ── 메인 루프 ─────────────────────────────────────────────
    def run(self, task: str, verify_fn=None, max_verify=3):
        """
        전체 흐름 지도 (아래 [STEP n] 주석과 번호가 맞물린다)
        ------------------------------------------------------------
        Level 2의 루프와 뼈대는 같다. 앞에 '가드 2개'(STEP 1·2)가 붙고,
        모델 호출이 재시도로 감싸진 것(STEP 3)이 달라진 전부다.

            [STEP 0] 준비: 대화 기록 초기화
                 │
                 ▼
            ┌──> [STEP 1] 예산 남았나?      ─ 아니오 → return False (중단)
            │         │ 예
            │         ▼
            │    [STEP 2] 대화가 너무 긴가?  ─ 예 → compact() 로 중간을 요약으로 교체
            │         │                              (더 줄일 수 없으면 return False)
            │         ▼
            │    [STEP 3] 모델 호출 (call_model = 재시도 + 예산 차감 포함)
            │         │
            │         ▼
            │    [STEP 4] 모델이 도구를 불렀나?
            │         │
            │         ├── 안 불렀다 (= "다 했어요")
            │         │      └─> [STEP 5] 검증 게이트 (Level 2와 동일)
            │         │             ├─ 통과      → return True
            │         │             ├─ 실패+한도  → return False
            │         │             └─ 실패      → 에러를 대화에 붙이고 ─┐
            │         │                                                │
            │         └── 불렀다                                        │
            │                └─> [STEP 6] 도구 실행                     │
            │                      └─> [STEP 7] 결과를 대화에 붙임      │
            │                                                          │
            └──────────────────────────────────────────────────────────┘

        ※ Level 2의 max_steps 같은 반복 상한이 없는 대신, [STEP 1]의
          예산(실제 API 요청 수)이 그 역할을 한다. 무한루프는 예산이 끊는다.
        ※ 이 함수는 main 과 sub 에이전트가 '똑같이' 쓴다. 서브 에이전트는
          같은 Harness 를 label="sub" 로 하나 더 만든 것뿐이다.
        """
        # ── [STEP 0] 준비 ────────────────────────────────────────
        self.task = task                                  # 압축할 때 head 재구성에 쓴다
        self.messages = [{"role": "user", "content": task}]
        verify_rounds = 0

        while True:
            # ── [STEP 1] 예산 가드 ───────────────────────────────
            #   부모·자식이 '같은 Budget 객체'를 공유하므로, 서브 에이전트가
            #   쓴 요청도 여기서 함께 반영되어 총액이 통제된다.
            #
            #   ★ 여기서 끝내되, '그냥' 끝내지 않는다. exhausted 는 예비분을
            #     남겨둔 채 True 가 되므로(Budget.reserve), 그 1건으로
            #     중단 보고를 받아서 함께 돌려준다. 태운 토큰을 회수하는 장치다.
            if self.budget.exhausted:
                self.log("stop", reason="budget")
                report = self._final_report()
                return False, f"예산 소진 ({self.budget.summary()})\n[중단 보고] {report}"

            # ── [STEP 2] 컨텍스트 가드 (A. 압축) ─────────────────
            #   메시지 개수 또는 추정 토큰이 한도를 넘으면 중간을 요약으로 접는다.
            #   compact() 가 False(더 줄일 게 없음)인데도 토큰이 넘치면 포기한다.
            # A. 길이/토큰 기반 압축
            if len(self.messages) > self.compact_at or \
               est_tokens(self.messages) > self.budget.max_context_tokens:
                # 규칙 기반이 기본. model_compact=True 면 모델에게 요약을 맡긴다
                # (그 호출도 예산에서 깎인다 — compact_with_model 참고).
                squeeze = self.compact_with_model if self.model_compact else self.compact
                if not squeeze() and est_tokens(self.messages) > self.budget.max_context_tokens:
                    self.log("stop", reason="context")
                    return False, "컨텍스트 한도 초과 (더 줄일 수 없음)"

            # ── [STEP 3] 모델 호출 ───────────────────────────────
            #   turns 는 '루프 반복 수'고, 예산이 세는 requests 는 '실제 API 요청 수'다.
            #   재시도가 일어나면 turns 1회에 requests 가 여러 번 늘어난다.
            self.turns += 1
            reply = self.call_model()                      # ← 재시도·예산 차감은 이 안에서
            self.messages.append(assistant_message(reply))
            if reply.text:
                print(f"\033[36m[{self.label} {self.turns}] \033[0m{reply.text}")
                self.log("say", text=reply.text[:200])

            # ── [STEP 4] 갈림길: 도구를 불렀는가? ────────────────
            if not reply.wants_tools:
                # ── [STEP 5] 검증 게이트 ─────────────────────────
                #   verify_fn 이 없으면(= 서브 에이전트) 그냥 답변을 반환한다.
                #   서브는 '조사 보고'가 목적이라 돌릴 테스트가 없기 때문이다.
                if verify_fn is None:
                    return True, reply.text
                verify_rounds += 1
                ok, out = verify_fn(self.sandbox)          # 하네스가 직접 테스트 실행
                self.log("verify", ok=ok)
                if ok:                                     # (5-a) 통과 → 성공 종료
                    return True, reply.text
                if verify_rounds >= max_verify:            # (5-b) 한도 초과 → 포기
                    return False, "검증 재시도 한도 초과"
                # (5-c) 실패 → 실패 출력을 대화에 붙이고 루프 맨 위로
                print(f"\033[33m  ⟳ 검증 실패 → 되돌려 보냄 ({verify_rounds}/{max_verify})\033[0m")
                self.messages.append({"role": "user", "content":
                    f"테스트가 아직 실패합니다:\n{out}\n원인을 다시 찾아 고치세요."})
                continue  # → [STEP 1]로 되돌아감

            # ── [STEP 6] 도구 실행 ───────────────────────────────
            #   spawn_subagent 도 여기서 '평범한 도구 하나'로 실행된다.
            #   즉 서브 에이전트 실행은 tool.run() 안에서 통째로 벌어지고,
            #   본체에는 보고서 문자열 하나만 결과로 돌아온다.
            results = []
            for c in reply.tool_calls:
                preview = {k: (str(v)[:50] + "…" if len(str(v)) > 50 else v)
                           for k, v in c.args.items()}
                print(f"\033[90m[{self.label} {self.turns}] 🔧 {c.name} {preview}\033[0m")
                tool = self.tools.get(c.name)      # 조회와 실행을 분리 (오보 방지)
                if tool is None:
                    out, err = f"없는 도구: {c.name}. 사용 가능: {', '.join(self.tools)}", True
                else:
                    try:
                        out, err = str(tool.run(**c.args)), False
                    except Exception as e:
                        # 샌드박스 차단(PermissionError)도 여기로 들어온다.
                        # 죽이지 않고 결과로 돌려줘야 모델이 다른 길을 찾는다.
                        out, err = f"{type(e).__name__}: {e}", True
                if err:
                    print(f"\033[31m      ↳ 차단/실패: {out}\033[0m")
                # E. 트레이스 기록
                # 트레이스에도 잘림을 적용한다. write_file 인자를 통째로 남기면
                # 로그가 소스코드 덤프가 된다.
                self.log("tool", name=c.name,
                         args={k: str(v)[:120] for k, v in c.args.items()},
                         error=err, out=out[:300])
                results.append((c.id, out, err))

            # ── [STEP 7] 결과를 대화에 붙이고 루프 처음으로 ──────
            self.messages.append(tool_result_message(results))

    def dump_trace(self, path: Path):
        with open(path, "w", encoding="utf-8") as f:
            for row in self.trace:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"트레이스 저장: {path} ({len(self.trace)}줄)")


# ── D. 서브 에이전트 ──────────────────────────────────────────
def make_subagent_tool(parent: "Harness", sub_model_factory, sub_system=""):
    """서브 에이전트는 '컨텍스트 방화벽'이다.

    파일 20개를 뒤지는 일을 본체가 직접 하면 대화가 파일 내용으로 가득 찬다.
    하위 에이전트에게 시키면 그 쓰레기는 하위 대화에서 소각되고,
    본체에는 '결론 몇 줄'만 돌아온다. 이게 긴 작업을 가능하게 하는 트릭.

    ★ 예산(budget)과 트레이스(trace)는 부모 것을 그대로 물려준다.
    """

    def _spawn(purpose: str):
        """이 함수 전체가 부모 입장에선 '도구 한 번 실행'이다.

        부모의 [STEP 6]에서 tool.run() 으로 불리고, 그 안에서 자식 하네스가
        자기 루프를 끝까지 다 돈 뒤, 마지막 한 줄만 문자열로 반환된다.
        자식이 읽은 파일 내용은 자식의 messages 와 함께 그대로 버려진다.
        """
        # 자식은 부모와 같은 클래스·같은 run() 을 쓴다. 다른 건 label 과
        # 시스템 프롬프트, 그리고 verify_fn 을 넘기지 않는다는 점뿐.
        sub = Harness(sub_model_factory(purpose), parent.sandbox.root,
                      system=sub_system, label="sub",
                      budget=parent.budget,    # ★ 예산 공유 — 총액 통제의 핵심
                      trace=parent.trace,      # ★ 트레이스도 공유 — 한 파일에 같이 남는다
                      compact_at=14, keep_tail=8, retry_sleep=parent.retry_sleep)
        # verify_fn 없이 호출 → 자식은 [STEP 5]에서 검증 없이 답변만 반환한다
        ok, answer = sub.run(f"다음을 조사해서 결론만 간결하게 보고하라: {purpose}")
        # 부모에게 돌아가는 건 이 문자열 하나뿐이다 (= 컨텍스트 방화벽)
        return f"[서브 에이전트 보고]\n{answer}" if ok else f"[서브 에이전트 실패] {answer}"

    return Tool("spawn_subagent",
                "조사/탐색 작업을 하위 에이전트에게 위임하고 요약된 결론만 받는다. "
                "파일을 많이 읽어야 하는 조사에 쓴다.",
                {"type": "object",
                 "properties": {"purpose": {"type": "string", "description": "조사할 내용"}},
                 "required": ["purpose"]},
                _spawn)


def verify_tests(sandbox: Sandbox):
    try:
        r = subprocess.run(VERIFY_CMD, shell=True, cwd=sandbox.root,
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, "검증 명령이 60초 안에 끝나지 않았습니다(무한 루프 의심)."
    return r.returncode == 0, (r.stdout + r.stderr).strip()


# ──────────────────────────────────────────────────────────────
# 가짜 시나리오
# ──────────────────────────────────────────────────────────────
MAIN_SYSTEM = """당신은 시니어 파이썬 개발자입니다.
- 조사·탐색이 필요하면 spawn_subagent 로 위임하고 결론만 받으세요.
- 부분 수정에는 edit_file 을 쓰세요(old_string 은 유일해야 합니다).
- 수정 후 `python3 -m unittest -q` 로 반드시 통과를 확인하세요."""

SUB_SYSTEM = "당신은 코드 조사 전문가입니다. 파일을 읽어 원인을 특정하고, 결론만 간결히 보고하세요."

MAIN_SCRIPT = [
    # F. 안전장치 시연 — 파일 도구의 폴더 탈출 시도
    Reply(text="시스템 파일을 먼저 볼까요?",
          tool_calls=[ToolCall("m0", "read_file", {"path": "../../../etc/passwd"})]),
    # F. 안전장치 시연 — 파괴적 명령
    Reply(text="폴더를 정리하겠습니다.",
          tool_calls=[ToolCall("m1", "run_bash", {"command": "rm -rf ."})]),
    # D. 조사는 서브 에이전트에게 위임
    Reply(text="차단되었군요. 정상 경로로 갑니다. 조사는 하위 에이전트에게 맡기겠습니다.",
          tool_calls=[ToolCall("m2", "spawn_subagent",
                               {"purpose": "calc.py 와 test_calc.py 를 읽고 실패 원인을 특정하라"})]),
    # 치환형 편집 — 전체 덮어쓰기보다 안전
    Reply(text="보고를 받았습니다. average() 부터 고칩니다.",
          tool_calls=[ToolCall("m3", "edit_file", {
              "path": "calc.py",
              "old_string": "def average(nums):\n    return sum(nums) / len(nums)",
              "new_string": "def average(nums):\n    if not nums:\n        return 0\n    return sum(nums) / len(nums)"})]),
    Reply(text="검증합니다.",
          tool_calls=[ToolCall("m4", "run_bash", {"command": VERIFY_CMD})]),
    Reply(text="average 는 통과. percent 도 같은 문제라 이어서 고칩니다.",
          tool_calls=[ToolCall("m5", "edit_file", {
              "path": "calc.py",
              "old_string": "def percent(part, whole):\n    return part / whole * 100",
              "new_string": "def percent(part, whole):\n    if whole == 0:\n        return 0\n    return part / whole * 100"})]),
    Reply(text="다시 검증합니다.",
          tool_calls=[ToolCall("m6", "run_bash", {"command": VERIFY_CMD})]),
    Reply(text="4개 테스트 모두 통과. 작업 완료."),
]

SUB_SCRIPT = [
    Reply(text="파일 목록 확인.", tool_calls=[ToolCall("s0", "list_dir", {"path": "."})]),
    Reply(text="테스트 읽기.", tool_calls=[ToolCall("s1", "read_file", {"path": "test_calc.py"})]),
    Reply(text="구현 읽기.", tool_calls=[ToolCall("s2", "read_file", {"path": "calc.py"})]),
    Reply(text="테스트 실행.", tool_calls=[ToolCall("s3", "run_bash", {"command": VERIFY_CMD})]),
    Reply(text="결론: average() 는 빈 리스트, percent() 는 whole==0 에서 ZeroDivisionError. "
               "두 함수 모두 앞단에 0 반환 가드가 필요하다."),
]

TASK = "테스트가 실패합니다. 조사하고 고치고 검증까지 완료하세요."

if __name__ == "__main__":
    real = "--real" in sys.argv
    here = Path(__file__).parent
    workdir = fresh_workdir(here / "demo_project")
    print(f"작업 폴더: {workdir}\n")

    budget = Budget(max_requests=40, max_context_tokens=40_000)

    if real:
        main_model = AnthropicModel()
        sub_factory = lambda purpose: AnthropicModel()
    else:
        # 3번째 호출에서 일부러 일시적 오류를 2번 내서 재시도 로직을 눈으로 확인한다
        main_model = FakeModel(MAIN_SCRIPT, flaky_at={3}, flaky_times=2)
        sub_factory = lambda purpose: FakeModel(SUB_SCRIPT)

    # 압축을 눈으로 보려고 일부러 낮게 잡았다(간격 3메시지 ≈ 1.5턴 — 실무에선 너무 좁다).
    # 실제로 쓸 때는 컨텍스트 한도의 60~70% 지점에서 토큰 기준으로 트리거하는 편이 낫다.
    h = Harness(main_model, workdir, system=MAIN_SYSTEM, label="main",
                budget=budget, compact_at=10, keep_tail=6,
                retry_sleep=0.05 if not real else 1.0)
    h.tools["spawn_subagent"] = make_subagent_tool(h, sub_factory, sub_system=SUB_SYSTEM)

    ok, msg = h.run(TASK, verify_fn=verify_tests)

    verdict = "\033[32m✅ 성공" if ok else "\033[31m❌ 실패"
    print(f"\n{verdict}\033[0m — {msg}")
    print(f"{budget.summary()}")
    print(f"루프 턴: main {h.turns}회 / 최종 컨텍스트 {len(h.messages)}개 메시지 "
          f"(≈{est_tokens(h.messages)} 토큰, 누적 소비량이 아니라 '현재 크기')")
    h.dump_trace(here / "trace.jsonl")
