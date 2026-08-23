# AI 하네스(Harness) 완전 학습 가이드

> 파이썬으로 직접 돌려보면서 배우는 에이전트 하네스의 구조
> 개념 → 부품 → 코드 4단계 → 자체 검증 → 실습 과제
> 문서에 나오는 모든 코드는 실제로 실행·검증된 것입니다 (`validate.py` 검사 23개 전부 통과)

---

## 0. 이 문서 사용법

이 문서는 **읽기만 하는 문서가 아닙니다.** 같이 들어있는 파이썬 파일은 전부 실제로 돌아가고, **API 키가 없어도 돌아갑니다.** 가짜 모델(`FakeModel`)이 미리 정해진 시나리오를 뱉어주기 때문에, 모델 비용·네트워크 없이 하네스의 '배관'만 따로 관찰할 수 있습니다.

권장 학습 경로:

1. **1~4장**을 읽어 개념을 잡습니다 (20분)
2. **6장부터** 각 레벨을 *먼저 실행해보고* → 출력을 보고 → 해설을 읽습니다
3. 각 레벨 해설 끝의 "직접 바꿔보기"를 실제로 손으로 고쳐봅니다
4. **10장(자체 검증)** 을 돌려보고, 11장의 "실제로 있었던 버그"를 읽습니다 ← 여기가 제일 재밌습니다
5. **13장 실습 과제**를 위에서부터 풉니다
6. 마지막에 `--real` 로 실제 모델을 붙여봅니다

파일 구조:

```
mini-harness/
├── README.md                 ← 이 문서
├── model.py                  ← 모델 어댑터 (가짜/진짜 공통 인터페이스)
├── tools.py                  ← 도구 + 샌드박스
├── level0_bare.py            ← 하네스 없음 (그냥 API 호출)
├── level1_loop.py            ← 도구 1개 + 루프          ★ 하네스의 심장
├── level2_agent.py           ← 도구 5개 + 검증 게이트    ★ 일하는 에이전트
├── level3_production.py      ← 압축·예산·재시도·서브에이전트
├── validate.py               ← ★ 하네스를 검증하는 하네스 (23개 검사)
├── trace.jsonl               ← level3 실행 시 생성되는 실행 기록
└── demo_project/             ← 에이전트가 고칠 대상 (원본은 항상 '버그 있는 상태')
    ├── calc.py               ← 버그 있는 코드
    └── test_calc.py          ← 테스트 4개 중 2개 실패
```

바로 실행:

```bash
cd mini-harness
python3 level0_bare.py
python3 level1_loop.py
python3 level2_agent.py
python3 level3_production.py
python3 validate.py            # ← 전부 통과하는지 확인
```

추가 설치 없습니다(표준 라이브러리만 씁니다, Python 3.9+). `--real` 로 실제 모델을 붙일 때만 `pip install anthropic` 이 필요합니다.

---

## 1. 하네스란 무엇인가

**모델은 엔진, 하네스는 엔진을 뺀 자동차의 나머지 전부.**

엔진만 덜렁 있으면 부릉거리는 것 말고는 아무것도 못 합니다. 바퀴·핸들·브레이크·연료탱크·계기판이 붙어야 "이동"이라는 실제 일이 됩니다. LLM도 같습니다. 모델 자체는 놀랍도록 똑똑하지만 그것만으로는 아무 일도 못 하고, 주변에 붙어서 실제 작업을 가능하게 만드는 장치 일체가 하네스입니다.

말의 뿌리는 마구(馬具)입니다. 말은 힘만 세고, 마구를 채워야 수레를 끕니다. 소프트웨어에서는 오래전부터 **테스트 하네스**라는 말을 썼습니다 — 함수 하나를 테스트하려면 입력을 만들어 넣고, 가짜 DB를 붙이고, 결과를 정답과 비교하고, 리포트를 뽑는 틀이 필요하죠. 그 감싸는 껍데기가 하네스입니다. AI 하네스는 같은 개념을 모델에 적용한 것입니다. 벤치마크 논문에서는 **scaffold(스캐폴드)** 라는 말도 거의 같은 뜻으로 씁니다.

정의:

> **하네스 = 모델을 감싸서 "말하는 것"에서 "일하는 것"으로 바꿔주는 코드 인프라 일체.**
> 컨텍스트 구성 + 도구 + 루프 + 기억 관리 + 검증 + 안전장치 + 오케스트레이션.

---

## 2. 왜 필요한가 — 모델의 세 가지 결핍

API를 직접 열어보면 LLM은 이렇게 단순한 함수입니다.

```
텍스트 넣으면 → 텍스트 나옴. 끝.
```

여기에 심각한 결핍이 셋 있습니다.

### 결핍 1: 손이 없다

파일을 읽을 수도, 명령을 실행할 수도, 웹을 볼 수도 없습니다. "이 프로젝트 `requirements.txt` 좀 봐줘"라고 하면 모델은 파일을 못 보니까 **그럴듯한 내용을 상상해서 지어냅니다.** 악의가 아니라 그것밖에 할 수 있는 게 없어서입니다. 환각(hallucination)의 상당 부분이 실은 "손이 없어서 생기는 현상"입니다.

### 결핍 2: 기억이 없다

호출 하나하나가 완전한 백지입니다. 방금 무슨 말을 했는지 매번 통째로 다시 넣어줘야 압니다. 대화가 이어지는 것처럼 느껴지는 건 **누군가가 뒤에서 지난 대화를 매번 다시 밀어넣고 있기 때문**입니다. 그 누군가가 하네스입니다.

### 결핍 3: 한 발짝만 걷는다

"일단 이걸 해보고, 결과를 보고 다음을 결정하자"를 스스로 못 합니다. 호출 한 번에 응답 한 번, 그리고 죽습니다.

**하네스는 이 세 결핍을 메우는 배관 공사 전체입니다.** 손 = 도구, 기억 = 컨텍스트 관리, 여러 발짝 = 루프.

---

## 3. 하네스의 7개 부품 지도

