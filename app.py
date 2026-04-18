"""
수학 문제 PDF/이미지 → HWPX 변환 Streamlit 앱

요구 환경변수:
    ANTHROPIC_API_KEY  : Claude API 키

같은 디렉터리에 있어야 하는 파일:
    pdf_to_hwpx.py     : HWPX 생성 라이브러리 (기존 스크립트 그대로 사용)
    template.hwpx      : HWPX 템플릿 (또는 UI에서 업로드)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st
from anthropic import Anthropic

try:
    from streamlit_paste_button import paste_image_button as _paste_image_button
    PASTE_AVAILABLE = True
except ImportError:
    PASTE_AVAILABLE = False

# pdf_to_hwpx 스크립트를 라이브러리로 로드 (수정 없이 그대로 사용)
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import pdf_to_hwpx  # noqa: E402

# ────────────────────────────────────────────────────────────
# pdf_to_hwpx.latex_to_hwpeq 출력 보정 (monkey-patch)
# 스킬이 \mathrm{X} 를 "X" (따옴표) 로 변환하는데, 한컴 수식편집기에서
# 로만체로 렌더되지 않을 수 있음. 출력의 "..." 를 rm{...} 로 치환.
# Streamlit 재실행에도 재귀되지 않도록 원본은 모듈 속성으로 저장.
# ────────────────────────────────────────────────────────────
if not hasattr(pdf_to_hwpx, "_original_latex_to_hwpeq"):
    pdf_to_hwpx._original_latex_to_hwpeq = pdf_to_hwpx.latex_to_hwpeq


def _paren_boundary(out: str) -> str:
    """
    한글 수식편집기가 'left (' (공백 있음) 패턴에서 소괄호를 삼키는 버그를 회피.
    공백을 제거하여 'left(' / 'right)' 형태로 만듦.
    """
    out = re.sub(r"\bleft\s+\(", "left(", out)
    out = re.sub(r"\bright\s+\)", "right)", out)
    return out


def _patched_latex_to_hwpeq(latex: str) -> str:
    out = pdf_to_hwpx._original_latex_to_hwpeq(latex)

    # 1) "X" → rm{X}  (따옴표는 mathrm 변환에서만 나오는 것으로 가정)
    out = re.sub(r'"([^"]+)"', r"rm{\1}", out)

    # 2) UNION/INTER → cup/cap  (빅 합집합/교집합 → 일반 합집합/교집합)
    out = re.sub(r"\bUNION\b", "cup", out)
    out = re.sub(r"\bINTER\b", "cap", out)

    # 3) ^{prime...} → ' prime ... '  (위첨자 대신 키워드, 양쪽 공백)
    def _prime_sub(m: re.Match) -> str:
        count = len(m.group(1)) // 5  # "prime" = 5 글자
        return " " + " ".join(["prime"] * count) + " "

    out = re.sub(r"\^\{((?:prime)+)\}", _prime_sub, out)

    # 4) 단일 소문자 변수를 it{...} 로 감싸기 (hwpEQ 이탤릭 강제)
    #    - rm{...}, it{...} 안은 건드리지 않음
    #    - 이미 다른 단어(sin, theta, over, bar 등)의 일부면 건드리지 않음
    _prot: list[str] = []

    def _save(m: re.Match) -> str:
        idx = len(_prot)
        _prot.append(m.group(0))
        return f"\x00{idx}\x00"

    out = re.sub(r"(?:rm|it)\{[^{}]*\}", _save, out)
    out = re.sub(r"(?<![a-zA-Z])([a-z])(?![a-zA-Z])", r"it{\1}", out)
    out = re.sub(r"\x00(\d+)\x00", lambda m: _prot[int(m.group(1))], out)

    # 5) 공백 개선 — 보기 답답함 해결
    # 5-a) 쉼표 뒤 ~ 공백 (순서쌍/수열 등 시각적 간격)
    #      예: "x,y" → "x,~ y",  "a, b" → "a,~ b"
    #      한글 수식편집기가 "숫자,~command" 패턴에서 이상하게 파싱하는 현상 방지를 위해
    #      `~` 뒤에 공백 하나를 더 삽입.
    out = re.sub(r",\s*(?!~)", ",~ ", out)
    # 5-b) 집합 구분자 | 좌우 공백 추가 ({A|B} → {A ~|~ B})
    #      절댓값 left |...right | 은 제외
    out = re.sub(r"(?<!left)(?<!right) \| ", " ~|~ ", out)
    # 5-c) 좌/우 극한 ^-, ^+ → -, + (위첨자 제거)
    #      예: 2^- → 2-,  0^{+} → 0+
    #      x^{-1}, x^-1 등 뒤에 숫자/문자가 붙으면 건드리지 않음
    out = re.sub(r"\^([+\-])(?![A-Za-z0-9{])", r"\1", out)
    out = re.sub(r"\^\{([+\-])\}", r"\1", out)

    # 5-d) bar/hat/tilde 다음의 ^ 는 bar 밖으로 그룹 경계 추가
    #      bar {rm{X}}^{2} → {bar {rm{X}}}^{2}
    #      (한컴 수식편집기가 bar 범위를 ^까지 확장하는 현상 방지)
    def _wrap_bar_before_sup(m: re.Match) -> str:
        return "{" + m.group(1) + "}"

    # 1단계 중첩 {...} 까지 허용
    out = re.sub(
        r"((?:bar|hat|tilde|vec|dot|ddot)\s*\{(?:[^{}]|\{[^{}]*\})*\})(?=\s*[\^_])",
        _wrap_bar_before_sup,
        out,
    )

    # 6) 여분의 공백 정리
    out = re.sub(r" +", " ", out).strip()

    # 7) 괄호 경계 보호 (맨 마지막에 적용)
    #    한글 수식편집기가 `}(` 나 `{(` 패턴에서 `(` 를 빈 그룹 `{}` 로
    #    오인하는 버그 회피. 공백 정리 뒤에 실행해야 보존됨.
    out = _paren_boundary(out)
    return out


pdf_to_hwpx.latex_to_hwpeq = _patched_latex_to_hwpeq

# ────────────────────────────────────────────────────────────
# 상수 / 설정
# ────────────────────────────────────────────────────────────

VISION_MODEL = "claude-sonnet-4-5"
EXTRACT_MODEL = "claude-sonnet-4-5"
# 모델별 max_tokens 한도에 맞춰 설정. 한도를 넘으면 BadRequestError.
VISION_MAX_TOKENS = 4000
STRUCT_MAX_TOKENS = 8000
MAX_TOKENS = 4000  # 하위 호환용 (기존 참조)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PDF_EXTS = {".pdf"}
DEFAULT_TEMPLATE = HERE / "template.hwpx"

VISION_PROMPT = r"""이 이미지에 있는 **수학 문제(문항)** 만 정확히 인식하세요.

