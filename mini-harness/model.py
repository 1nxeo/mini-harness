"""
model.py — 모델 어댑터 층
================================================================
하네스의 첫 번째 통찰:
    모델은 "메시지 목록 + 도구 목록 → (텍스트, 도구호출 목록)" 함수일 뿐이다.

이 함수를 인터페이스로 고정해두면, 그 뒤에 진짜 API가 있든 스크립트로
짜놓은 가짜가 있든 하네스 코드는 한 줄도 안 바뀐다.
가짜 모델(FakeModel)이 있으면 API 키/비용/네트워크 없이 하네스의
'배관'만 따로 검증할 수 있다. 테스트 하네스의 mock과 정확히 같은 역할.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 2026년 8월 기준 최신 모델 ID. 4.6 세대부터는 날짜 없는 형식이며,
# 그 자체가 고정 스냅샷이다(evergreen 포인터가 아니다).
#   claude-opus-5 / claude-sonnet-5 / claude-haiku-4-5
DEFAULT_MODEL = "claude-sonnet-5"


# ──────────────────────────────────────────────────────────────
# 모델이 뱉는 것: 텍스트 아니면 "이 도구 불러줘"라는 요청
# ──────────────────────────────────────────────────────────────
@dataclass
class ToolCall:
    id: str                 # 이 호출의 식별자. 결과를 돌려줄 때 짝을 맞추는 데 쓴다
    name: str               # 부르려는 도구 이름
    args: Dict[str, Any]    # 인자


@dataclass
class Reply:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)

    # ↓ 실제 API 응답의 content 블록을 '그대로' 보관한다. 왜 필요한지는
    #   아래 assistant_message() 주석 참고. (가짜 모델은 None)
    raw_content: Optional[Any] = None
    # 실제 청구 토큰. est_tokens 같은 추정치보다 항상 이 값이 우선이다.
    usage: Optional[Dict[str, int]] = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class TransientError(RuntimeError):
    """일시적 오류(레이트리밋/네트워크)를 흉내내는 예외. 오프라인 재시도 데모용."""


# ──────────────────────────────────────────────────────────────
# 가짜 모델: 정해진 순서대로 Reply를 뱉는다
# ──────────────────────────────────────────────────────────────
class FakeModel:
    """API 키 없이 하네스를 돌려보기 위한 스크립트 모델.

    실제 모델처럼 '생각'하지는 않지만, 하네스가 도구를 제대로 실행하고
    결과를 제대로 되먹이는지는 완벽하게 검증할 수 있다.
    """

    name = "fake"

    def __init__(self, script: List[Reply], flaky_at: Optional[set] = None, flaky_times: int = 2):
        """flaky_at: 이 호출 번호(1-based)에서 일부러 TransientError를 낸다.
        재시도 로직이 실제로 동작하는지 오프라인에서 보여주기 위한 장치."""
        self.script = list(script)
        self.call_count = 0
        self.flaky_at = flaky_at or set()
        self.flaky_times = flaky_times
        self._flaked = {}

    def call(self, messages, tools, system=None) -> Reply:
        # ★ 실패한 시도는 call_count 를 올리지 않는다. 올려버리면 재시도할 때
        #   '다른 호출'로 취급되어 flaky 지점이 어긋난다 (초안의 버그).
        n = self.call_count + 1
        if n in self.flaky_at and self._flaked.get(n, 0) < self.flaky_times:
            self._flaked[n] = self._flaked.get(n, 0) + 1
            raise TransientError(f"429 rate_limit (가짜, {self._flaked[n]}회째)")
        self.call_count = n
        if not self.script:
            return Reply(text="(스크립트가 끝났습니다)")
        return self.script.pop(0)


# ──────────────────────────────────────────────────────────────
# 진짜 모델: Anthropic API
# ──────────────────────────────────────────────────────────────
class AnthropicModel:
    """실제 API 어댑터.

    사용 전:  pip install anthropic
              export ANTHROPIC_API_KEY=sk-ant-...
    """

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, system: str = "", max_tokens: int = 4096):
        from anthropic import Anthropic  # 지연 import: 가짜 모드에선 설치 불필요

        self.client = Anthropic()  # ANTHROPIC_API_KEY 환경변수를 자동으로 읽는다
        self.model = model
        self.system = system
        self.max_tokens = max_tokens

    def call(self, messages, tools, system=None) -> Reply:
        # 빈 문자열/빈 리스트는 넘기지 않는다. API는 "비어있지만 존재하는 값"을
        # 스키마 위반으로 거절할 수 있으므로, 없으면 아예 키를 빼는 게 안전하다.
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        sys_prompt = system if system is not None else self.system
        if sys_prompt:
            kwargs["system"] = sys_prompt
        if tools:
            kwargs["tools"] = [t.schema for t in tools]

        resp = self.client.messages.create(**kwargs)

        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, args=dict(block.input)))

        return Reply(
            text="\n".join(text_parts),
            tool_calls=calls,
            raw_content=resp.content,   # ← 원본 블록 보관 (아래 설명)
            usage={"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
        )


# ──────────────────────────────────────────────────────────────
# Reply → 대화 기록에 넣을 메시지로 변환
#   (Anthropic 메시지 포맷을 표준으로 삼는다. 가짜/진짜 모두 이걸 쓴다)
# ──────────────────────────────────────────────────────────────
def assistant_message(reply: Reply) -> dict:
    """모델의 응답을 대화 기록에 되돌려 넣는다.

    ★ 중요: 실제 API에서는 응답의 content 블록을 **그대로** 되돌려 넣어야 한다.
      공식 패턴이 {"role": "assistant", "content": response.content} 인 이유:
      - 블록을 파싱해서 재조립하면 text/tool_use 의 원래 순서가 뭉개진다
      - extended thinking 을 켠 경우 thinking / redacted_thinking 블록과
        그 signature 를 빼먹으면 다음 턴에서 400 이 난다
        (실제로 자주 보고되는 오류다)
      그래서 raw_content 가 있으면 무조건 그걸 쓴다.
    """
    if reply.raw_content is not None:
        return {"role": "assistant", "content": reply.raw_content}

    # 가짜 모델용 폴백: Reply 로부터 블록을 재구성
    content = []
    if reply.text:
        content.append({"type": "text", "text": reply.text})
    for c in reply.tool_calls:
        content.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.args})
    if not content:
        # 빈 content 리스트는 API가 거절한다("non-empty content").
        # 모델이 아무것도 안 뱉는 턴은 실제로 생길 수 있으므로 반드시 막아둔다.
        content.append({"type": "text", "text": "(내용 없음)"})
    return {"role": "assistant", "content": content}


def tool_result_message(results: List[tuple]) -> dict:
    """results: [(tool_call_id, 결과문자열, is_error), ...]

    도구 결과는 'user' 역할로 되돌려준다. 모델 입장에서 도구 결과는
    '외부에서 들어온 새 정보'이기 때문이다.

    ★ 규칙: assistant 턴의 tool_use 블록 하나하나에 대해, **바로 다음 메시지**에
      같은 tool_use_id 를 가진 tool_result 가 있어야 한다. 짝이 깨지면
      "tool_result block missing corresponding tool_use" 같은 400 이 난다.
      (컨텍스트 압축이 이 짝을 갈라놓기 쉽다 — level3 의 compact() 참고)
    """
    content = []
    for tid, out, err in results:
        content.append({
            "type": "tool_result",
            "tool_use_id": tid,
            # 빈 문자열도 거절 대상이다. 도구가 아무것도 안 돌려줬으면 그렇다고 적는다.
            "content": out if out else "(출력 없음)",
            **({"is_error": True} if err else {}),
        })
    return {"role": "user", "content": content}


# ──────────────────────────────────────────────────────────────
# 재시도해도 되는 오류인지 판정
# ──────────────────────────────────────────────────────────────
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def is_retryable(e: BaseException) -> bool:
    """★ 모든 예외를 재시도하면 안 된다.
    잘못된 히스토리(400)나 잘못된 키(401)는 100번 해도 100번 실패하고,
    내 코드의 TypeError 는 재시도로 절대 안 고쳐진다. 재시도는
    '기다리면 될 수도 있는 것'에만 쓴다.
    """
    if isinstance(e, TransientError):
        return True
    try:
        import anthropic
    except ImportError:
        return False
    if isinstance(e, (anthropic.APIConnectionError, anthropic.RateLimitError)):
        return True
    if isinstance(e, anthropic.APIStatusError):
        return getattr(e, "status_code", None) in RETRY_STATUS
    return False