| # | 부품 | 하는 일 | 없으면 생기는 증상 | 이 레포에서 |
|---|------|---------|-------------------|-------------|
| 1 | **컨텍스트 구성** | 매 호출마다 뭘 보여줄지 결정 (시스템 프롬프트, 대화, 관련 문서) | 프로젝트 규칙을 모르고 엉뚱한 스타일로 작업 | `SYSTEM` / `Harness.system` |
| 2 | **도구(Tool)** | 모델에게 손을 달아줌. 정의 + 실행 + 결과 반환 | 코드를 상상해서 지어냄 | `tools.py` |
| 3 | **루프** | 관찰→판단→행동을 반복. 여러 발짝 걷게 함 | 한 번 답하고 끝. 긴 작업 불가 | `run()` / `Harness.run()` |
| 4 | **컨텍스트 관리** | 요약·압축·외부 메모로 대화가 터지지 않게 | 30분 일하다 자기가 뭘 하던지 잊음 | `Harness.compact()` |
| 5 | **검증** | 모델의 자기 보고를 안 믿고 기계적으로 확인 | "고쳤습니다"라는 거짓 성공이 최종 결과가 됨 | `verify()` 게이트 |
| 6 | **안전** | 폴더 격리, 위험 명령 차단, 승인 요구 | `rm -rf` 사고, 정보 유출 | `Sandbox` |
| 7 | **오케스트레이션** | 서브 에이전트, 병렬 실행, 결과 합치기 | 큰 작업에서 컨텍스트가 먼저 터짐 | `spawn_subagent` |

이 표가 이 문서 전체의 목차입니다. 아래 레벨들은 이 부품을 하나씩 붙여나가는 과정입니다.

---

## 4. 핵심 추상화: 모델을 함수로 보기

코드를 보기 전에 딱 하나만 짚습니다. `model.py` 는 모델을 이렇게 못박습니다.

```python
model.call(messages, tools, system=None) -> Reply(text, tool_calls, raw_content, usage)
```

이 인터페이스만 고정해두면 뒤에 뭐가 있든 하네스 코드는 안 바뀝니다.

```python
class FakeModel:      # 스크립트대로 뱉는 가짜 → API 키 없이 배관 검증
class AnthropicModel: # 실제 API
```

**이게 첫 번째 설계 교훈입니다.** 하네스를 만들 때 모델 호출을 코드 곳곳에 직접 박아넣으면 테스트가 불가능해집니다. 어댑터 하나로 감싸두면 가짜 모델로 하네스만 따로 검증할 수 있습니다 — 테스트 하네스에서 mock을 쓰는 것과 완전히 같은 발상입니다. 10장의 `validate.py` 가 이 설계 덕분에 존재할 수 있습니다.

모델이 뱉는 것은 두 종류뿐입니다.

```python
@dataclass
class ToolCall:
    id: str          # 결과를 돌려줄 때 짝을 맞추는 식별자
    name: str        # 부르려는 도구
    args: dict       # 인자
```

### 모델 ID (2026년 8월 기준)

| 계열 | 최신 API ID |
|------|-------------|
| Opus | `claude-opus-5` |
| Sonnet | `claude-sonnet-5` |
| Haiku | `claude-haiku-4-5` |

Claude 4.6 세대부터 모델 ID가 **날짜 없는 형식**으로 바뀌었고, 이 형식은 별칭이 아니라 **그 자체가 고정 스냅샷**입니다(예전처럼 `-latest` 성격의 포인터가 아닙니다). 이 레포의 기본값은 `model.py` 의 `DEFAULT_MODEL` 에 있습니다.

---

## 5. ★ 대화 히스토리 불변식 — 실무에서 가장 많이 터지는 곳

이 절은 원래 계획에 없었는데, 코드를 검증하다 보니 **하네스 버그의 절반이 여기서 나온다**는 게 명확해져서 별도 장으로 뺐습니다. Anthropic API를 예로 들지만 다른 함수호출 API도 대동소이합니다.

### 규칙 1: `tool_use` 는 반드시 **바로 다음 메시지**에서 `tool_result` 로 답해야 한다

```python
{"role": "assistant", "content": [{"type": "tool_use", "id": "abc", ...}]}
{"role": "user",      "content": [{"type": "tool_result", "tool_use_id": "abc", "content": "..."}]}
```

id가 하나라도 짝을 못 찾으면 400입니다. 실제로 `tool_result block missing corresponding tool_use` / `Missing tool result block for tool use id` 류의 오류가 코딩 에이전트에서 가장 흔하게 보고되는 400입니다.

**함정**: 컨텍스트 압축이 이 짝을 갈라놓습니다. `messages[-6:]` 처럼 뒤에서 N개를 자르면, N에 따라 잘린 구간이 `tool_result` 로 시작할 수 있습니다 — 그 짝인 `tool_use` 는 방금 버린 것이죠. **압축은 반드시 짝 단위로 잘라야 합니다.** (9장, 11장 참고)

### 규칙 2: 빈 content 는 거절된다

`{"role": "assistant", "content": []}` 나 `tool_result` 의 `"content": ""` 는 "non-empty content" 위반입니다. 이게 실제로 생기는 경로:

- 모델이 텍스트도 도구호출도 없는 턴을 뱉는다 → 재조립하면 `content: []`
- 도구가 빈 파일을 읽는다 → `tool_result` content가 `""`

그래서 이 레포는 두 곳 모두에 폴백을 넣었습니다(`(내용 없음)`, `(빈 파일)`, `(출력 없음)`).

### 규칙 3: 응답 블록은 **그대로** 되돌려 넣어라

공식 패턴은 이겁니다.

```python
messages.append({"role": "assistant", "content": response.content})   # ← 원본 그대로
```

파싱해서 재조립하면 두 가지가 깨집니다.

- `text → tool_use → text` 처럼 섞인 순서가 뭉개짐
- **extended thinking을 켠 경우** `thinking` / `redacted_thinking` 블록과 그 `signature` 가 유실 → 다음 턴에서 400. 이것도 실전에서 자주 보고되는 오류입니다.

그래서 `model.py` 의 `Reply` 는 `raw_content` 필드로 원본 블록을 들고 있고, `assistant_message()` 는 그게 있으면 무조건 그걸 씁니다. 가짜 모델일 때만 재조립 폴백을 씁니다.

### 규칙 4: 비어 있는 파라미터는 아예 보내지 마라

`system=""`, `tools=[]` 같은 "존재하지만 빈" 값은 보내지 않는 편이 안전합니다. `AnthropicModel.call()` 이 kwargs를 조건부로 조립하는 이유입니다.

### 규칙 5: 첫 메시지는 user, 같은 역할을 연속시키지 마라

직접 API는 같은 역할 연속을 병합해주지만, Bedrock/Vertex 스타일 변환기나 프록시는 거절합니다. 압축 요약을 별도 `user` 메시지로 끼워넣으면 이 모양이 됩니다 — 그래서 이 레포는 **요약을 최초 지시 메시지 안에 접어 넣습니다.**

`validate.py` 의 `validate_history()` 가 이 다섯 규칙을 전부 기계적으로 검사합니다.

---

## 6. Level 0 — 하네스 없음

```bash
python3 level0_bare.py
```

출력:

```
============================================================
요청: demo_project 의 테스트가 실패한다. 원인을 찾아서 고쳐줘.
============================================================
테스트 실패의 흔한 원인은 다음과 같습니다:
1. ZeroDivisionError — 0으로 나누는 경우
2. import 경로 문제
3. 부동소수점 비교 오차
코드를 붙여주시면 더 정확히 봐드릴 수 있습니다.
============================================================
모델 호출 횟수: 1
실제로 바뀐 파일: 없음   ← 손이 없으니 아무것도 못 한다
```