## ⚠️ 인식 범위 — 문제 본문만
- **문항(문제 본문)만** 추출합니다
- 다음은 **절대 포함하지 마세요**:
  - 풀이(Solution), 해설, 답안, 정답 설명
  - "풀이:", "해설:", "답:", "정답:", "Sol)", "Solution:" 이후 내용
  - 손글씨/필기로 적힌 풀이 과정
  - 교사 설명 박스, 해답지
- 문항 번호(예: 1., 2., 23., 서답형1) 와 본문 내용만 옮겨 적기

## ⚠️ 문제 누락 금지 (범위 내에서만)
페이지 안의 **모든 문항**을 번호 순서대로 포함:
- 번호가 1, 2, 3, ... 처럼 이어지면 모두 포함
- 짧은 문항이라도 건너뛰지 말 것

## ⚠️ 괄호 구조 보존 — 매우 중요
수식에 있는 **모든 괄호**를 원본 그대로 유지하세요.
- 중괄호 `\{ ... \}` 를 소괄호로 바꾸거나 생략하지 말 것
- 소괄호 `( ... )` 를 중괄호로 바꾸거나 생략하지 말 것
- `\left...\right` 가 있으면 그대로 유지
- 중첩된 괄호는 **안쪽부터 바깥쪽까지** 모든 종류를 정확히 반영

예: 원본 `lim{(합) + ln n}` → 반드시 `\lim\left\{\left(\sum...\right) + \ln n\right\}`
❌ `\lim\left(\sum... + \ln n\right)` 처럼 중괄호 삭제 금지

## 기본 규칙
- 문제 번호(예: 1., 2., 23.)를 그대로 유지
- 본문 텍스트는 한국어 그대로
- 수식은 LaTeX 인라인 형식(`$...$`)으로 표기
- 페이지 머리글/바닥글/페이지 번호는 생략

## ⭐ 수식 인식 — 매우 중요
수학적 의미를 가진 **모든** 기호·문자·숫자는 반드시 `$...$` 로 감싸세요.
절대 일반 텍스트로 두지 마세요.

포함 대상:
- 변수: `$x$, $y$, $a$, $n$, $k$` — 단 한 글자라도 반드시 수식
- 함수: `$f(x)$, $g(t)$, $f'(a)$, $f^{-1}(x)$`
- 수열·집합 원소: `$a_n$, $S_n$, $a_1, a_2, \ldots, a_n$`
- 숫자(수학적 의미): `$105$, $2021$, $\frac{1}{2}$`
- 연산·기호: `$+$, $-$, $\times$, $\div$, $\leq$, $\geq$`
- 구간·부등식: `$0 < x < 1$, $[0, 1]$`

예시:
- ❌ "x에 대한 이차함수 f(x)가 있다" (잘못됨)
- ✅ "$x$에 대한 이차함수 $f(x)$가 있다"
- ❌ "점 A와 점 B 사이 거리" (잘못됨)
- ✅ "점 $\mathrm{A}$와 점 $\mathrm{B}$ 사이 거리"

## ⭐ 기하 기호 로만체 — 매우 중요 (반복 강조)
점·선분·각·삼각형·사각형을 나타내는 **알파벳 대문자**는 반드시 `\mathrm{}` 로 감싸서 로만체로 처리하세요. **예외 없음.**

| 대상 | LaTeX 표기 |
|------|-----------|
| 점 A | `$\mathrm{A}$` |
| 점 P (아래첨자) | `$\mathrm{P}_0$`, `$\mathrm{P}_1$` |
| 좌표가 있는 점 | `$\mathrm{A}(1, 0)$`, `$\mathrm{B}(6, 5)$` |
| 선분 AB | `$\overline{\mathrm{AB}}$` |
| 직선 AB | `$\overleftrightarrow{\mathrm{AB}}$` |
| 반직선 AB | `$\overrightarrow{\mathrm{AB}}$` |
| 각 ABC | `$\angle \mathrm{ABC}$` |
| 삼각형 ABC | `$\triangle \mathrm{ABC}$` |
| 사각형 ABCD | `$\square \mathrm{ABCD}$` |
| 호 AB | `$\overset{\frown}{\mathrm{AB}}$` |
| 선분의 합 | `$\overline{\mathrm{AP}} + \overline{\mathrm{BP}}$` |

규칙:
- 여러 글자로 된 기하 라벨은 **한 번에** `\mathrm{AB}` 로 묶어 쓸 것 (`\mathrm{A}\mathrm{B}` 아님)
- 아래첨자가 있어도 점 글자는 로만체: `\mathrm{P}_0`, `\mathrm{A}_1`
- 변수 `$a, b, x, y, n, k$` 등은 **절대** `\mathrm` 붙이지 말 것 (이탤릭 유지)

⚠️ 흔한 실수 (이렇게 하지 마세요):
- ❌ `$A(1,0)$` → ✅ `$\mathrm{A}(1, 0)$`
- ❌ `$\overline{AP}+\overline{BP}$` → ✅ `$\overline{\mathrm{AP}} + \overline{\mathrm{BP}}$`
- ❌ `$P_0$` → ✅ `$\mathrm{P}_0$`
- ❌ `$\triangle ABC$` → ✅ `$\triangle \mathrm{ABC}$`

## ⭐ 집합 기호 — 반드시 정확하게
| 뜻 | 사용할 LaTeX | 잘못된 예 |
|----|-------------|----------|
| 합집합 | `\cup` | ❌ `\bigcup` (큰 연산자) |
| 교집합 | `\cap` | ❌ `\bigcap` (큰 연산자) |
| 여러 개 합집합 | `A \cup B \cup C` | ❌ `\bigcup ABC` |
| 공집합 | `\emptyset` 또는 `\varnothing` | |
| 부분집합 | `\subset`, `\subseteq` | |
| 원소 개수 | `n(\mathrm{A})` | |

예:
- ✅ `$n(\mathrm{A} \cup \mathrm{B} \cup \mathrm{C}) = 3$`
- ✅ `$\mathrm{A} \cap \mathrm{B} = \emptyset$`
- ❌ `$n(A\bigcup B\bigcup C)$` ← 이렇게 쓰지 말 것

## ⭐ 숫자는 문맥에 따라
- "제1사분면", "1학년" 등 — **수학 문맥**이면 수식: `제$1$사분면`
- "1등급", "페이지 1" 등 일반 문맥은 텍스트 그대로 가능
- 애매하면 수식 처리를 우선

## ⭐ 객관식 보기 — 매우 중요
①②③④⑤ 로 시작하는 객관식 보기는 본문 뒤에 **반드시 줄바꿈**해서 각각 **새 줄**에 쓰세요.

