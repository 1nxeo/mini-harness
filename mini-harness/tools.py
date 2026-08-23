"""
tools.py — 손(도구) + 샌드박스
================================================================
여기가 하네스에서 가장 위험한 층이다. 모델에게 파일 쓰기와 셸을 쥐여주는
순간 `rm -rf` 도 가능해진다.

★ 먼저 정직하게 밝혀둘 것 (학습용으로 매우 중요) ★
이 파일의 Sandbox 는 **파일 도구(read/write/list)에 대해서만** 경로를 가둔다.
run_bash 는 `cwd=root` 로 실행될 뿐이고, 셸 안에서 `cat ../secret` 이나
`cat /etc/passwd` 는 그냥 된다. cwd 는 감옥이 아니다.
아래 check_command 는 문자열 패턴 차단(deny-list)일 뿐이라 우회도 쉽다
(`rm --recursive --force`, `find . -delete`,
 `python3 -c "import shutil;shutil.rmtree('.')"` 등 전부 통과한다).

즉 **셸을 주는 순간 진짜 격리는 프로세스 밖에서 해야 한다** —
컨테이너/VM, 별도 유저 권한, seccomp, 네트워크 허용목록, 사용자 승인.
여기 있는 것은 "이런 층이 필요하다"를 보여주는 최소 예시이고,
그 자체로 안전 보장이 아니다.
"""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable

    @property
    def schema(self):
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class Sandbox:
    """파일 도구의 경로를 작업 폴더 안으로 제한하는 관문."""

    # deny-list 는 근본적으로 우회 가능하다. 실무에서는 allow-list
    # (허용된 명령만 통과)를 쓰고, 그 위에 프로세스 격리를 얹는다.
    BLOCKED = [
        r"\brm\s+-[rf]", r"\brm\s+--recursive", r"\brm\s+--force",
        r"\bmkfs\b", r"\bdd\s+if=", r":\(\)\s*\{", r"\bshutdown\b",
        r"\breboot\b", r">\s*/dev/sd", r"\bchmod\s+777\s+/",
        r"\bcurl\b", r"\bwget\b", r"\bfind\b.*-delete", r"\bshutil\.rmtree",
    ]

    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, rel: str) -> Path:
        """상대 경로를 절대 경로로 바꾸면서, 루트 밖으로 나가는지 검사한다.

        ★ 흔한 버그: `str(p).startswith(str(self.root))` 로 검사하면
          root=/tmp/proj 일 때 /tmp/proj_secret 이나 /tmp/projects 가 통과한다
          (문자열 접두어만 맞으면 되니까). 실제로 뚫린다.
          그래서 경로를 정규화한 뒤 '부모 관계'로 검사한다.
        """
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError(f"작업 폴더 밖 접근 차단: {rel}")
        return p

    def check_command(self, cmd: str):
        for pat in self.BLOCKED:
            if re.search(pat, cmd):
                raise PermissionError(f"위험한 명령 차단: {cmd!r} (패턴 {pat})")
        # 데모 수준의 추가 방어. 셸에서의 경로 탈출을 '조금' 어렵게 만든다.
        # 진짜 격리가 아님을 다시 강조한다 — 파이프/변수/인코딩으로 우회 가능.
        if re.search(r"(^|[\s=/'\"])\.\.(/|$)", cmd):
            raise PermissionError(f"상위 폴더 참조 차단: {cmd!r}")