코드 전체가 사실상 이 한 줄입니다.

```python
reply = model.call([{"role": "user", "content": TASK}], tools=[])
```

**관찰**: 답변이 틀린 건 아닙니다. 하지만 일반론이고, 문제는 그대로 남습니다. 이게 우리가 "챗봇"이라 부르는 상태입니다.

---

## 7. Level 1 — 루프와 첫 번째 손 ★핵심★

```bash
python3 level1_loop.py
```

### 도구는 두 겹이다

```python
@dataclass
class Tool:
    name: str
    description: str      # ← 모델에게 보여주는 쪽
    input_schema: dict    # ← 모델에게 보여주는 쪽
    run: Callable         # ← 실제로 실행되는 파이썬 함수
```

앞의 셋은 **모델용 설명서**, `run` 은 **실제 구현**입니다. 모델은 `run` 을 보지 못하고 설명만 봅니다. 그래서 **설명이 부실하면 성능이 떨어집니다. 도구 설명은 프롬프트의 일부입니다.**

> ⚠️ Level 1의 `read_file` 에는 샌드박스가 없습니다. `pathlib` 은 오른쪽 인자가 절대경로면 그쪽이 이기므로 `read_file("/etc/passwd")` 가 그냥 됩니다. "손을 달아주는 것"만 보여주는 단계이고, 격리는 Level 2에서 붙입니다.

### 루프 — 이 20줄이 챗봇과 에이전트를 가른다

```python
def run(model, task, max_steps=10):
    messages = [{"role": "user", "content": task}]

    for step in range(1, max_steps + 1):
        reply = model.call(messages, tools=list(TOOLS.values()))   # ① 모델 호출
        messages.append(assistant_message(reply))                  # ② 대화에 기록

        if not reply.wants_tools:                                  # ③ 끝났으면 탈출
            return messages

        results = []
        for call in reply.tool_calls:
            tool = TOOLS.get(call.name)                            # ④ 조회와 실행을 분리
            if tool is None:
                results.append((call.id, f"없는 도구입니다: {call.name}", True))
                continue
            try:
                out, err = tool.run(**call.args), False            # ⑤ 하네스가 실행
            except Exception as e:
                out, err = f"{type(e).__name__}: {e}", True        # ⑥ 실패도 결과다
            results.append((call.id, str(out)[:4000], err))        # ⑦ 잘라서 넣는다

        messages.append(tool_result_message(results))              # ⑧ 되먹임 → ①로
```

놓치지 말아야 할 다섯 가지:

**(a) 실행 주체는 하네스다.** 모델은 "`read_file` 을 부르고 싶다"는 구조화된 요청만 뱉습니다. 파일을 실제로 여는 건 ⑤번의 파이썬 코드입니다. 모델은 끝까지 텍스트만 뱉습니다.

**(b) 실패도 결과로 되돌려준다.** ⑥번이 이 예제에서 가장 중요한 한 줄입니다. 실행해보면 없는 파일을 읽으려 하고 `FileNotFoundError` 를 받는 장면이 나오는데, 하네스가 죽지 않고 모델이 그 정보를 받아 다음 판단을 합니다. **에이전트가 자기 실수를 고칠 수 있는 건 오직 실패 정보가 되돌아오기 때문입니다.** 여기서 예외를 그냥 raise 하면 에이전트는 유리처럼 깨집니다.

**(c) 도구 조회와 도구 실행은 다른 try에 둔다.** ④번. `TOOLS[name].run(...)` 을 한 표현식으로 쓰고 `except KeyError` 를 잡으면, **도구 함수 안에서 난 `KeyError` 가 "없는 도구입니다"로 오보**됩니다. 실제로 이 레포 초안에 있던 버그입니다(11장).

**(d) 결과를 잘라 넣는다.** ⑦번. 로그 파일 하나 읽었다가 컨텍스트가 통째로 날아가는 일을 막습니다. 단, **잘랐으면 잘랐다고 말해야 합니다** — `tools.py` 의 `_truncate()` 는 `...(전체 N자 중 M자 생략됨)` 을 붙입니다. 조용한 잘림은 모델에게 하는 거짓말입니다.

**(e) 스텝 상한이 있다.** 무한 루프는 곧 무한 청구서입니다.

**Level 1의 한계**: 읽는 손만 있고 쓰는 손이 없습니다. 원인은 찾아냈지만 고칠 수 없습니다.

---

## 8. Level 2 — 일하는 에이전트 ★핵심★

```bash
python3 level2_agent.py
```

실제 출력(발췌):

```
작업 폴더(임시 복사본): /tmp/harness_xxx/demo_project

[1] 모델 먼저 폴더 구조를 봅니다.
[1] 🔧 list_dir {'path': '.'}
[2] 모델 테스트를 돌려 실패를 직접 확인합니다.
[2] 🔧 run_bash {'command': 'python3 -m unittest -q'}
[3] 모델 구현 코드를 읽습니다.
[3] 🔧 read_file {'path': 'calc.py'}
[4] 모델 average() 의 빈 리스트 처리를 추가합니다.
[4] 🔧 write_file {'path': 'calc.py', ...}
[5] 모델 고쳤습니다. 완료했습니다.

⟳ 검증 실패 → 모델에게 되돌려 보냄 (1/3)          ← ★

[6] 모델 percent() 도 같은 문제였네요. 함께 고칩니다.
[6] 🔧 write_file {'path': 'calc.py', ...}
[7] 모델 테스트를 다시 돌립니다.
[7] 🔧 run_bash {'command': 'python3 -m unittest -q'}
[8] 모델 4개 테스트 모두 통과했습니다.

✅ 검증 통과 (모델 호출 8회)

최종 결과: 성공
대화에 쌓인 메시지 수: 16   ← 이게 컨텍스트다. Level 3의 관리 대상.
```

### 8-1. 쓰는 손 — 도구 출력 설계가 절반이다

`run_bash` 의 반환값을 눈여겨보세요.

```python
return _truncate(f"exit_code: {r.returncode}\n"
                 f"--- stdout ---\n{r.stdout.strip() or '(없음)'}\n"
                 f"--- stderr ---\n{r.stderr.strip() or '(없음)'}")
```

**종료 코드를 반드시 같이 줍니다.** stdout만 주면 모델은 성공/실패를 구분 못 합니다. 같은 맥락으로 `read_file` 은 줄 번호를 붙여 돌려줍니다 — 모델이 "42번째 줄"을 정확히 말할 수 있게 하는 배려입니다.