예시:
```
문제 본문 마지막 문장입니다. $f(5)$의 값은?
① $-2$
② $-1$
③ $0$
④ $1$
⑤ $2$
```

모든 ①②③④⑤ 앞에는 반드시 줄바꿈(`\n`) 이 들어가야 합니다. 본문 뒤에 바로 붙이지 마세요.

## 박스 / 그림
- 박스로 묶인 보기(ㄱ/ㄴ/ㄷ) 또는 조건(㈎㈏㈐)은 `[박스시작]` / `[박스끝]` 으로 감쌉니다
- 그래프·도형이 있는 위치에는 `[그림: 간단한 설명]` 이라고 표시"""

STRUCT_PROMPT = r"""다음 수학 문제 텍스트를 JSON 구조로 변환하세요.

## ⚠️ 변환 범위 — 문항만
- **문항(문제 본문)만** JSON 으로 변환합니다
- 다음은 **절대 JSON 에 포함하지 마세요**:
  - 풀이, 해설, 답안, 정답 설명
  - "풀이:", "해설:", "답:", "Sol)" 같은 라벨 이후 내용
  - 보조 해설이나 교사 주석
- 입력에 풀이/해설이 섞여 있어도 **문항 부분만** 골라서 변환

## ⚠️ 문항 누락 금지 (문항 범위 내에서)
- 문제 번호가 1, 2, 3, ... 이어질 때 **중간 번호를 건너뛰지 말 것**
- 짧은 문항이라도 포함
- 하나의 문항 안에서는 **내용 요약·축약 금지**. 모든 문장을 segments 로 분해

## ⚠️ LaTeX 괄호 구조 보존 — 매우 중요
원본에 있는 **모든 괄호 종류**를 그대로 유지하세요. 절대 단순화하거나 생략하지 마세요.

- **중괄호 `\{ ... \}`** → 반드시 `\{`, `\}` 그대로 유지
- **소괄호 `( ... )`** → `(`, `)` 그대로 유지
- **대괄호 `[ ... ]`** → `[`, `]` 그대로 유지
- **`\left...\right`** → 원본대로 유지

### 흔한 실수 (하지 마세요)
❌ 바깥 중괄호 생략:
원본: `\lim\left\{\left(\sum...\right) + \ln n\right\}`
잘못: `\lim\left(\sum... + \ln n\right)` ← 중괄호를 삭제하고 소괄호로 합쳐버림

✅ 올바른 예:
원본의 구조 **그대로** `\lim\left\{\left(\sum...\right) + \ln n\right\}` 유지

### 확인 체크리스트
각 수식을 JSON 에 넣기 전에:
1. 여는 괄호 개수 = 닫는 괄호 개수 인지 확인
2. 중첩된 괄호들의 타입(`{}`, `()`, `[]`)이 원본과 동일한지 확인
3. `\left...\right` 가 있으면 그대로 유지

## 출력 규칙
- 반드시 **JSON만** 반환 (마크다운 코드펜스 금지)
- 최상위 키는 `"problems"`, 값은 배열
- 각 항목은 아래 네 종류 중 하나

### 1) 일반 문단 (문제 본문)
```
{
  "number": "1",
  "main": true,
  "segments": [
    {"type": "text", "content": "다항식 "},
    {"type": "formula", "content": "f(x)=x^2+1"},
    {"type": "text", "content": "의 값은?"}
  ]
}
```

### 2) 객관식 보기 줄 (①②③④⑤)
⭐ **각 보기는 반드시 별도 problem 엔트리로 분리** (절대 본문 segments 뒤에 붙이지 말 것).
`main: false` 로 표시하며, 엔트리 하나가 곧 한 줄이 됩니다.

```
{"number": "1-①", "main": false, "segments": [
    {"type": "text", "content": "① "},
    {"type": "formula", "content": "-2"}
]},
{"number": "1-②", "main": false, "segments": [
    {"type": "text", "content": "② "},
    {"type": "formula", "content": "-1"}
]}
```

### 3) 박스 (보기/조건)
- `box_type`: "bogi" (ㄱ/ㄴ/ㄷ 등 보기) 또는 "condition" (㈎㈏㈐ 등 조건)
- `segments`는 **2차원 배열**(줄별 세그먼트)
```
{
  "number": "1-박스",
  "box": true,
  "box_type": "bogi",
  "segments": [
    [{"type":"text","content":"ㄱ. "}, {"type":"formula","content":"f(1)>0"}],
    [{"type":"text","content":"ㄴ. "}, {"type":"formula","content":"f(2)<0"}]
  ]
}
```

### 4) 그림 자리표시자
`[그림: …]` 표기가 있는 위치에 삽입:
```
{"type": "image_placeholder", "description": "…원문 그림 설명…"}
```

## ⭐ 세그먼트 분리 기준 — 엄격 준수
수학적 의미를 가진 기호·문자·숫자는 **반드시** `formula` 세그먼트로 분리하세요.
단 한 글자라도 수학적으로 쓰인 것이면 formula. 절대 text 세그먼트에 넣지 말 것.

### formula 로 처리할 것
- 변수 한 글자: `x`, `y`, `a`, `n`, `k` → 각각 `{"type":"formula","content":"x"}`
- 함수: `f(x)`, `g(t)`, `f'(a)`, `f^{-1}(x)`
- 수열 원소: `a_n`, `S_{10}`, `a_1, a_2, \ldots, a_n`
- 수학 의미의 숫자: `105`, `2021`, `\frac{1}{2}`
- 기하 기호 (반드시 `\mathrm{}` 로 로만체): `\mathrm{A}`, `\overline{\mathrm{AB}}`, `\triangle \mathrm{ABC}`, `\angle \mathrm{ABC}`
- 부등식·구간: `0 < x < 1`, `[a, b]`

### text 로 처리할 것
- 조사·명사·동사 등 **순수 한국어 연속체**: `"에 대하여 "`, `"의 값을 구하시오."`
- 문맥상 수학 기호가 아닌 구두점·괄호: `", "`, `"이고,"`, `"(단, "`

### ⭐ 기하 로만체 — 필수
점·선분·각·삼각형을 나타내는 알파벳 대문자는 반드시 `\mathrm{}` 로 감싸서 로만체 처리.

| 대상 | LaTeX 표기 |
|------|-----------|
| 점 A | `\mathrm{A}` |
| 선분 AB | `\overline{\mathrm{AB}}` |
| 직선 AB | `\overleftrightarrow{\mathrm{AB}}` |
| 반직선 AB | `\overrightarrow{\mathrm{AB}}` |
| 각 ABC | `\angle \mathrm{ABC}` |
| 삼각형 ABC | `\triangle \mathrm{ABC}` |

