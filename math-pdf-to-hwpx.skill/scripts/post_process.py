"""
수학 문제 LaTeX → HWPX 후처리 모듈.

Claude 가 문제를 LaTeX 세그먼트로 구조화한 JSON 을 받아서:
  1. _auto_mathrm() 로 LaTeX 정리 (대문자 로만체, 프라임 분리 등)
  2. pdf_to_hwpx.latex_to_hwpeq() 로 hwpEQ 변환 (monkey-patch 로 후처리 강화)
  3. pdf_to_hwpx.build_hwpx() 로 최종 HWPX 파일 생성

사용 예:
    from post_process import convert_problems_to_hwpx
    problems = [  # Claude 가 만든 구조
        {"number": "1", "main": True, "segments": [...]},
        ...
    ]
    convert_problems_to_hwpx(
        problems=problems,
        template_path="template.hwpx",
        output_path="output.hwpx",
    )
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# pdf_to_hwpx.py 를 같은 디렉터리에서 로드
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
import pdf_to_hwpx  # noqa: E402


# ════════════════════════════════════════════════════════════
# pdf_to_hwpx.latex_to_hwpeq 출력 보정 (monkey-patch)
# 한글 수식편집기 렌더링 호환성을 위한 다양한 치환
# ════════════════════════════════════════════════════════════

if not hasattr(pdf_to_hwpx, "_original_latex_to_hwpeq"):
    pdf_to_hwpx._original_latex_to_hwpeq = pdf_to_hwpx.latex_to_hwpeq


def _paren_boundary(out: str) -> str:
    """left ( → left(, right ) → right)  (공백 제거로 괄호 삼킴 방지)."""
    out = re.sub(r"\bleft\s+\(", "left(", out)
    out = re.sub(r"\bright\s+\)", "right)", out)
    return out


def _patched_latex_to_hwpeq(latex: str) -> str:
    """한컴 수식편집기 친화적으로 hwpEQ 출력 후처리."""
    # 입력 LaTeX 의 \mathit{X} 를 사전 보호 (skill 변환기가 그냥 {X} 로만 출력해서
    # 이탤릭 명시 표현이 사라짐)
    _mathit_placeholders: list[str] = []

    def _save_mathit(m: re.Match) -> str:
        idx = len(_mathit_placeholders)
        _mathit_placeholders.append(m.group(1))
        return f"\x01{idx}\x01"

    latex = re.sub(r"\\mathit\{([^{}]+)\}", _save_mathit, latex)

    # 호(arc) 표기 사전 보호 — skill 변환기가 인식 못 함
    _arc_placeholders: list[str] = []

    def _save_arc(m: re.Match) -> str:
        idx = len(_arc_placeholders)
        _arc_placeholders.append(m.group(1))
        return f"\x02{idx}\x02"

    # 1단계 중첩 허용 (\widehat{\mathrm{AB}} 처리)
    _arc_arg = r"((?:[^{}]|\{[^{}]*\})+)"
    latex = re.sub(rf"\\widehat\{{{_arc_arg}\}}", _save_arc, latex)
    latex = re.sub(rf"\\overset\{{\\frown\}}\{{{_arc_arg}\}}", _save_arc, latex)
    latex = re.sub(rf"\\overarc\{{{_arc_arg}\}}", _save_arc, latex)

    out = pdf_to_hwpx._original_latex_to_hwpeq(latex)

    # 보호된 \mathit 복원 → it{X}
    out = re.sub(
        r"\x01(\d+)\x01",
        lambda m: f"it{{{_mathit_placeholders[int(m.group(1))]}}}",
        out,
    )

    # 보호된 호 복원 → {arch{...}}
    def _restore_arc(m: re.Match) -> str:
        content = _arc_placeholders[int(m.group(1))]
        content = re.sub(r"\\mathrm\{([^{}]+)\}", r"rm \1", content)
        if re.fullmatch(r"[A-Z]+", content):
            content = f"rm {content}"
        return f"{{arch{{{content}}}}}"

    out = re.sub(r"\x02(\d+)\x02", _restore_arc, out)

    # 1) "X" → rm{X}  (mathrm 변환 산물)
    out = re.sub(r'"([^"]+)"', r"rm{\1}", out)

    # 2) UNION/INTER → cup/cap  (빅 → 일반 집합 기호)
    out = re.sub(r"\bUNION\b", "cup", out)
    out = re.sub(r"\bINTER\b", "cap", out)

    # 3) ^{prime...} → ' prime ... '  (위첨자 대신 키워드, 양쪽 공백)
    def _prime_sub(m: re.Match) -> str:
        count = len(m.group(1)) // 5  # "prime" = 5 글자
        return " " + " ".join(["prime"] * count) + " "

    out = re.sub(r"\^\{((?:prime)+)\}", _prime_sub, out)

    # 4) 단일 소문자 변수를 it{...} 로 감싸기 (이탤릭 강제)
    _prot: list[str] = []

    def _save(m: re.Match) -> str:
        idx = len(_prot)
        _prot.append(m.group(0))
        return f"\x00{idx}\x00"

    out = re.sub(r"(?:rm|it)\{[^{}]*\}", _save, out)
    out = re.sub(r"(?<![a-zA-Z])([a-z])(?![a-zA-Z])", r"it{\1}", out)
    out = re.sub(r"\x00(\d+)\x00", lambda m: _prot[int(m.group(1))], out)

    # 5) 공백 개선
    # 5-a) 쉼표 뒤 ~ 공백 (순서쌍/수열 띄어쓰기)
    out = re.sub(r",\s*(?!~)", ",~ ", out)
    # 5-a2) cdots 양옆 ~ 공백 (a_1, a_2, cdots, a_n 가 너무 좁아 보이는 현상 해소)
    #       이미 ~ 가 있으면 중복 추가하지 않음
    out = re.sub(r"(?<![~ ])cdots", " cdots", out)
    out = re.sub(r"cdots(?![~ ])", "cdots ", out)
    # 5-b) 집합 구분자 | 좌우 공백 (|A| 절댓값 제외)
    out = re.sub(r"(?<!left)(?<!right) \| ", " ~|~ ", out)
    # 5-c) 좌/우 극한 ^-, ^+ → -, +
    out = re.sub(r"\^([+\-])(?![A-Za-z0-9{])", r"\1", out)
    out = re.sub(r"\^\{([+\-])\}", r"\1", out)

    # 5-d) bar/hat/vec 다음 ^/_ 를 블록 바깥으로 그룹화
    def _wrap_bar_before_sup(m: re.Match) -> str:
        return "{" + m.group(1) + "}"

    out = re.sub(
        r"((?:bar|hat|tilde|vec|dot|ddot)\s*\{(?:[^{}]|\{[^{}]*\})*\})(?=\s*[\^_])",
        _wrap_bar_before_sup,
        out,
    )

    # 6) 여분 공백 정리
    out = re.sub(r" +", " ", out).strip()

    # 6-b) `vec rm{X}` → `{vec{rm X}}` 그룹화
    #     한컴 수식편집기에서 vec 인자는 {...} 그룹이어야 화살표 정상 표시.
    #     외부 그룹까지 추가해 후속 토큰과 격리 (소문자 vec 처리와 일관).
    # 첨자 점쌍 (vec rm{X}_{n}rm{Y} ['|prime]?)
    out = re.sub(
        r"vec rm\{([^{}]+)\}(_\{[^{}]+\})rm\{([^{}]+)\}(\s*'\s*|\s+prime\s*)?",
        lambda m: f"{{vec{{rm {m.group(1)} {m.group(2)} rm {m.group(3)}{m.group(4) or ''}}}}}",
        out,
    )
    # 끝 첨자 (vec rm{X}_{n} ['|prime]?)
    out = re.sub(
        r"vec rm\{([^{}]+)\}(_\{[^{}]+\})(\s*'\s*|\s+prime\s*)?",
        lambda m: f"{{vec{{rm {m.group(1)} {m.group(2)}{m.group(3) or ''}}}}}",
        out,
    )
    # 단순 점쌍 (vec rm{X} ['|prime]?)
    out = re.sub(
        r"vec rm\{([^{}]+)\}(\s*'\s*|\s+prime\s*)?",
        lambda m: f"{{vec{{rm {m.group(1)}{m.group(2) or ''}}}}}",
        out,
    )
    # 소문자 벡터 (vec it{X} → {vec{it X}})
    #     한컴이 `vec it{a}` 를 `{vec{it}} it a` 로 잘못 해석하므로 외부 그룹 추가.
    out = re.sub(
        r"vec it\{([^{}]+)\}",
        lambda m: f"{{vec{{it {m.group(1)}}}}}",
        out,
    )

    # 6-c) `\right|+\left|` 짝짓기 깨짐 안전망
    out = re.sub(r"right\s+left\s+\|", r"right |", out)
    out = re.sub(r"left\s+right\s+\|", r"left |", out)

    # 6-d) THEREFORE / BECAUSE 뒤 `~` 띄어쓰기
    out = re.sub(r"\bTHEREFORE\b\s*", "THEREFORE~ ", out)
    out = re.sub(r"\bBECAUSE\b\s*", "BECAUSE~ ", out)

    # 7) 괄호 경계 보호 (맨 마지막)
    out = _paren_boundary(out)
    return out


pdf_to_hwpx.latex_to_hwpeq = _patched_latex_to_hwpeq


# ════════════════════════════════════════════════════════════
# LaTeX 전처리 — _auto_mathrm
# ════════════════════════════════════════════════════════════


def _auto_mathrm(latex: str) -> str:
    """
    수식 LaTeX 에 자동으로 \\mathrm 을 적용하고 잘못된 기호를 교정.

    처리 단계:
    0)   \\displaystyle 제거 (skill 변환기의 \\lim 삼킴 버그 회피)
    0-a) \\mid, \\vert, \\middle| → |, \\% → % 등 이스케이프 정리
    0-b) 그리스문자 + _ → 그리스문자 + 공백 + _ (alpha_1 버그 회피)
    0-c) \\int / \\sum / \\prod 다음 _ / ^ 앞 공백 삽입
    0-d) \\left( → (, \\right) → ) (skill 이 left( 로 변환)
    1)   \\bigcup / \\bigcap → \\cup / \\cap
    1-b) 삼각/로그/극한 함수 앞 백슬래시 누락 보정
    2-3) 기존 \\mathrm, LaTeX 명령을 placeholder 로 보호
    4)   남은 대문자(프라임 포함)를 \\mathrm{...} 으로 감싸기
    5)   placeholder 복원
    6)   \\mathrm{...} 내용 정규화 (혼합 내용 분리, 프라임 ^{\\prime} 로)
    """
    s = latex

    # 0) 스타일 명령 제거
    for _cmd in (r"\displaystyle", r"\textstyle", r"\scriptstyle", r"\scriptscriptstyle"):
        s = s.replace(_cmd, "")

    # 0-a) 스킬이 처리 못 하는 LaTeX 명령 치환
    s = s.replace(r"\mid", "|")
    s = s.replace(r"\middle|", "|")
    s = s.replace(r"\vert", "|")
    s = s.replace(r"\|", "||")
    s = s.replace(r"\%", "%")
    s = s.replace(r"\&", "&")
    s = s.replace(r"\#", "#")
    s = s.replace(r"\_", "_")

    # 0-b) 그리스문자 + 아래첨자 버그 회피
    _GREEK = (
        "alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|"
        "iota|kappa|lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|"
        "tau|upsilon|phi|varphi|chi|psi|omega|"
        "Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega"
    )
    s = re.sub(rf"(\\(?:{_GREEK}))_", r"\1 _", s)

    # 0-c) \int / \sum / \prod 다음 _/^ 앞 공백 삽입 (연산자 삼킴 버그 회피)
    s = re.sub(r"(\\(?:int|sum|prod|oint|iint|iiint|bigcup|bigcap))(?=[_^])", r"\1 ", s)

    # 0-d) \left( / \right) 는 유지 (한컴 수식편집기에서 분수/벡터/근호 크기 자동 조정에 필요)
    #     후처리에서 'left ( ... right )' → 'left( ... right)' 로 공백만 제거

    # 0-e) 불필요한 LaTeX 공백 명령 제거 (PDF 규칙 ⑥)
    #     \, \; \: \quad \qquad 같은 명령이 후처리에서 잘못 변환되는 것을 방지
    s = re.sub(r"\\[,:;]", " ", s)            # \,  \:  \;
    s = s.replace(r"\!", "")                   # \! 제거
    s = s.replace(r"\quad", " ")
    s = s.replace(r"\qquad", "  ")

    # 0-f) \overrightarrow / \overleftarrow / \overleftrightarrow 화살표 누락 회피
    #     skill 변환기가 이 명령을 그냥 {X} 로만 출력해 화살표 사라짐.
    #     \vec{...} 로 치환해 vec 키워드가 살아남도록
    s = re.sub(r"\\overrightarrow\b", r"\\vec", s)
    s = re.sub(r"\\overleftarrow\b", r"\\vec", s)
    s = re.sub(r"\\overleftrightarrow\b", r"\\vec", s)

    # 0-g) 소문자 변수 + \vec 사이 공백 (스칼라 곱 m\vec{a} 처리)
    s = re.sub(r"([a-z])(?=\\vec\b)", r"\1 ", s)

    # 1) 큰 합/교집합 교정
    s = s.replace(r"\bigcup", r"\cup")
    s = s.replace(r"\bigcap", r"\cap")

    # 1-b) 삼각/로그/극한 함수 앞 백슬래시 누락 보정
    _FUNCS = (
        "sin", "cos", "tan", "cot", "sec", "csc",
        "sinh", "cosh", "tanh",
        "log", "ln", "lg", "exp",
        "lim", "max", "min", "det", "gcd",
    )
    for fn in sorted(_FUNCS, key=len, reverse=True):
        s = re.sub(rf"(?<![A-Za-z\\])({fn})(?![A-Za-z])", rf"\\{fn}", s)

    # 2-3) 보호 (placeholder 치환)
    protected: list[str] = []

    def _save(match: re.Match) -> str:
        idx = len(protected)
        protected.append(match.group(0))
        return f"\x00{idx}\x00"

    s = re.sub(
        r"\\(?:mathrm|text|operatorname|mathbb|mathcal|mathbf|mathit|mathsf|mathfrak)\{[^{}]*\}",
        _save,
        s,
    )
    s = re.sub(r"\\[a-zA-Z]+", _save, s)

    # 4) 연속 대문자 2글자 이상만 \mathrm{...} 로 감싸기 (PDF 규칙 ⑦ 자동 변환)
    #    단일 대문자는 Claude 가 명시적으로 \mathrm 또는 \mathit 적용
    #    (점이면 \mathrm{A}, 도형이면 \mathit{S}, 변수면 그대로)
    s = re.sub(
        r"(?:[A-Z]'*){2,}",
        lambda m: f"\\mathrm{{{m.group(0)}}}",
        s,
    )

    # 5) placeholder 복원
    s = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], s)

    # 6) \mathrm{...} 정규화
    def _split_primes_run(text: str) -> str:
        parts: list[str] = []
        for m in re.finditer(r"([A-Z])(\'*)", text):
            letter, primes = m.group(1), m.group(2)
            parts.append(f"\\mathrm{{{letter}}}")
            if primes:
                parts.append("^{" + r"\prime" * len(primes) + "}")
        return "".join(parts)

    def _normalize_mathrm(match: re.Match) -> str:
        content = match.group(1)
        if re.fullmatch(r"[A-Z]+", content):
            return match.group(0)
        if re.fullmatch(r"[A-Z']+", content) and "'" in content:
            return _split_primes_run(content)
        # 혼합 내용 — 대문자 run 만 \mathrm, 나머지는 바깥으로
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


# ════════════════════════════════════════════════════════════
# JSON 세그먼트 정리 — sanitize
# ════════════════════════════════════════════════════════════

_HANGUL_RE = re.compile(r"[\uAC00-\uD7A3]+(?:[\s,.!?·\uAC00-\uD7A3]*[\uAC00-\uD7A3])?")


def _clean_segment(seg: Any) -> dict[str, Any] | None:
    """세그먼트 1개 정리. 빈 content/잘못된 type → None."""
    if not isinstance(seg, dict):
        return None
    seg_type = seg.get("type", "text")
    if seg_type not in ("text", "formula"):
        return None
    content = seg.get("content")
    if content is None:
        content = seg.get("text") or seg.get("description") or ""
    content = str(content)
    if not content:
        return None
    if seg_type == "formula":
        content = _auto_mathrm(content)
    return {"type": seg_type, "content": content}


def _split_korean_in_segments(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """formula 세그먼트에 한글이 섞이면 쪼갬 (이고/일때/이다 등)."""
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


def sanitize_problems(problems: list[Any]) -> list[dict[str, Any]]:
    """Claude JSON 을 build_hwpx 가 기대하는 모양으로 정리."""
    cleaned: list[dict[str, Any]] = []
    for prob in problems:
        if not isinstance(prob, dict):
            continue

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

        cleaned.append(prob)

    return cleaned


# ════════════════════════════════════════════════════════════
# 문제 간 공백 삽입
# ════════════════════════════════════════════════════════════

PROBLEM_GAP_LINES = 2  # 다른 문제 사이 빈 줄 수


def _insert_problem_spacing(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """다른 문제 번호 사이에 빈 문단 삽입 (같은 문제 안은 그대로)."""
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


# ════════════════════════════════════════════════════════════
# 메인 변환 API
# ════════════════════════════════════════════════════════════


def convert_problems_to_hwpx(
    problems: list[dict[str, Any]],
    template_path: str,
    output_path: str,
    extra_images: dict[str, str] | None = None,
) -> str:
    """
    구조화된 problems 리스트를 HWPX 파일로 변환.

    Args:
        problems: Claude 가 만든 JSON 구조 (sanitize 전 상태여도 OK)
        template_path: template.hwpx 파일 경로
        output_path: 출력 HWPX 경로
        extra_images: {bin_name: image_path} - 이미지 삽입 시

    Returns:
        output_path (성공 시)
    """
    cleaned = sanitize_problems(problems)
    cleaned = _insert_problem_spacing(cleaned)
    pdf_to_hwpx.build_hwpx(
        template_path=str(template_path),
        output_path=str(output_path),
        problems=cleaned,
        extra_images=extra_images or {},
    )
    return str(output_path)


if __name__ == "__main__":
    # 간단 자가 테스트
    tests = [
        r"\triangle O'A'B'",
        r"\overline{AB}^2",
        r"\lim_{x \to 0^+} f(x)",
        r"\alpha_1 + \alpha_2",
    ]
    for t in tests:
        print(f"{t}")
        print(f"  → auto: {_auto_mathrm(t)}")
        print(f"  → hwpeq: {pdf_to_hwpx.latex_to_hwpeq(_auto_mathrm(t))}")
        print()