`write_file` 과 `edit_file` 두 개가 있는 것도 의도적입니다.

```python
def _edit_file(path, old_string, new_string):
    n = text.count(old_string)
    if n == 0: return "실패: old_string 을 찾을 수 없습니다. read_file 로 확인하세요."
    if n > 1:  return f"실패: old_string 이 {n}번 나타나 모호합니다. 문맥을 더 붙이세요."
    ...
```

**유일성을 강제하는 게 핵심입니다.** 0번이면 잘못 짚은 것이고, 2번 이상이면 어느 쪽을 고칠지 모호합니다. 둘 다 조용히 엉뚱한 결과를 만들기 때문에, 애매하면 실패시키고 모델에게 다시 정하게 하는 편이 옳습니다. 전체 덮어쓰기(`write_file`)만 있으면 모델이 파일 일부만 써서 코드를 날리는 사고가 실제로 납니다.

### 8-2. 샌드박스 — 여기서 정직하게 짚을 것

```python
def resolve(self, rel):
    p = (self.root / rel).resolve()
    if p != self.root and self.root not in p.parents:
        raise PermissionError(f"작업 폴더 밖 접근 차단: {rel}")
    return p
```

**★ 이 레포 초안은 여기가 뚫려 있었습니다.** 원래는 이렇게 썼습니다.

```python
if not str(p).startswith(str(self.root)):     # ← 실제로 뚫린다
```

문자열 접두어만 맞으면 통과하므로, root가 `/tmp/proj` 일 때 **`/tmp/proj_secret` 과 `/tmp/projects` 가 통과합니다.** 실제로 확인한 결과:

```
구버전 startswith: 뚫림 → SECRET
수정판 parents  : 차단됨
```

교훈: **경로 검사는 문자열이 아니라 경로 관계로 해야 합니다.** `.resolve()` 로 정규화한 뒤 부모 관계를 봅니다.

**그리고 더 중요한 정직함**: 이 `Sandbox` 는 **파일 도구(read/write/edit/list)만** 가둡니다. `run_bash` 는 `cwd=root` 로 실행될 뿐이고, **cwd는 감옥이 아닙니다.** 셸 안에서 `cat /etc/passwd` 는 그냥 됩니다. `check_command` 의 차단 목록은 문자열 패턴 매칭이라 우회도 쉽습니다 — `rm --recursive --force`, `find . -delete`, `python3 -c "import shutil;shutil.rmtree('.')"` 처럼요(그래서 이 레포는 이 패턴들도 추가로 막았지만, 원리상 끝이 없습니다).

> **결론: 셸을 주는 순간 진짜 격리는 프로세스 밖에서 해야 합니다.** 컨테이너/VM, 별도 유저 권한, seccomp, 네트워크 허용목록, 사용자 승인 프롬프트. deny-list보다 allow-list. 이 레포의 `Sandbox` 는 "이런 층이 필요하다"를 보여주는 최소 예시이고, 그 자체로 안전 보장이 아닙니다.

`fresh_workdir()` 로 원본을 임시 폴더에 복사해서 그 안에서만 작업하는 것도 안전 장치입니다. 부수 효과가 좋습니다 — **원본 `demo_project` 는 영원히 버그 있는 상태로 남아서 데모를 몇 번이고 똑같이 재현할 수 있습니다.**

### 8-3. 검증 게이트 ★가장 중요★

```python
if not reply.wants_tools:                    # 모델이 "다 했다"고 함
    verify_rounds += 1
    ok, out = verify(sandbox)                # 하네스가 직접 테스트를 돌린다
    if ok:
        return True, messages
    if verify_rounds >= max_verify:
        return False, messages               # 무한 루프 방지
    messages.append({"role": "user", "content":
        f"아직 테스트가 실패합니다. `{VERIFY_CMD}` 결과:\n\n{out}\n\n원인을 다시 찾아 고치세요."})
    continue                                 # 루프로 되돌려 보낸다
```

가짜 시나리오에 **일부러 절반만 고치는 실수**를 심어놨습니다. 모델은 `average()` 만 고친 뒤 "고쳤습니다. 완료했습니다"라고 선언합니다. 검증 게이트가 없으면 여기서 작업이 끝나고, 사용자는 여전히 깨진 코드를 받습니다.

게이트가 있으면 하네스가 직접 `unittest` 를 돌리고, 종료 코드가 0이 아닌 걸 확인하고, **실패 출력을 그대로 모델에게 되돌려 보냅니다.** 모델은 그걸 보고 `percent()` 도 고칩니다.

**검증기는 절대 예외를 밖으로 흘리면 안 됩니다.**

```python
try:
    r = subprocess.run(VERIFY_CMD, ..., timeout=60)
except subprocess.TimeoutExpired:
    return False, "검증 명령이 60초 안에 끝나지 않았습니다(무한 루프 의심)."
```

이걸 안 감싸면, 에이전트가 무한루프 테스트를 써넣는 순간 **하네스 자체가 죽습니다.** (이것도 초안에 있던 버그입니다.)

### 직접 바꿔보기

- `SCRIPT` 에서 두 번째 수정 `Reply` 를 지워보세요 → 검증 한도 초과 경로 확인
- `SCRIPT` 에 `run_bash {"command": "rm -rf ."}` 를 넣어보세요 → 샌드박스 차단 확인
- `test_calc.py` 에 새 테스트를 추가하고 수정은 그대로 두세요 → 검증이 잡아내는지 확인

---

## 9. Level 3 — 실무형 하네스

```bash
python3 level3_production.py
```

실제 출력(발췌):

```
[main 1] 시스템 파일을 먼저 볼까요?
[main 1] 🔧 read_file {'path': '../../../etc/passwd'}
      ↳ 차단/실패: PermissionError: 작업 폴더 밖 접근 차단
[main 2] 폴더를 정리하겠습니다.
[main 2] 🔧 run_bash {'command': 'rm -rf .'}
      ↳ 차단/실패: PermissionError: 위험한 명령 차단
  ↻ 일시적 오류(429 rate_limit (가짜, 1회째)) — 0.1초 후 재시도 1/2
  ↻ 일시적 오류(429 rate_limit (가짜, 2회째)) — 0.1초 후 재시도 2/2
[main 3] 차단되었군요. 조사는 하위 에이전트에게 맡기겠습니다.
[main 3] 🔧 spawn_subagent {'purpose': '... 실패 원인을 특정하라'}
[sub 1] 🔧 list_dir · [sub 2] 🔧 read_file test_calc.py
[sub 3] 🔧 read_file calc.py · [sub 4] 🔧 run_bash unittest
[sub 5] 결론: average() 는 빈 리스트, percent() 는 whole==0 에서 …
[main 4] 보고를 받았습니다. average() 부터 고칩니다.
[main 4] 🔧 edit_file {...}
[main 5] 검증합니다.
  ⤵ [main] 압축: 4개 → 요약, 최근 6개 유지 (다음 압축까지 3개 여유)
[main 6] average 는 통과. percent 도 같은 문제라 이어서 고칩니다.
[main 8] 4개 테스트 모두 통과. 작업 완료.

✅ 성공 — API 요청 15/40회
루프 턴: main 8회 / 최종 컨텍스트 8개 메시지 (≈625 토큰, 누적 소비량이 아니라 '현재 크기')
```