여러 글자 라벨은 **한 번에** `\mathrm{AB}` 처럼 묶어 쓸 것 (따로따로 쓰지 말 것).
변수(`a, b, x` 등)에는 `\mathrm` 붙이지 말 것.

### 변환 예시 1 — 본문 세그먼트 분리
입력: "다항식 f(x)가 (ax+b)(x+c)^2로 인수분해될 때, f(7)의 값을 구하시오."

❌ 잘못된 예:
```
[{"type":"text","content":"다항식 f(x)가 (ax+b)(x+c)^2로 인수분해될 때, f(7)의 값을 구하시오."}]
```

✅ 올바른 예:
```
[
  {"type":"text","content":"다항식 "},
  {"type":"formula","content":"f(x)"},
  {"type":"text","content":"가 "},
  {"type":"formula","content":"(ax+b)(x+c)^2"},
  {"type":"text","content":"로 인수분해될 때, "},
  {"type":"formula","content":"f(7)"},
  {"type":"text","content":"의 값을 구하시오."}
]
```

### 변환 예시 2 — 기하 로만체
입력: "점 A와 점 B를 잇는 선분 AB의 길이는 5이다"

✅ 올바른 예:
```
[
  {"type":"text","content":"점 "},
  {"type":"formula","content":"\\mathrm{A}"},
  {"type":"text","content":"와 점 "},
  {"type":"formula","content":"\\mathrm{B}"},
  {"type":"text","content":"를 잇는 선분 "},
  {"type":"formula","content":"\\overline{\\mathrm{AB}}"},
  {"type":"text","content":"의 길이는 "},
  {"type":"formula","content":"5"},
  {"type":"text","content":"이다"}
]
```

### 변환 예시 3 — 객관식 보기 분리
입력:
```
다음 중 옳은 것은? ① -2 ② -1 ③ 0 ④ 1 ⑤ 2
```

❌ 잘못된 예 (한 엔트리에 몰아넣기):
```
{"number":"1","main":true,"segments":[
    {"type":"text","content":"다음 중 옳은 것은? ① "},{"type":"formula","content":"-2"},
    {"type":"text","content":" ② "},{"type":"formula","content":"-1"}, ...
]}
```

✅ 올바른 예 (본문 + 보기 각각 별도 엔트리):
```
{"number":"1","main":true,"segments":[
    {"type":"text","content":"다음 중 옳은 것은?"}
]},
{"number":"1-①","main":false,"segments":[
    {"type":"text","content":"① "},{"type":"formula","content":"-2"}
]},
{"number":"1-②","main":false,"segments":[
    {"type":"text","content":"② "},{"type":"formula","content":"-1"}
]},
{"number":"1-③","main":false,"segments":[
    {"type":"text","content":"③ "},{"type":"formula","content":"0"}
]},
{"number":"1-④","main":false,"segments":[
    {"type":"text","content":"④ "},{"type":"formula","content":"1"}
]},
{"number":"1-⑤","main":false,"segments":[
    {"type":"text","content":"⑤ "},{"type":"formula","content":"2"}
]}
```

## formula 의 content 규칙
- LaTeX 본문만 (달러 기호 `$` 제외)
- 분수 `\frac{}{}`, 루트 `\sqrt{}`, 첨자 `_{}` / `^{}` 사용
- 기하 라벨은 `\mathrm{}` 로 감싸기
- JSON 문자열 안에서 백슬래시는 `\\` 로 이스케이프 (예: `"\\mathrm{A}"`, `"\\overline{\\mathrm{AB}}"`)
"""

# ────────────────────────────────────────────────────────────
# Claude 호출 헬퍼
# ────────────────────────────────────────────────────────────


def get_client() -> Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        # st.secrets 는 로컬에 secrets.toml 이 없으면 예외를 던짐
        try:
            key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:
            key = ""
    if not key:
        st.error(
            "`ANTHROPIC_API_KEY` 환경변수가 없습니다. "
            "Streamlit Cloud 의 Secrets 또는 로컬 환경변수로 설정하세요."
        )
        st.stop()
    return Anthropic(api_key=key)


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")


# Claude API 이미지 제한
MAX_IMAGE_DIM = 2000          # 긴 변 최대 픽셀
MAX_IMAGE_BYTES = 4_500_000   # 안전 여유(5MB 제한 대비)
JPEG_QUALITY_START = 88


def prepare_image_for_vision(path: Path) -> tuple[bytes, str]:
    """
    Claude API 에 전송하기 전에 이미지 크기를 줄입니다.
    - 긴 변이 MAX_IMAGE_DIM 초과면 비율 유지 축소
    - JPEG 로 변환하고 크기가 여전히 크면 품질을 낮춰 재압축
    - 원본이 이미 작고 JPEG/PNG 면 원본 그대로 반환
    """
    from io import BytesIO
    from PIL import Image

    # 원본 크기가 제한 미만이고 이미 JPEG/PNG 면 그대로 전송
    size = path.stat().st_size
    if size < MAX_IMAGE_BYTES and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return path.read_bytes(), _mime_for(path)

    img = Image.open(path)

    # RGBA/팔레트 → RGB (JPEG 저장용)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # 긴 변 축소
    w, h = img.size
    longest = max(w, h)
    if longest > MAX_IMAGE_DIM:
        scale = MAX_IMAGE_DIM / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # JPEG 로 품질 낮춰가며 압축
    quality = JPEG_QUALITY_START
    while quality >= 50:
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_IMAGE_BYTES:
            return data, "image/jpeg"
        quality -= 10

    # 그래도 크면 더 축소한 뒤 재시도
    img = img.resize((img.size[0] // 2, img.size[1] // 2), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=70, optimize=True)
    return buf.getvalue(), "image/jpeg"


def vision_recognize(client: Anthropic, image_paths: list[Path]) -> list[str]:
    """이미지들을 Claude Vision 으로 읽어 **페이지별** LaTeX 섞인 평문 리스트 반환."""
    outputs: list[str] = []
    progress = st.progress(0.0, text="Vision 인식 준비 중…")
    total = len(image_paths)
    for idx, path in enumerate(image_paths, 1):
        progress.progress((idx - 1) / total, text=f"Vision 인식 중… ({idx}/{total})")
        img_bytes, mime = prepare_image_for_vision(path)
        img_b64 = base64.b64encode(img_bytes).decode()
        resp = client.messages.create(
            model=VISION_MODEL,
            max_tokens=VISION_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": VISION_PROMPT},
                    ],
                }
            ],
        )
        outputs.append(resp.content[0].text)
    progress.progress(1.0, text="Vision 인식 완료")
    return outputs


def _parse_json_loose(text: str) -> dict[str, Any]:
    """코드펜스 제거 후 json.loads. 실패 시 가장 큰 균형잡힌 { ... } 블록만 시도."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 잘린 응답 복구 시도: 마지막 완전한 } 까지만 사용
        last = text.rfind("]")
        if last > 0:
            candidate = text[: last + 1]
            # problems 배열만 추출해보기
            m = re.search(r'"problems"\s*:\s*\[', candidate)
            if m:
                inner = candidate[m.end():]
                try:
                    arr = json.loads("[" + inner + "]" if not inner.endswith("]") else inner)
                    return {"problems": arr if isinstance(arr, list) else []}
                except Exception:
                    pass
        raise