def make_tools(sandbox: Sandbox, timeout: int = 30, max_out: int = 6000):
    """샌드박스에 묶인 도구 묶음을 만든다."""

    def _truncate(s: str) -> str:
        """★ 조용한 잘림은 모델에게 하는 거짓말이다. 잘렸으면 잘렸다고 말한다."""
        if len(s) <= max_out:
            return s
        return s[:max_out] + (
            f"\n\n...(전체 {len(s)}자 중 {len(s) - max_out}자 생략됨. "
            f"범위를 좁혀서 다시 읽거나 grep 을 쓰세요)"
        )

    def _list_dir(path: str = "."):
        target = sandbox.resolve(path)
        if not target.is_dir():
            return f"폴더가 아닙니다: {path}"
        items = sorted(target.iterdir())
        if not items:
            return "(빈 폴더)"
        return _truncate("\n".join(
            f"{'d' if i.is_dir() else '-'} {i.relative_to(sandbox.root)}" for i in items
        ))

    def _read_file(path: str):
        text = sandbox.resolve(path).read_text(encoding="utf-8")
        if not text:
            # 빈 문자열을 tool_result 로 돌려주면 API가 거절한다.
            return "(빈 파일)"
        # 줄 번호를 붙여준다. 모델이 '몇 번째 줄'을 정확히 말할 수 있게 하는 배려.
        return _truncate("\n".join(
            f"{n:4d} | {line}" for n, line in enumerate(text.splitlines(), 1)
        ))
    
    # ── 내가 짠 버전 (보관용) ─────────────────────────────────
    # def _grep(path: str, pattern: str):
    #     text = sandbox.resolve(path).read_text(encoding="utf-8")
    #     if not text:
    #         # 빈 문자열을 tool_result 로 돌려주면 API가 거절한다.
    #         return "(빈 파일)"
    #     # 줄 번호를 붙여준다. 모델이 '몇 번째 줄'을 정확히 말할 수 있게 하는 배려.
    #     _truncate("\n".join(
    #         f"{n:4d} | {line}" for n, line in enumerate(text.splitlines(), 1) if pattern in line
    #     ))
    #
    #     return
    #
    # 잘한 것: 인자를 pattern 으로 바꾼 것, if pattern in line 필터를 붙인 것.
    # 남은 문제 2가지:
    #   1) _truncate(...) 앞에 return 이 없다 → 계산해놓고 버린 뒤 None 을 반환한다.
    #      도구가 None 을 반환하면 루프에서 str(None) = "None" 이 모델에게 간다.
    #   2) '매치 0건'을 못 잡는다. if not text 가드는 *파일이* 비었을 때만 잡아주고,
    #      파일에 내용이 있는데 *결과가* 비는 경우(= 못 찾음)는 그대로 통과해
    #      빈 문자열이 반환된다 → 실제 API가 거절한다.

    def _grep(path: str, pattern: str, regex: bool = False):
        """파일에서 pattern 이 든 줄만 골라 줄 번호와 함께 반환한다.

        regex=False (기본) : pattern 을 '문자 그대로' 취급해 부분 일치를 찾는다.
        regex=True         : pattern 을 정규식으로 해석한다.
                             예) r"def (average|percent)"   두 함수만
                                 r"return .*/"              나눗셈이 있는 return 줄
                                 r"^import "                줄 맨 앞의 import

        read_file 은 파일 전체를 대화에 밀어넣지만 grep 은 필요한 줄만
        돌려주므로, 파일이 클수록 컨텍스트를 크게 아낀다.
        """
        text = sandbox.resolve(path).read_text(encoding="utf-8")

        # ★ 두 모드를 한 줄로 합치는 요령: 단순 검색이면 re.escape 로
        #   정규식 특수문자를 전부 무력화한다. 그러면 "a.b" 의 점이
        #   '아무 문자 하나'가 아니라 진짜 점으로 취급되어,
        #   결과적으로 `pattern in line` 과 정확히 같은 의미가 된다.
        try:
            rx = re.compile(pattern if regex else re.escape(pattern))
        except re.error as e:
            # ★ 모델이 깨진 정규식을 보내는 일은 흔하다(괄호 안 닫기 등).
            #   예외로 터뜨려도 루프가 잡아주긴 하지만, 이렇게 '무엇이 왜
            #   잘못됐고 어떻게 하면 되는지'를 적어 돌려주면 모델이 다음
            #   턴에서 스스로 고쳐 재시도한다. 도구의 에러 메시지도 UI다.
            return (f"정규식이 올바르지 않습니다: {pattern!r} ({e}). "
                    f"패턴을 고치거나, regex 를 빼고 단순 문자열로 검색하세요.")

        # ★ join 하기 전에 리스트로 먼저 받는다.
        #   그래야 매치 개수를 셀 수 있고, 0건인지도 판단할 수 있다.
        #   바로 join 해버리면 "결과가 빈 문자열"인지 알 방법이 없다.
        hits = [f"{n:4d} | {line}"
                for n, line in enumerate(text.splitlines(), 1)
                if rx.search(line)]

        if not hits:
            # ★ 빈 문자열을 tool_result 로 돌려주면 API가 거절한다.
            #   게다가 '못 찾았다'는 사실 자체가 모델에겐 유용한 정보다
            #   (= 잘못 짚었으니 다른 패턴으로 다시 시도하라는 신호).
            mode = "정규식" if regex else "문자열"
            return f"{mode} '{pattern}' 과(와) 일치하는 줄이 없습니다: {path}"

        # 몇 줄이 걸렸는지 먼저 알려주면 모델이 '범위를 더 좁힐지'를 판단하기 쉽다.
        return _truncate(f"{path} — {len(hits)}줄 일치\n" + "\n".join(hits))

    def _write_file(path: str, content: str):
        p = sandbox.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"{path} 에 {len(content)}자 저장 완료"

    def _edit_file(path: str, old_string: str, new_string: str):
        """★ 전체 덮어쓰기(write_file)보다 안전한 편집 방식.
        old_string 이 정확히 한 번만 나타나야 한다. 0번이면 잘못 짚은 것이고,
        2번 이상이면 어느 쪽을 고칠지 모호하다 — 둘 다 조용히 엉뚱한 결과를
        만들므로, 애매하면 실패시키고 모델에게 다시 정하게 하는 게 옳다.
        """
        p = sandbox.resolve(path)
        text = p.read_text(encoding="utf-8")
        n = text.count(old_string)
        if n == 0:
            return "실패: old_string 을 찾을 수 없습니다. read_file 로 정확한 내용을 확인하세요."
        if n > 1:
            return f"실패: old_string 이 {n}번 나타나 모호합니다. 앞뒤 문맥을 더 붙여 유일하게 만드세요."
        p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"{path} 1곳 수정 완료"

    def _run_bash(command: str):
        sandbox.check_command(command)
        r = subprocess.run(
            command, shell=True, cwd=sandbox.root,
            capture_output=True, text=True, timeout=timeout,
        )
        # 종료 코드를 반드시 같이 준다. 이게 없으면 모델은 성공/실패를 구분 못 한다.
        return _truncate(
            f"exit_code: {r.returncode}\n"
            f"--- stdout ---\n{r.stdout.strip() or '(없음)'}\n"
            f"--- stderr ---\n{r.stderr.strip() or '(없음)'}"
        )

    return [
        Tool("list_dir", "폴더 내용을 나열한다.",
             {"type": "object",
              "properties": {"path": {"type": "string", "description": "상대 경로. 기본값 '.'"}}},
             _list_dir),
        Tool("read_file", "텍스트 파일을 줄 번호와 함께 읽는다.",
             {"type": "object",
              "properties": {"path": {"type": "string", "description": "작업 폴더 기준 상대 경로"}},
              "required": ["path"]},
             _read_file),
        # ★ 스키마는 '모델이 이 도구를 어떻게 쓸지' 결정하는 유일한 근거다.
        #   여기서 pattern 을 빠뜨리면 모델은 path 만 넘기고 → TypeError 가 난다.
        #   description 도 read_file 과 똑같이 두면 모델이 둘을 구분하지 못하므로,
        #   '언제 read_file 대신 이걸 써야 하는지'를 문장에 담는다.
        Tool("grep",
             "파일에서 특정 패턴이 든 줄만 줄 번호와 함께 찾는다. "
             "파일이 크고 필요한 부분이 일부일 때, read_file 로 전체를 읽는 대신 이걸 쓴다.",
             {"type": "object",
              "properties": {"path": {"type": "string", "description": "작업 폴더 기준 상대 경로"},
                             "pattern": {"type": "string",
                                         "description": "찾을 패턴. 기본은 문자 그대로의 부분 일치이며, "
                                                        "regex=true 일 때만 정규식으로 해석된다."},
                             # ★ 선택 인자는 required 에 넣지 않는다. 넣으면 모델이
                             #   매번 억지로 채워야 하고, 빠뜨리면 호출이 실패한다.
                             "regex": {"type": "boolean",
                                       "description": "true 면 pattern 을 정규식으로 해석한다. "
                                                      "예: 'def (average|percent)'. 기본값 false."}},
              "required": ["path", "pattern"]},
             _grep),
        Tool("write_file", "파일을 새로 쓴다(기존 내용을 전부 덮어쓴다). 완성된 전체 내용을 넘겨야 한다.",
             {"type": "object",
              "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
              "required": ["path", "content"]},
             _write_file),
        Tool("edit_file",
             "파일의 한 곳만 치환한다. old_string 은 파일 안에서 유일해야 한다. "
             "부분 수정에는 write_file 보다 이걸 쓰는 게 안전하다.",
             {"type": "object",
              "properties": {"path": {"type": "string"},
                             "old_string": {"type": "string", "description": "바꿀 원본 문자열(유일해야 함)"},
                             "new_string": {"type": "string", "description": "새 문자열"}},
              "required": ["path", "old_string", "new_string"]},
             _edit_file),
        Tool("run_bash",
             "작업 폴더에서 셸 명령을 실행하고 종료코드/stdout/stderr를 반환한다.",
             {"type": "object",
              "properties": {"command": {"type": "string"}},
              "required": ["command"]},
             _run_bash),
    ]


def fresh_workdir(src: Path) -> Path:
    """원본을 건드리지 않도록 임시 폴더에 복사해서 그 안에서만 작업한다.

    부수 효과: 데모를 몇 번이고 똑같이 재현할 수 있다.
    (원본 demo_project 는 영원히 '버그 있는 상태'로 남는다)

    주의: /tmp/harness_* 는 자동 삭제하지 않는다. 실행 흔적을 직접
    들여다보게 하려는 의도이니, 주기적으로 지워주면 된다.
    """
    dst = Path(tempfile.mkdtemp(prefix="harness_"))
    shutil.copytree(src, dst / src.name,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dst / src.name