`API 요청 15회` 인데 `루프 턴 8회`인 데 주목하세요. 차이 7 = 서브에이전트 5회 + 재시도 2회입니다. **루프 턴만 세면 청구서의 절반을 못 봅니다.**

### A. 컨텍스트 압축 — 짝을 깨지 않는 것이 전부다

원칙은 **"최초 목표와 최근 상황은 절대 버리지 않고, 중간을 압축한다"** 입니다. 최초 지시를 버리면 목표를 잊고, 최근 상황을 버리면 방금 한 일을 반복합니다. 그런데 순진하게 구현하면 5장의 규칙 1을 위반합니다.

```python
def compact(self):
    start = len(self.messages) - self.keep_tail
    # (1) 고아 tool_result 로 시작하지 않도록 시작점을 뒤로 민다
    while start < len(self.messages) and has_block(self.messages[start], "tool_result"):
        start += 1
    # user 로 시작하면 head(user)와 연속되므로 그 내용은 요약으로 흡수
    while start < len(self.messages) and self.messages[start].get("role") == "user":
        carried.append(plain_text(self.messages[start])); start += 1
    tail = self.messages[start:]
    # 응답 없는 tool_use 로 끝나면 잘라낸다 (반대 방향의 같은 위반)
    while tail and has_block(tail[-1], "tool_use") and not has_block(tail[-1], "tool_result"):
        tail = tail[:-1]
    ...
    # (3) 누적 요약을 (2) 최초 지시 안에 접어 넣는다
    self.summary = (self.summary + "\n" + piece).strip()
    head = {"role": "user", "content": f"{self.task}\n\n[지금까지의 경과 요약]\n{self.summary}"}
    self.messages = [head] + tail
```

세 가지를 동시에 지킵니다.

1. **짝 보존** — `tool_result` 로 시작하면 짝인 `tool_use` 가 이미 잘린 상태라 실제 API에서 400. 짝이 맞을 때까지 시작점을 민다.
2. **연속 user 턴 방지** — 요약을 별도 메시지로 끼우지 않고 최초 지시에 접어 넣는다.
3. **누적 요약** — 초안은 `dropped = messages[1:-4]` 에 **이전 요약까지 포함**시키면서 새 요약에 반영하지 않았습니다. 결과적으로 압축이 4번 일어나면 앞의 3번 요약이 영구히 사라졌습니다. 지금은 `self.summary` 에 계속 덧붙입니다.

> **왜 이게 미묘한가**: 초안 코드는 *운 좋게* 동작했습니다. 루프가 항상 메시지를 2개씩 붙이니까 `messages[-4:]` 가 늘 assistant에서 시작했던 거죠. `-3` 이나 `-5` 로 바꾸는 순간 깨집니다. **한 글자 깊이의 우연에 기대고 있던 것**이고, 그래서 `validate.py` 가 `keep_tail` 을 2~9까지 흔들며 전수 검사합니다.

실무에서는 이 요약을 모델에게 시킵니다("지금까지 한 일과 알아낸 사실을 300자로 요약하라"). 여기서는 오프라인 실행을 위해 결정적 요약을 쓰지만 원리는 같습니다. 더 나아간 기법으로 중요한 사실을 파일에 적어두고 필요할 때 다시 읽는 **외부 기억**이 있습니다 — 사람이 메모하는 것과 같습니다.

### B. 예산 — 재시도와 서브에이전트가 새는 구멍

```python
class Budget:
    def __init__(self, max_requests=40, max_context_tokens=40_000):
        self.requests = 0      # ★ 루프 턴이 아니라 '실제 API 요청' 수
```

두 가지가 초안의 구멍이었습니다.

- **재시도가 예산을 빠져나갔습니다.** 루프 턴 1회 = API 요청 1회로 셌으니, 재시도 3회짜리 하네스에서 `max_calls=25` 는 실제로 최대 75건을 허용했습니다. 그래서 지금은 `call_model()` 안에서, 시도마다 예산을 깎습니다.
- **서브 에이전트가 독립 예산을 가졌습니다.** 부모 25 × 자식 8 = 최악 200회. 지금은 `Budget` 객체 **하나**를 물려줍니다. `validate.py` 검사 8번이 이걸 확인합니다.

토큰 추정도 고쳤습니다.

```python
# ❌ 초안:  len(json) // 4        ← 영어/코드 기준. 한글은 4배 과소평가
# ✅ 수정:  ascii//4 + 비ascii    ← 한글은 대략 글자당 1토큰
```

한글 프롬프트에서 `//4` 를 쓰면 `max_tokens=60_000` 이 실제로는 200k+를 허용해서 **토큰 가드가 사실상 작동하지 않습니다.** 실측: 한글 1000자 → 1008 (수정판) vs 258 (초안). 물론 진짜 정답은 응답의 `usage` 값을 누적하는 것이고, 그래서 `Reply.usage` 가 있습니다.

### C. 재시도 — 무엇을 재시도하면 안 되는가

```python
def is_retryable(e):
    if isinstance(e, TransientError): return True
    if isinstance(e, (anthropic.APIConnectionError, anthropic.RateLimitError)): return True
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code in {408, 409, 429, 500, 502, 503, 504, 529}
    return False
```

**모든 예외를 재시도하면 안 됩니다.** 잘못된 히스토리(400)나 잘못된 키(401)는 100번 해도 100번 실패하고, 내 코드의 `TypeError` 는 재시도로 절대 안 고쳐집니다. 재시도는 '기다리면 될 수도 있는 것'에만 씁니다. "모든 루프에 상한을 두라"는 원칙을 내세우는 코드가 `except Exception` 으로 전부 재시도하고 있으면 앞뒤가 안 맞습니다(초안이 그랬습니다).

또 초안은 `wait = 2**attempt` 를 출력하고 `sleep(min(wait,1))` 을 실행했습니다 — **로그가 거짓말을 하고 있었습니다.** 지금은 `retry_sleep` 계수를 곱해서 실제 잠든 시간을 그대로 출력합니다.

오프라인에서 재시도를 눈으로 보려고 `FakeModel(script, flaky_at={3}, flaky_times=2)` 를 넣어놨습니다. 3번째 호출에서 일부러 두 번 실패합니다.