def _is_response_truncated(resp) -> bool:
    """Claude 응답이 max_tokens 에 걸려 잘렸는지 판단."""
    reason = getattr(resp, "stop_reason", None)
    return reason == "max_tokens"


def structure_single(
    client: Anthropic,
    raw_text: str,
    max_tokens: int = STRUCT_MAX_TOKENS,
) -> tuple[list[dict[str, Any]], bool]:
    """
    페이지 하나의 평문을 JSON 으로 구조화.
    반환: (problems, truncated) — truncated 는 응답이 잘렸는지 여부.
    """
    resp = client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "user",
                "content": STRUCT_PROMPT + "\n\n입력:\n" + raw_text,
            }
        ],
    )
    text = resp.content[0].text
    truncated = _is_response_truncated(resp)
    data = _parse_json_loose(text)
    return data.get("problems", []), truncated


def _split_page_text(raw_text: str) -> list[str]:
    """페이지 텍스트를 문제 번호 기준으로 두 덩어리로 쪼갬 (잘림 대응용)."""
    # 줄 시작에 있는 "1.", "2.", "12." 같은 패턴으로 분리
    lines = raw_text.split("\n")
    problem_starts = []
    pat = re.compile(r"^\s*(\d+|서답형\d+)\s*[.\)]")
    for i, line in enumerate(lines):
        if pat.match(line):
            problem_starts.append(i)

    if len(problem_starts) < 2:
        # 분리할 수 없으면 절반으로 나눔
        mid = len(lines) // 2
        return ["\n".join(lines[:mid]), "\n".join(lines[mid:])]

    # 중간 지점을 기준으로 둘로 나눔
    mid_idx = problem_starts[len(problem_starts) // 2]
    return ["\n".join(lines[:mid_idx]), "\n".join(lines[mid_idx:])]


def structure_problems(client: Anthropic, raw_texts: list[str]) -> list[dict[str, Any]]:
    """
    페이지별로 따로 구조화한 뒤 하나의 problems 리스트로 병합.
    응답이 잘리면(`max_tokens`) 페이지를 반으로 쪼개서 재시도.
    """
    all_problems: list[dict[str, Any]] = []
    progress = st.progress(0.0, text="문제 구조화 준비 중…")
    total = len(raw_texts)
    for idx, page_text in enumerate(raw_texts, 1):
        progress.progress((idx - 1) / total, text=f"문제 구조화 중… ({idx}/{total})")
        if not page_text.strip():
            continue
        try:
            page_problems, truncated = structure_single(client, page_text)
        except json.JSONDecodeError as e:
            st.warning(f"페이지 {idx} 구조화 실패: {e}. 재시도 중…")
            truncated = True
            page_problems = []

        if truncated:
            st.info(f"페이지 {idx} 응답이 길어 쪼개서 재시도 중… (누락 방지)")
            # 페이지를 둘로 나눠서 각각 구조화
            recovered: list[dict[str, Any]] = []
            for chunk in _split_page_text(page_text):
                if not chunk.strip():
                    continue
                try:
                    chunk_probs, _ = structure_single(client, chunk)
                    recovered.extend(chunk_probs)
                except json.JSONDecodeError:
                    continue
            # 재시도 결과가 더 많으면 채택, 아니면 원래 결과 유지
            if len(recovered) > len(page_problems):
                page_problems = recovered

        all_problems.extend(page_problems)

    progress.progress(1.0, text="문제 구조화 완료")
    cleaned = sanitize_problems(all_problems)
    return _insert_problem_spacing(cleaned)


def _auto_mathrm(latex: str) -> str:
    """
    수식 안의 대문자(점·선분·집합 등)에 자동으로 \\mathrm 을 적용하고
    잘못된 기호를 교정합니다.

    전략:
    1) \\bigcup / \\bigcap → \\cup / \\cap
    2) 이미 \\mathrm / \\text / \\mathbb / \\mathcal ... 안에 있는 블록은 placeholder 로 보호
    3) 모든 LaTeX 명령 토큰(\\triangle 등)도 placeholder 로 보호
    4) 남은 곳에서 대문자 연속(프라임 포함)을 \\mathrm{...} 으로 감싸기
    5) placeholder 복원

    결과 예시:
      O(0,0)               → \\mathrm{O}(0,0)
      \\triangle OAB       → \\triangle \\mathrm{OAB}
      \\triangle O'A'B'    → \\triangle \\mathrm{O'A'B'}
      \\overline{AB}       → \\overline{\\mathrm{AB}}
      A\\cup B\\cup C      → \\mathrm{A}\\cup \\mathrm{B}\\cup \\mathrm{C}
      P_0                  → \\mathrm{P}_0
      \\mathbb{R}          → \\mathbb{R}   (변경 없음)
      x^2+y^2+ax+by+c      → (변경 없음)
    """
    s = latex

    # 0) 스타일 명령 제거
    #    \displaystyle\lim 처럼 붙여 쓰면 skill 변환기가 \lim 까지 삼키는 버그가 있음.
    #    스타일 힌트는 hwpEQ 에서 중요하지 않으므로 제거.
    for _cmd in (r"\displaystyle", r"\textstyle", r"\scriptstyle", r"\scriptscriptstyle"):
        s = s.replace(_cmd, "")

    # 0-a) 스킬이 처리 못 하는 LaTeX 명령을 hwpEQ 친화 기호로 대체
    s = s.replace(r"\mid", "|")      # 집합 구분자 → |
    s = s.replace(r"\middle|", "|")  # \left...\middle|...\right 에서 쓰임
    s = s.replace(r"\vert", "|")     # 수직 막대
    s = s.replace(r"\|", "||")       # 평행 (이중 막대)
    s = s.replace(r"\%", "%")        # \% → % (한글에서 \ 가 그대로 렌더됨)
    s = s.replace(r"\$", "$")
    s = s.replace(r"\&", "&")
    s = s.replace(r"\#", "#")
    s = s.replace(r"\_", "_")

    # 0-c) \int / \sum / \prod / \oint 다음에 _{...} 가 바로 붙고
    #      지수에 \frac 이 들어가면 skill 변환기가 연산자를 삼킴.
    #      예: \int_{0}^{\frac{\pi}{4}} → '_{0}^{...}' (INT 사라짐)
    #      공백을 삽입해 버그 회피: \int _{0}^{\frac{\pi}{4}}
    s = re.sub(r"(\\(?:int|sum|prod|oint|iint|iiint|bigcup|bigcap))(?=[_^])", r"\1 ", s)

    # 0-d) \left( / \right) 는 그대로 유지 — 아래 단계에서 left( / right) 로
    #      변환되도록 (공백 없이) 만들어야 한글 수식편집기가 정상 렌더.

    # 0-b) 그리스문자 + 아래첨자 버그 회피
    #     \alpha_1 → \alpha _1 (skill 변환기가 \alpha 를 삼키는 버그)
    _GREEK = (
        "alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|"
        "iota|kappa|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|"
        "tau|upsilon|phi|varphi|chi|psi|omega|"
        "Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega"
    )
    s = re.sub(rf"(\\(?:{_GREEK}))_", r"\1 _", s)

    # 1) 큰 합/교집합 교정
    s = s.replace(r"\bigcup", r"\cup")
    s = s.replace(r"\bigcap", r"\cap")

    # 1-b) 삼각/로그/극한 함수 앞 백슬래시 누락 보정
    # Vision 이 \sin, \cos, \log 에서 백슬래시를 빠뜨리면 변수로 렌더됨
    # 앞뒤가 알파벳/백슬래시가 아닌 경우에만 (단어 중간이 아닌 경우)
    _FUNCS = (
        "sin", "cos", "tan", "cot", "sec", "csc",
        "sinh", "cosh", "tanh",
        "log", "ln", "lg", "exp",
        "lim", "max", "min", "det", "gcd",
    )
    # 긴 이름부터 처리 (sinh 가 sin 으로 오인되지 않게)
    for fn in sorted(_FUNCS, key=len, reverse=True):
        s = re.sub(rf"(?<![A-Za-z\\])({fn})(?![A-Za-z])", rf"\\{fn}", s)

    # 2-3) 보호(placeholder 치환)
    protected: list[str] = []

    def _save(match: re.Match) -> str:
        idx = len(protected)
        protected.append(match.group(0))
        return f"\x00{idx}\x00"

    # 2) 이미 스타일이 적용된 블록 보호 (인수 포함)
    s = re.sub(
        r"\\(?:mathrm|text|operatorname|mathbb|mathcal|mathbf|mathit|mathsf|mathfrak)\{[^{}]*\}",
        _save,
        s,
    )
    # 3) LaTeX 명령 토큰 보호 (인수 없음). 예: \triangle, \overline, \frac, \cup, \sin
    s = re.sub(r"\\[a-zA-Z]+", _save, s)

    # 4) 대문자 연속(프라임 포함)을 \mathrm{...} 로 감싸기
    #    - (?:[A-Z]'*)+  : 대문자 1개 뒤에 0개 이상의 프라임, 이 조합이 1회 이상
    s = re.sub(
        r"(?:[A-Z]'*)+",
        lambda m: f"\\mathrm{{{m.group(0)}}}",
        s,
    )

    # 5) placeholder 복원
    s = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], s)

    # 6) \mathrm{...} 블록 정규화
    #    - 순수 대문자만 있으면 그대로 유지
    #    - 프라임(')이 있으면 \mathrm{O}^{\prime}... 형태로 분리
    #    - 소문자/숫자/연산자 등 혼합 내용이 있으면
    #      → 대문자만 \mathrm 에 남기고 나머지는 바깥으로 빼냄
    #        (그래야 x, y 같은 변수가 이탤릭으로 렌더됨)
    def _split_primes_run(text: str) -> str:
        """'O'A'B'' 같은 대문자+프라임 연속을 hwpEQ 호환 형태로."""
        parts: list[str] = []
        for m in re.finditer(r"([A-Z])(\'*)", text):
            letter, primes = m.group(1), m.group(2)
            parts.append(f"\\mathrm{{{letter}}}")
            if primes:
                parts.append("^{" + r"\prime" * len(primes) + "}")
        return "".join(parts)

    def _normalize_mathrm(match: re.Match) -> str:
        content = match.group(1)
        # 1) 순수 대문자만
        if re.fullmatch(r"[A-Z]+", content):
            return match.group(0)
        # 2) 대문자 + 프라임만
        if re.fullmatch(r"[A-Z']+", content) and "'" in content:
            return _split_primes_run(content)
        # 3) 혼합 내용 — 대문자 run 만 \mathrm, 나머지는 바깥으로
        result: list[str] = []
        i = 0
        n = len(content)
        while i < n:
            c = content[i]
            if c.isupper() and c.isalpha():
                j = i
                while j < n and (
                    (content[j].isupper() and content[j].isalpha()) or content[j] == "'"
                ):
                    j += 1
                chunk = content[i:j]
                if "'" in chunk:
                    result.append(_split_primes_run(chunk))
                else:
                    result.append(f"\\mathrm{{{chunk}}}")
                i = j
            else:
                result.append(c)
                i += 1
        return "".join(result)

    s = re.sub(r"\\mathrm\{([^{}]*)\}", _normalize_mathrm, s)
    return s