### D. 서브 에이전트 = 컨텍스트 방화벽

```python
sub = Harness(sub_model_factory(purpose), parent.sandbox.root, label="sub",
              budget=parent.budget, trace=parent.trace)     # ★ 예산·트레이스 공유
ok, answer = sub.run(f"다음을 조사해서 결론만 간결하게 보고하라: {purpose}")
return f"[서브 에이전트 보고]\n{answer}"
```

파일 20개를 뒤지는 일을 본체가 직접 하면 대화가 파일 내용으로 가득 찹니다. 하위 에이전트에게 시키면 **그 쓰레기는 하위 대화에서 소각되고, 본체에는 결론 몇 줄만 돌아옵니다.**

`trace.jsonl` 을 보면 이 효과를 정확히 셀 수 있습니다. 서브가 `read_file` 로 파일 두 개를 통째로 읽었지만, 본체 컨텍스트에 들어간 것은 5줄짜리 결론 문자열 하나입니다. (초안 README는 "본체 메시지가 7개뿐인 게 이 효과"라고 썼는데, 그건 **틀렸습니다** — 그 7개는 압축 결과였습니다. 서브에이전트 효과는 실재하지만 그 숫자가 근거는 아닙니다.)

이게 긴 작업을 가능하게 하는 핵심 트릭입니다. 독립적인 조사가 여러 개면 병렬로 띄우면 되는데, **파일을 쓰는 서브에이전트를 병렬로 돌리면 충돌합니다** — 각자 별도 작업 폴더를 줘야 합니다. 실제 코딩 에이전트가 git worktree를 쓰는 이유입니다.

### E. 트레이스 로그

```python
self.log("tool", name=c.name,
         args={k: str(v)[:120] for k, v in c.args.items()},   # ★ 인자도 잘라야 한다
         error=err, out=out[:300])
```

`trace.jsonl` 을 열어보면 무슨 도구를 왜 불렀고 뭐가 실패했는지 한 줄씩 남아있습니다. 에이전트가 이상하게 행동할 때 **디버깅할 수 있느냐 없느냐**가 이 로그에 달립니다. 하네스를 개선하려면 먼저 관측이 되어야 합니다.

초안은 출력만 자르고 인자는 통째로 남겨서, `write_file` 한 번에 로그가 소스코드 덤프가 됐습니다.

---

## 10. 하네스를 검증하는 하네스

```bash
python3 validate.py
```

```
[1] 샌드박스 격리 — 파일 도구             ✅ ×5   (../proj_secret 류 탈출 포함)
[2] 위험 명령 차단                        ✅ ×5
[3] 메시지 변환 — 빈 값 거절 모양         ✅ ×4
[4] 정상 실행 후 히스토리 유효성          ✅ ×2
[5] 압축 스트레스 (keep_tail 2~9 전수)    ✅      ← 36개 조합 전수 검사
[6] 재시도가 예산을 빠져나가지 않는가     ✅
[7] 예산 소진 시 폭주하지 않고 멈추는가   ✅
[8] 서브에이전트가 부모 예산 공유         ✅
[9] 토큰 추정이 한글을 과소평가 안 하는가 ✅
[10] 데모 원본이 그대로인가 (재현성)      ✅
✅ 전부 통과 (검사 23개)
```

핵심은 `validate_history()` 입니다. 5장의 다섯 가지 불변식을 기계적으로 검사합니다.

```python
# tool_use 하나하나는 '바로 다음' 메시지에 같은 id의 tool_result 가 있어야 한다
for i, m in enumerate(messages):
    ids = {b["id"] for b in blocks(m, "tool_use")}
    nxt = messages[i+1] if i+1 < len(messages) else {}
    answered = {b["tool_use_id"] for b in blocks(nxt, "tool_result")}
    if ids - answered:
        fail(f"응답 없는 tool_use {ids - answered}")
```

**이 검사기가 있으면 API 키 없이도 "실제 API에 보내면 400 날 히스토리"를 미리 잡을 수 있습니다.** 실무 하네스를 만들 때 가장 투자 대비 효과가 큰 코드입니다. 압축·요약·잘라내기 같은 컨텍스트 조작을 손댈 때마다 여기가 잡아줍니다.

---

## 11. 실제로 있었던 버그 목록

이 레포 초안을 검토했을 때 나온 것들입니다. **전부 실제 하네스에서 반복적으로 나오는 종류**라서 그대로 남겨둡니다.

| # | 버그 | 증상 | 심각도 |
|---|------|------|--------|
| 1 | `startswith` 경로 검사 | `/tmp/proj` 격리인데 `/tmp/proj_secret` 읽기·쓰기 가능 | **치명** |
| 2 | `run_bash` 는 격리 안 되는데 문서가 격리된다고 주장 | 독자가 폴더가 봉인됐다고 오해 | **치명(문서)** |
| 3 | 빈 `content` / 빈 `tool_result` | 실제 API 400 | 높음 |
| 4 | 응답 블록 재조립 | thinking 블록 유실 → 다음 턴 400 | 높음 |
| 5 | 압축이 짝을 깰 수 있음 | `keep_tail` 홀수면 400 | 높음 |
| 6 | 요약이 누적 안 됨 | 압축 반복 시 초반 정보 영구 소실 | 높음 |
| 7 | 압축 요약에 데모 목표가 하드코딩 | 다른 작업에 재사용하면 거짓 지시 주입 | 중간 |
| 8 | 재시도가 예산을 우회 | `max=25` 인데 실제 75건 청구 | 중간 |
| 9 | 서브에이전트 독립 예산 | 총액 통제 소실 (최악 200건) | 중간 |
| 10 | `except Exception` 전체 재시도 | 400/401/TypeError 를 3번씩 재시도 | 중간 |
| 11 | 로그가 실제 sleep과 불일치 | "2초 후 재시도"라며 0.1초 대기 | 중간 |
| 12 | `retries=0` 이면 `None` 반환 | `AttributeError` 크래시 | 중간 |
| 13 | `est_tokens` 가 한글 4배 과소평가 | 토큰 가드가 사실상 미작동 | 중간 |
| 14 | 검증기 타임아웃 미처리 | 무한루프 테스트에 하네스 자체가 사망 | 중간 |
| 15 | 도구 조회/실행을 한 try에 | 도구 내부 `KeyError` 가 "없는 도구"로 오보 | 낮음 |
| 16 | 트레이스가 인자를 안 자름 | 로그가 소스코드 덤프 | 낮음 |
| 17 | 조용한 잘림 | 모델이 전체를 봤다고 착각 | 낮음 |
| 18 | `Harness.system` 이 죽은 파라미터 | 넘겨도 아무 일 없음 | 낮음 |
| 19 | `__pycache__` 를 임시폴더에 복사 | 재현성 오염 | 낮음 |

패턴이 보입니다. **버그가 몰리는 곳은 딱 세 군데입니다.**

1. **컨텍스트를 조작하는 코드** (압축, 잘라내기, 요약) — 불변식을 깨기 쉽고, 깨져도 조용하다
2. **경계·안전 코드** (경로 검사, 명령 차단) — 문자열로 처리하려는 유혹이 강하다
3. **한도·예산 코드** (재시도, 서브에이전트) — 새는 경로가 눈에 안 보인다

그리고 **문서가 코드보다 먼저 낡습니다.** #2와 #11은 코드가 아니라 설명이 틀린 경우였는데, 배우는 사람에게는 코드 버그보다 더 해롭습니다.

---

## 12. 왜 지금 이 단어가 자주 나오는가 (근거 있는 숫자)

몇 년 전까지는 모델 성능이 압도적 변수였습니다. 지금은 프론티어 모델 간 순수 지능 차가 좁혀지면서, **같은 모델을 얼마나 잘 부리는지**가 결과를 더 크게 가릅니다. 여기에 대해 2026년에 나온 실증 논문("Stop Comparing LLM Agents Without Disclosing the Harness", Zhang 등)이 구체적인 숫자를 제시합니다.

| 관찰 | 숫자 |
|------|------|
| SWE-bench Pro, **모델 고정**(Claude Opus 4.5), 하네스만 SEAL → Claude Code 로 교체 | 45.9% → **55.4%** (+9.5%p) |
| SWE-bench Verified Mini (HAL 리더보드), 같은 모델의 스캐폴드 간 격차 | Claude Sonnet 4.5 **34점**, o4-mini **48점** |
| TerminalBench 2.0, 모델 고정, 프롬프트+미들웨어+검증만 개선 | **+13.7%p** |
| 프론티어 모델 3종 × 하네스 3종 통제 실험에서 하네스 유발 분산 / 모델 유발 분산 | **7.8배** |

같은 모델의 스캐폴드 간 격차가 34~48점이라는 건, **모델 간 차이보다 하네스 간 차이가 크다**는 뜻입니다. 그래서 **"모델은 상수, 하네스는 변수"** 라는 말이 돌고, 논문의 결론도 "하네스를 공개하지 않은 벤치마크 점수는 모델 비교 근거가 될 수 없다"입니다.

실무도 같습니다. 회사에서 AI 도입이 잘 안 될 때 원인은 대개 모델이 멍청해서가 아니라 하네스가 없어서입니다. 사내 데이터에 접근할 손이 없고, 결과를 검증할 장치가 없고, 실패했을 때 재시도하는 루프가 없는 상태로 "챗봇에 물어보기"만 하고 있는 것이죠.

흔한 오해 둘:

- **"하네스는 그냥 프롬프트 엔지니어링이다"** — 아닙니다. 프롬프트는 부품 하나(컨텍스트 구성)입니다. 도구 실행, 루프, 검증, 샌드박스는 코드로 만드는 인프라입니다. 11장의 버그 19개 중 프롬프트로 고칠 수 있는 건 하나도 없습니다.
- **"좋은 모델만 쓰면 하네스는 상관없다"** — 위 표가 반박합니다. 모델이 똑똑할수록 하네스가 주는 레버리지가 커집니다.

---

## 13. 하네스 설계 원칙 12가지

1. **모델 호출은 어댑터 하나로 감싼다.** 가짜 모델로 테스트할 수 있어야 한다.
2. **응답 블록은 원본 그대로 되돌려 넣는다.** 재조립하면 순서와 thinking 블록이 깨진다.
3. **히스토리 불변식을 코드로 검사한다.** `tool_use`↔`tool_result` 짝, 빈 content, 역할 교대.
4. **실패를 삼키지 말고 되돌려준다.** 에러 메시지는 모델의 자기수정 연료다.
5. **기계가 확인할 수 있는 신호로 판정한다.** 종료 코드, 테스트 통과, 스키마 검증. 모델의 "다 했습니다"는 신호가 아니다.
6. **검증기는 예외를 밖으로 흘리지 않는다.** 타임아웃까지 결과값으로 바꾼다.
7. **모든 루프에 상한을 둔다.** 그리고 상한은 '루프 턴'이 아니라 **'실제 청구 단위'**로 센다.
8. **재시도는 기다리면 될 수도 있는 오류에만.** 400/401/TypeError는 재시도로 안 낫는다.
9. **도구 설명은 프롬프트다.** 이름·설명·인자 설명을 사람이 읽고 바로 쓸 수 있을 만큼 쓴다.
10. **도구 출력은 구조화하고, 잘랐으면 잘랐다고 말한다.** 종료 코드·줄 번호·잘림 표시.
11. **위험한 동작은 기본 차단, 예외적 허용.** 그리고 **셸을 주면 격리는 프로세스 밖에서.**
12. **트레이스를 남긴다.** 관측 안 되는 건 개선도 안 된다.

---

## 14. 흔한 실패 모드와 처방

| 증상 | 진짜 원인 | 처방 |
|------|-----------|------|
| 없는 함수/파일을 지어낸다 | 읽는 손이 없다 | `read_file`, `list_dir`, `grep` 추가 |
| "고쳤습니다"라는데 안 고쳐졌다 | 검증 게이트가 없다 | 하네스가 직접 테스트 실행 |
| 같은 동작을 무한 반복 | 실패 정보가 안 돌아가거나 상한이 없다 | 에러 되먹임 + 상한 |
| 400 `missing tool_use/tool_result` | 압축이 짝을 갈랐다 | 짝 단위 압축 + `validate_history` |
| 400 `thinking block missing` | 응답 블록을 재조립했다 | `content=response.content` 그대로 |
| 400 `non-empty content` | 빈 도구 출력/빈 응답 턴 | 폴백 문자열 |
| 긴 작업 중간에 목표를 잊는다 | 압축이 최초 지시를 버렸다 | head 보존 + 누적 요약 |
| 컨텍스트 한도 초과로 죽는다 | 도구 출력이 무제한이다 | 잘라내기 + 서브에이전트 위임 |
| 청구서가 예상의 3배 | 재시도/서브에이전트가 예산 밖 | 공유 `Budget`, 요청 단위 계상 |
| 파일을 날린다 | 전체 덮어쓰기 도구뿐 | 유일성 강제 `edit_file` |
| 정보가 유출됐다 | 셸이 격리 안 됨 | 컨테이너/권한 분리, allow-list |
| 왜 이랬는지 알 수 없다 | 로그가 없다 | 트레이스 JSONL |

---

## 15. 실습 과제

난이도 순입니다. 전부 이 레포 안에서 할 수 있고, **각 과제를 끝낸 뒤 `python3 validate.py` 를 돌려 통과하는지 확인하세요.**