def _clean_segment(seg: Any) -> dict[str, Any] | None:
    """세그먼트 1개 정리. content 가 없거나 비어있으면 None."""
    if not isinstance(seg, dict):
        return None
    seg_type = seg.get("type", "text")
    # image_placeholder 가 segments 내부에 잘못 들어온 경우 → 드롭
    if seg_type not in ("text", "formula"):
        return None
    content = seg.get("content")
    if content is None:
        # description 같은 다른 키가 있으면 fallback
        content = seg.get("text") or seg.get("description") or ""
    content = str(content)
    if not content:
        return None
    if seg_type == "formula":
        content = _auto_mathrm(content)
    return {"type": seg_type, "content": content}


_HANGUL_RE = re.compile(r"[\uAC00-\uD7A3]+(?:[\s,.!?·\uAC00-\uD7A3]*[\uAC00-\uD7A3])?")


def _split_korean_in_segments(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    formula 세그먼트 안에 한글이 섞여 있으면 세그먼트를 분리:
      formula("y=x이고y=(x-k)^2") → formula("y=x"), text(" 이고 "), formula("y=(x-k)^2")
    """
    result: list[dict[str, Any]] = []
    for s in segs:
        if (
            s.get("type") == "formula"
            and isinstance(s.get("content"), str)
            and re.search(r"[\uAC00-\uD7A3]", s["content"])
        ):
            content = s["content"]
            last_end = 0
            for m in _HANGUL_RE.finditer(content):
                start, end = m.span()
                if start > last_end:
                    pre = content[last_end:start].strip()
                    if pre:
                        result.append({"type": "formula", "content": pre})
                korean = m.group(0).strip()
                if korean:
                    result.append({"type": "text", "content": " " + korean + " "})
                last_end = end
            if last_end < len(content):
                post = content[last_end:].strip()
                if post:
                    result.append({"type": "formula", "content": post})
        else:
            result.append(s)
    return result


PROBLEM_GAP_LINES = 2  # 다른 문제 사이의 빈 줄 개수


def _insert_problem_spacing(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    다른 문제 번호 사이에 빈 문단을 PROBLEM_GAP_LINES 만큼 삽입.
    "1", "1-①", "1-box" 등은 같은 문제로 간주 (앞 숫자만 비교).
    """
    if not problems:
        return problems

    def _base(p: dict) -> str:
        num = str(p.get("number", ""))
        m = re.match(r"^\s*(\d+)", num)
        return m.group(1) if m else ""

    result: list[dict[str, Any]] = []
    last_base: str | None = None
    for prob in problems:
        cur_base = _base(prob)
        if last_base and cur_base and cur_base != last_base:
            for _ in range(PROBLEM_GAP_LINES):
                result.append(
                    {
                        "number": "",
                        "main": False,
                        "segments": [{"type": "text", "content": " "}],
                    }
                )
        result.append(prob)
        if cur_base:
            last_base = cur_base
    return result


def sanitize_problems(problems: list[Any]) -> list[dict[str, Any]]:
    """
    Claude 가 준 JSON 을 build_hwpx 가 기대하는 모양으로 정리:
      - 모든 segment 는 {"type": "text"|"formula", "content": str} 형태
      - image_placeholder 는 problems 레벨에서만 허용 (segments 안에 섞여 있으면 빼냄)
      - 빈 segment/problem 은 제거
      - formula 안 한글은 text 로 분리
    """
    cleaned: list[dict[str, Any]] = []
    for prob in problems:
        if not isinstance(prob, dict):
            continue

        # image_placeholder 타입 problem 은 그대로 유지
        if prob.get("type") == "image_placeholder":
            cleaned.append(prob)
            continue

        segs = prob.get("segments")

        # 박스(2차원 배열)
        if prob.get("box") and isinstance(segs, list) and segs and isinstance(segs[0], list):
            new_rows: list[list[dict[str, Any]]] = []
            for row in segs:
                if not isinstance(row, list):
                    continue
                row_clean = [s for s in (_clean_segment(x) for x in row) if s]
                row_clean = _split_korean_in_segments(row_clean)
                if row_clean:
                    new_rows.append(row_clean)
            if new_rows:
                new_prob = dict(prob)
                new_prob["segments"] = new_rows
                cleaned.append(new_prob)
            continue

        # 일반 문단
        if isinstance(segs, list):
            # segments 안에 잘못 들어간 image_placeholder 는 별도 problem 으로 승격
            lifted: list[dict[str, Any]] = []
            flat_clean: list[dict[str, Any]] = []
            for x in segs:
                if isinstance(x, dict) and x.get("type") == "image_placeholder":
                    lifted.append(x)
                    continue
                c = _clean_segment(x)
                if c:
                    flat_clean.append(c)
            flat_clean = _split_korean_in_segments(flat_clean)
            if flat_clean:
                new_prob = dict(prob)
                new_prob["segments"] = flat_clean
                cleaned.append(new_prob)
            cleaned.extend(lifted)
            continue

        # segments 가 없지만 다른 형식일 수도 있음 → 그대로 통과
        cleaned.append(prob)

    return cleaned


# ────────────────────────────────────────────────────────────
# 파일 처리
# ────────────────────────────────────────────────────────────


def pdf_to_images(pdf_path: Path, workdir: Path, dpi: int = 200) -> list[Path]:
    """PDF → 페이지별 PNG. pdf2image 가 없으면 친절히 안내."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        st.error("`pdf2image` 가 설치되어 있지 않습니다. `pip install pdf2image` 후 poppler 도 필요합니다.")
        st.stop()

    pages = convert_from_path(str(pdf_path), dpi=dpi)
    paths: list[Path] = []
    for i, page in enumerate(pages, 1):
        out = workdir / f"page_{i:03d}.png"
        page.save(out, "PNG")
        paths.append(out)
    return paths


def collect_input_images(
    uploaded,
    workdir: Path,
    dpi: int = 150,
    pasted_images: list[bytes] | None = None,
) -> list[Path]:
    """업로드된 파일(이미지/PDF) + 붙여넣은 PNG 바이트 → 이미지 경로 리스트."""
    image_paths: list[Path] = []

    # 붙여넣은 이미지 먼저 저장
    if pasted_images:
        for i, data in enumerate(pasted_images, 1):
            p = workdir / f"pasted_{i:03d}.png"
            p.write_bytes(data)
            image_paths.append(p)

    # 업로드된 파일 처리
    for up in uploaded or []:
        suffix = Path(up.name).suffix.lower()
        saved = workdir / up.name
        saved.write_bytes(up.getvalue())

        if suffix in IMAGE_EXTS:
            image_paths.append(saved)
        elif suffix in PDF_EXTS:
            st.write(f"📄 PDF 렌더링: `{up.name}` (DPI={dpi})")
            image_paths.extend(pdf_to_images(saved, workdir, dpi=dpi))
        else:
            st.warning(f"지원하지 않는 형식 무시: {up.name}")

    return image_paths


def resolve_template(workdir: Path, uploaded_template) -> Path | None:
    if uploaded_template is not None:
        dst = workdir / "template.hwpx"
        dst.write_bytes(uploaded_template.getvalue())
        return dst
    if DEFAULT_TEMPLATE.exists():
        return DEFAULT_TEMPLATE
    return None


# ────────────────────────────────────────────────────────────
# Streamlit UI
# ────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(page_title="PDF/이미지 → HWPX", page_icon="📐", layout="centered")
    st.title("📐 수학 문제 → 한글(HWPX) 변환기")
    st.caption("이미지(JPG/PNG) 또는 PDF 를 업로드하면 Claude Vision 이 수식을 인식해 HWPX 로 변환합니다.")

    with st.sidebar:
        st.header("설정")
        st.text(
            "API 키는 환경변수 `ANTHROPIC_API_KEY` 또는\n"
            "Streamlit Secrets 에 설정하세요."
        )
        dpi = st.slider("PDF 렌더링 DPI", min_value=100, max_value=250, value=150, step=10,
                        help="높을수록 인식 정확도↑ / 요청 크기↑. 전송 전 자동 리사이즈되지만 낮은 DPI가 더 빠릅니다.")
        show_raw = st.checkbox("Vision 인식 원문 보기", value=False)
        show_json = st.checkbox("구조화 JSON 보기", value=False)

    uploaded = st.file_uploader(
        "문제 파일 업로드 (여러 개 가능)",
        type=["pdf", "jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
    )

    # ── 클립보드 붙여넣기 영역 ──
    if "pasted_images" not in st.session_state:
        st.session_state.pasted_images = []  # list[bytes]

    if PASTE_AVAILABLE:
        st.markdown("**또는 클립보드에서 이미지 붙여넣기**  `⌘V` / `Ctrl+V` 로 바로 붙여넣기 가능")
        col_paste, col_clear = st.columns([3, 1])
        with col_paste:
            pasted = _paste_image_button(
                label="📋 클립보드에서 붙여넣기",
                key="clip_paste",
                errors="ignore",
            )
            if pasted.image_data is not None:
                from io import BytesIO
                buf = BytesIO()
                pasted.image_data.save(buf, format="PNG")
                img_bytes = buf.getvalue()
                # 중복 방지: 같은 바이트가 이미 있으면 skip
                if img_bytes not in st.session_state.pasted_images:
                    st.session_state.pasted_images.append(img_bytes)
        with col_clear:
            if st.session_state.pasted_images and st.button("🗑 비우기"):
                st.session_state.pasted_images = []
                st.rerun()

        # ⌘V / Ctrl+V 가 눌리면 위의 붙여넣기 버튼을 자동 클릭
        # (브라우저 보안상 완전한 "아무데서나 Cmd+V" 는 어렵지만, 키 입력 시 버튼 클릭을
        #  대신 트리거해주면 사용자 체감상 같은 UX가 됩니다)
        st.components.v1.html(
            """
            <script>
            (function() {
                const topDoc = window.parent.document;
                if (topDoc.__pasteShortcutBound) return;
                topDoc.__pasteShortcutBound = true;

                topDoc.addEventListener('keydown', function(e) {
                    const isPaste = (e.metaKey || e.ctrlKey) && (e.key === 'v' || e.key === 'V');
                    if (!isPaste) return;

                    // 입력 필드에서 입력 중이면 정상 동작 우선
                    const ae = topDoc.activeElement;
                    const tag = ae && ae.tagName ? ae.tagName.toLowerCase() : '';
                    const isEditable = ae && (ae.isContentEditable
                        || tag === 'input' || tag === 'textarea');
                    if (isEditable) return;

                    // streamlit-paste-button 의 내부 버튼을 찾아 클릭
                    const frames = topDoc.querySelectorAll('iframe');
                    for (const f of frames) {
                        try {
                            const doc = f.contentDocument;
                            if (!doc) continue;
                            const btns = doc.querySelectorAll('button');
                            for (const b of btns) {
                                if (b.innerText && b.innerText.includes('붙여넣기')) {
                                    e.preventDefault();
                                    b.click();
                                    return;
                                }
                            }
                        } catch (_err) {
                            // cross-origin iframe — skip
                        }
                    }
                }, true);
            })();
            </script>
            """,
            height=0,
        )

        if st.session_state.pasted_images:
            st.caption(f"붙여넣은 이미지: {len(st.session_state.pasted_images)}장")
            cols = st.columns(min(4, len(st.session_state.pasted_images)))
            for i, img_bytes in enumerate(st.session_state.pasted_images):
                with cols[i % len(cols)]:
                    st.image(img_bytes, caption=f"#{i+1}", use_container_width=True)
    else:
        st.info("붙여넣기 기능을 쓰려면 `streamlit-paste-button` 을 설치하세요.")

    uploaded_template = st.file_uploader(
        "HWPX 템플릿 업로드 (선택 — 기본 template.hwpx 가 있으면 생략 가능)",
        type=["hwpx"],
        accept_multiple_files=False,
    )

    has_input = bool(uploaded) or bool(st.session_state.pasted_images)
    if not has_input:
        st.info("변환할 파일을 업로드하거나 이미지를 붙여넣으세요.")
        return

    if not st.button("🚀 변환 시작", type="primary"):
        return

    client = get_client()

    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = Path(tmpdir)

        # 1) 템플릿 확인
        template = resolve_template(workdir, uploaded_template)
        if template is None:
            st.error(
                "HWPX 템플릿이 필요합니다. 프로젝트 루트에 `template.hwpx` 를 두거나 위에서 업로드하세요."
            )
            return

        # 2) 입력 → 이미지
        with st.status("입력 파일 처리 중…", expanded=True) as status:
            image_paths = collect_input_images(
                uploaded,
                workdir,
                dpi=dpi,
                pasted_images=st.session_state.get("pasted_images", []),
            )
            if not image_paths:
                status.update(label="처리할 이미지가 없습니다.", state="error")
                return
            status.update(label=f"이미지 {len(image_paths)}장 준비 완료", state="complete")

        # 3) Vision 인식 (페이지별 리스트 반환)
        with st.status("Claude Vision 인식 중…", expanded=False) as status:
            raw_texts = vision_recognize(client, image_paths)
            status.update(label=f"Vision 인식 완료 ({len(raw_texts)}장)", state="complete")

        if show_raw:
            with st.expander("📝 Vision 인식 원문"):
                for i, t in enumerate(raw_texts, 1):
                    st.markdown(f"**페이지 {i}**")
                    st.code(t, language="markdown")

        # 4) 구조화 (페이지별로 나눠 호출 → 병합)
        with st.status("문제 구조화 중…", expanded=False) as status:
            try:
                problems = structure_problems(client, raw_texts)
            except json.JSONDecodeError as e:
                status.update(label="JSON 파싱 실패", state="error")
                st.exception(e)
                return
            if not problems:
                status.update(label="추출된 문제가 없습니다.", state="error")
                return
            status.update(label=f"문제 {len(problems)}개 구조화 완료", state="complete")

        if show_json:
            with st.expander("🧾 구조화 결과 JSON"):
                st.json(problems)

        # 5) HWPX 생성
        output_path = workdir / "converted.hwpx"
        with st.status("HWPX 파일 생성 중…", expanded=False) as status:
            try:
                pdf_to_hwpx.build_hwpx(str(template), str(output_path), problems)
            except Exception as e:  # noqa: BLE001
                status.update(label="HWPX 생성 실패", state="error")
                st.exception(e)
                with st.expander("🔍 전달된 problems (디버그용)", expanded=True):
                    st.json(problems)
                return
            status.update(label="HWPX 생성 완료", state="complete")

        data = output_path.read_bytes()
        kb = len(data) / 1024
        st.success(f"✅ 변환 완료 — {kb:.1f} KB")
        st.download_button(
            "⬇️ HWPX 다운로드",
            data=data,
            file_name="converted.hwpx",
            mime="application/vnd.hancom.hwpx",
            type="primary",
        )


if __name__ == "__main__":
    main()