### ① `grep` 도구 추가 (쉬움)
`tools.py` 에 `grep(pattern, path)` 을 추가하고 시나리오에서 `read_file` 대신 써보세요. 파일이 커질수록 왜 `grep` 이 컨텍스트 효율이 좋은지 체감할 수 있습니다.

### ② `validate.py` 에 검사 하나 추가 (쉬움)
"도구 출력이 `max_out` 을 넘으면 잘림 표시가 반드시 붙는다"를 검사하세요.

### ③ 예산 초과를 우아하게 (보통)
예산이 소진되면 현재는 그냥 실패합니다. 마지막 요청 1건을 남겨서 "지금까지 한 일과 남은 일을 정리해 보고하라"를 시키고 그 결과를 반환하도록 고치세요.

### ④ 압축을 모델에게 맡기기 (보통)
`Harness.compact()` 가 결정적 요약 대신 모델 요약을 쓰도록 바꾸세요. **주의: 그 요약 호출도 예산에서 깎아야 합니다.** 압축 전/후 `est_tokens` 를 출력해 얼마나 줄었는지 보세요.

### ⑤ 승인 게이트 (보통)
`write_file`/`edit_file`/`run_bash` 실행 전에 `input()` 으로 승인을 받는 `--approve` 모드를 추가하세요. 실제 코딩 에이전트가 하는 일입니다.

### ⑥ 압축 깨뜨려보기 (보통) ★추천★
`compact()` 의 짝 보존 `while` 루프를 지우고 `keep_tail=5` 로 돌린 뒤 `validate.py` 를 실행하세요. **검사기가 정확히 무엇을 잡아내는지** 눈으로 보는 게 이 레포에서 가장 값진 순간입니다.

### ⑦ usage 기반 예산 (보통)
`est_tokens` 추정 대신 `Reply.usage` 누적으로 토큰 한도를 걸도록 바꾸세요. 가짜 모델에도 `usage` 를 채워 넣어야 합니다.

### ⑧ 검증자 패널 (어려움)
`verify_fn` 을 여러 개(테스트 통과 / 린트 / "이 수정이 요구사항을 실제로 만족하는가"를 판정하는 별도 모델 호출)로 만들고, 과반 통과를 성공으로 처리하세요.

### ⑨ 병렬 서브 에이전트 (어려움)
`spawn_subagent` 를 `concurrent.futures` 로 동시에 여러 개 띄우고 결과를 합치세요. 파일을 쓰는 서브에이전트를 병렬로 돌리면 충돌하니 각자 별도 작업 폴더(또는 git worktree)를 줘야 합니다. 공유 `Budget` 의 스레드 안전성도 생각해보세요.

### ⑩ 실제 모델 붙이기 (실전)
```bash
pip install anthropic && export ANTHROPIC_API_KEY=sk-ant-...
python3 level2_agent.py --real
python3 level3_production.py --real
```
바뀌는 건 `FakeModel` → `AnthropicModel` 한 줄뿐입니다. 그리고 가짜 모드에서 안 보이던 게 보입니다 — 모델이 예상 밖 순서로 도구를 부르고, 스키마를 어기고, `write_file` 로 파일 일부만 씁니다. **그 관찰이 곧 하네스 개선 목록입니다.**

---

## 16. 용어집

| 용어 | 뜻 |
|------|-----|
| **Harness / Scaffold** | 모델을 감싸 실제 작업을 가능하게 하는 코드 인프라 전체 |
| **Tool / Function calling** | 모델이 호출을 요청하고 하네스가 실행하는 외부 기능 |
| **Agent loop** | 모델 호출 → 도구 실행 → 결과 되먹임의 반복 |
| **Context window** | 한 번의 호출에 넣을 수 있는 토큰 한도 |
| **Compaction** | 대화 중간을 요약으로 대체해 컨텍스트를 줄이는 것 |
| **tool_use / tool_result** | 도구 호출 요청 블록과 그 결과 블록. 짝이 맞아야 한다 |
| **Sandbox** | 도구 실행을 격리해 사고를 막는 층 |
| **Subagent** | 하위 작업을 맡고 결론만 반환하는 별도 루프 (컨텍스트 방화벽) |
| **Verifier / Gate** | 결과를 기계적으로 판정하는 검증 장치 |
| **Trace** | 에이전트가 무엇을 했는지 남긴 실행 기록 |

---

## 17. 최종 체크리스트

- [ ] 필요한 도구가 다 있고, 설명이 명확한가
- [ ] 자기가 한 일의 결과를 실제로 확인할 눈이 있는가
- [ ] 실패를 감지하고 되먹여서 다시 시도하는가
- [ ] 히스토리 불변식을 검사하는 코드가 있는가
- [ ] 긴 작업에서 목표와 결정사항이 유지되는가
- [ ] 상한이 '실제 청구 단위'로 걸려 있는가 (재시도·서브에이전트 포함)
- [ ] 재시도가 재시도해도 소용없는 오류를 재시도하지 않는가
- [ ] 위험한 동작이 기본 차단되어 있고, 격리가 프로세스 밖에도 있는가
- [ ] 무슨 도구를 왜 불렀는지 사람이 따라갈 수 있는가
- [ ] 모델을 갈아끼워도 코드가 안 바뀌는가

---

**한 줄 정리**: 하네스 = 컨텍스트 + 도구 + 루프 + 기억 관리 + 검증 + 안전 + 오케스트레이션. 모델은 텍스트를 뱉는 함수일 뿐이고, 그걸 "일"로 바꾸는 건 전부 이 코드입니다. 그리고 이 코드는 **반드시 자기 자신을 검증해야 합니다** — 11장의 버그 19개가 그 이유입니다.

---

## 참고 자료

- [Stop Comparing LLM Agents Without Disclosing the Harness (arXiv 2605.23950)](https://arxiv.org/html/2605.23950) — 12장의 모든 숫자 출처
- [Models overview — Claude Platform Docs](https://platform.claude.com/docs/en/about-claude/models/overview) — 모델 ID
- [Model IDs and versioning — Claude Platform Docs](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions) — 날짜 없는 ID 형식
- [Tool use overview — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) — `tool_use`/`tool_result` 포맷
- [Extended thinking blocks not preserved during tool use loops (claude-code #14264)](https://github.com/anthropics/claude-code/issues/14264) — 5장 규칙 3의 실제 사례
- [Anthropic API Error: Tool Result Block Missing Corresponding Tool Use Block (claude-code #65726)](https://github.com/anthropics/claude-code/issues/65726) — 5장 규칙 1의 실제 사례
- [Missing corresponding tool_use block for tool_result (claude-code #69828)](https://github.com/anthropics/claude-code/issues/69828)
