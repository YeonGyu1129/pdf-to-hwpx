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
    # 입력 LaTeX 의 \mathit{X} 사전 보호 (skill 변환기가 그냥 {X} 로만 출력해
    # 이탤릭 명시 표현이 사라지므로)
    _mathit_placeholders: list[str] = []

    def _save_mathit(m: re.Match) -> str:
        idx = len(_mathit_placeholders)
        _mathit_placeholders.append(m.group(1))
        return f"\x01{idx}\x01"

    latex = re.sub(r"\\mathit\{([^{}]+)\}", _save_mathit, latex)

    # 호(arc) 표기 사전 보호 — skill 변환기가 인식 못 함
    # \widehat{X}, \overset{\frown}{X}, \overarc{X} → 모두 한컴의 arch 로
    _arc_placeholders: list[str] = []

    def _save_arc(m: re.Match) -> str:
        idx = len(_arc_placeholders)
        _arc_placeholders.append(m.group(1))
        return f"\x02{idx}\x02"

    # 1단계 중첩 중괄호까지 허용: \widehat{\mathrm{AB}} 같은 경우 처리
    _arc_arg = r"((?:[^{}]|\{[^{}]*\})+)"
    latex = re.sub(rf"\\widehat\{{{_arc_arg}\}}", _save_arc, latex)
    latex = re.sub(rf"\\overset\{{\\frown\}}\{{{_arc_arg}\}}", _save_arc, latex)
    latex = re.sub(rf"\\overarc\{{{_arc_arg}\}}", _save_arc, latex)

    # 중첩 \sqrt 사전 변환 (원본 latex_to_hwpeq 은 \sqrt 를 1회만 처리해서
    # 안쪽 \sqrt 가 그대로 남고 step 8 catch-all `\\[a-zA-Z]+` 에 의해
    # 통째로 사라지는 버그가 있음. 예: `\sqrt{(4\sqrt{3})^2+2^2}` →
    # `sqrt {(4{3})^{2}+2^{2}}` (안쪽 √3 소멸).
    # balanced-brace 방식으로 임의 깊이 중첩을 안전하게 처리.
    def _convert_sqrt_balanced(src: str) -> str:
        i, out = 0, []
        n = len(src)
        while i < n:
            mn = re.match(r"\\sqrt\[([^\]]+)\]\{", src[i:])
            m1 = re.match(r"\\sqrt\{", src[i:])
            m = mn or m1
            if m:
                idx_arg = mn.group(1) if mn else None
                start = i + m.end()
                depth, j = 1, start
                while j < n and depth > 0:
                    if src[j] == "{":
                        depth += 1
                    elif src[j] == "}":
                        depth -= 1
                    j += 1
                # j points just past the closing }
                content = src[start : j - 1]
                content = _convert_sqrt_balanced(content)  # 재귀적 내부 처리
                if idx_arg is not None:
                    out.append(f" sqrt [{idx_arg}] {{{content}}}")
                else:
                    out.append(f" sqrt {{{content}}}")
                i = j
            else:
                out.append(src[i])
                i += 1
        return "".join(out)

    latex = _convert_sqrt_balanced(latex)

    out = pdf_to_hwpx._original_latex_to_hwpeq(latex)

    # 보호된 \mathit 복원 → it{X}
    out = re.sub(
        r"\x01(\d+)\x01",
        lambda m: f"it{{{_mathit_placeholders[int(m.group(1))]}}}",
        out,
    )

    # 보호된 호(arc) 복원 → {arch{...}}
    def _restore_arc(m: re.Match) -> str:
        content = _arc_placeholders[int(m.group(1))]
        # \mathrm{X} → rm X 로 변환
        content = re.sub(r"\\mathrm\{([^{}]+)\}", r"rm \1", content)
        # 단순 대문자만이면 자동 rm 적용
        if re.fullmatch(r"[A-Z]+", content):
            content = f"rm {content}"
        return f"{{arch{{{content}}}}}"

    out = re.sub(r"\x02(\d+)\x02", _restore_arc, out)

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
    # 5-a2) \cdots 양옆 공백 (PDF 규칙 ⑦)
    out = re.sub(r"(?<![~ ])cdots", " cdots", out)
    out = re.sub(r"cdots(?![~ ])", "cdots ", out)
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

    # 6-b) `vec rm{X}` / `vec it{X}` 형태로 그룹화
    #     한컴 수식편집기에서 vec 명령은 인자가 {...} 그룹 형태여야 화살표가 위에 정상 표시됨.
    #     skill 변환기가 `vec rm{X}` 로 출력하면 한컴이 `{vec{rm}} rm X` 로 잘못 해석.
    #     `{vec{rm X}}` 형태로 외부 그룹까지 추가해 후속 토큰과 격리.
    # 6-b-1) 첨자 점쌍 (vec rm{X}_{n}rm{Y} ['|prime]?)
    out = re.sub(
        r"vec rm\{([^{}]+)\}(_\{[^{}]+\})rm\{([^{}]+)\}(\s*'\s*|\s+prime\s*)?",
        lambda m: f"{{vec{{rm {m.group(1)} {m.group(2)} rm {m.group(3)}{m.group(4) or ''}}}}}",
        out,
    )
    # 6-b-2) 끝에 첨자만 있는 경우 (vec rm{X}_{n} ['|prime]?)
    out = re.sub(
        r"vec rm\{([^{}]+)\}(_\{[^{}]+\})(\s*'\s*|\s+prime\s*)?",
        lambda m: f"{{vec{{rm {m.group(1)} {m.group(2)}{m.group(3) or ''}}}}}",
        out,
    )
    # 6-b-3) 단순한 점쌍 (vec rm{X} ['|prime]?)
    out = re.sub(
        r"vec rm\{([^{}]+)\}(\s*'\s*|\s+prime\s*)?",
        lambda m: f"{{vec{{rm {m.group(1)}{m.group(2) or ''}}}}}",
        out,
    )
    # 6-b-4) 소문자 벡터 (vec it{X} → {vec{it X}})
    #     한컴이 `vec it{a}` 를 `{vec{it}} it a` 로 잘못 해석.
    #     `{vec{it a}}` 형태로 외부 그룹 추가해 a 까지 vec 인자에 포함시킴.
    out = re.sub(
        r"vec it\{([^{}]+)\}",
        lambda m: f"{{vec{{it {m.group(1)}}}}}",
        out,
    )

    # 6-b-5) 윗줄/모자 등 점쌍 장식 (bar/hat/tilde/dot/ddot)
    #     `bar {rm{AQ}}` 형태는 한컴이 잘못 파싱해 bar 범위가 뒤따르는
    #     연산자(BOT/TIMES 등)·다음 장식까지 번져 먹어버린다
    #     (예: `bar {rm{AQ}} BOT bar {rm{BP}}` → "AQBP" 한 줄 bar, ⊥ 소멸).
    #     vec 와 동일하게 `{bar{rm AQ}}` 로 외부 그룹 + 공백형 rm 으로 격리.
    out = re.sub(
        r"\b(bar|hat|tilde|dot|ddot)\s*\{rm\{([^{}]+)\}\}",
        lambda m: f"{{{m.group(1)}{{rm {m.group(2)}}}}}",
        out,
    )

    # 6-c) `\right|+\left|` 짝짓기 깨짐 안전망
    #     skill 변환기가 절댓값 연속 (\left|...\right|+\left|...\right|) 을
    #     변환할 때 짝이 어긋나 `right left | + left right |` 같은
    #     이상한 출력을 만드는 현상 교정.
    out = re.sub(r"right\s+left\s+\|", r"right |", out)
    out = re.sub(r"left\s+right\s+\|", r"left |", out)

    # 6-d) THEREFORE / BECAUSE 뒤 `~` 띄어쓰기 (한컴 명시적 공백 기호)
    out = re.sub(r"\bTHEREFORE\b\s*", "THEREFORE~ ", out)
    out = re.sub(r"\bBECAUSE\b\s*", "BECAUSE~ ", out)

    # 7) 괄호 경계 보호 (맨 마지막에 적용)
    #    한글 수식편집기가 `}(` 나 `{(` 패턴에서 `(` 를 빈 그룹 `{}` 로
    #    오인하는 버그 회피. 공백 정리 뒤에 실행해야 보존됨.
    out = _paren_boundary(out)
    return out


pdf_to_hwpx.latex_to_hwpeq = _patched_latex_to_hwpeq

# ────────────────────────────────────────────────────────────
# make_section_xml 재구현 (monkey-patch)
# ────────────────────────────────────────────────────────────
# 원본 pdf_to_hwpx.make_section_xml 은 (1) problem 마다 빈 줄을 1개씩 강제로
# 덧붙이고 (2) 미주(endnote)를 지원하지 않는다. 원본은 "외부 스킬" 이라 직접
# 수정하지 않고, 여기서 동일 동작 + 두 가지 개선을 적용한 버전으로 교체한다.
#   - 개선 1: problem 마다 붙던 trailing empty 문단 제거 (풀이 줄 사이 빈 줄 방지)
#   - 개선 2: role=='solution' 엔트리를 미주로 렌더링 (문제 번호로 매칭)
# 헬퍼는 pdf_to_hwpx 모듈의 내부 함수를 그대로 재사용한다.

if not hasattr(pdf_to_hwpx, "_original_make_section_xml"):
    pdf_to_hwpx._original_make_section_xml = pdf_to_hwpx.make_section_xml


def _en_base(prob: dict) -> str:
    """problem number 앞 숫자만 추출 (미주↔문제 매칭). '3-①' → '3'."""
    m = re.match(r"^\s*(\d+)", str(prob.get("number", "")))
    return m.group(1) if m else ""


def _en_subpara(segments: list, eq_id: int, autonum_n=None) -> tuple:
    """미주 subList 안 문단 1개 생성. autonum_n 주어지면 앞에 미주번호 마커 삽입.
    문단/run 속성은 템플릿의 '작동하는' 미주와 동일하게 맞춘다
    (id=2147483648 = 미주 sub-list 전용, paraPr/style/charPr ref 도 동일)."""
    P = pdf_to_hwpx
    eid = eq_id
    parts = []
    max_h = 1100
    if autonum_n is not None:
        parts.append(
            f'<hp:ctrl><hp:autoNum num="{autonum_n}" numType="ENDNOTE">'
            f'<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" '
            f'suffixChar=")" supscript="0"/></hp:autoNum></hp:ctrl><hp:t> </hp:t>'
        )
    for seg in segments:
        if seg.get("type") == "text":
            txt = P._xt(seg["content"])
            if txt:
                parts.append(f"<hp:t>{txt}</hp:t>")
        else:
            hwpeq = P.latex_to_hwpeq(seg["content"])
            _, h, _ = P.estimate_eq_size(hwpeq)
            max_h = max(max_h, h)
            parts.append(P._eq_block(eid, hwpeq))
            eid += 1
    parts.append("<hp:t/>")  # 템플릿과 동일하게 run 끝 빈 텍스트
    if autonum_n is not None:
        para_attr = 'id="2147483648" paraPrIDRef="10" styleIDRef="15"'
        run_attr = 'charPrIDRef="3"'
    else:
        para_attr = 'id="2147483648" paraPrIDRef="22" styleIDRef="0"'
        run_attr = 'charPrIDRef="0"'
    bl = int(max_h * 0.85)
    xml = (
        f'<hp:p {para_attr} pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run {run_attr}>{"".join(parts)}</hp:run>'
        f'<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="{max_h}" '
        f'textheight="{max_h}" baseline="{bl}" spacing="272" horzpos="0" '
        f'horzsize="42520" flags="393216"/></hp:linesegarray>'
        f"</hp:p>"
    )
    return xml, eid - eq_id


def _en_make_endnote(sol_entries: list, eq_id: int, num: int, inst_id: int) -> tuple:
    """풀이 엔트리 리스트 → <hp:endNote> XML. 반환 (xml, 사용 eq_id 수)."""
    eid = eq_id
    paras = []
    for i, entry in enumerate(sol_entries):
        p, used = _en_subpara(entry.get("segments", []) or [], eid,
                              autonum_n=num if i == 0 else None)
        eid += used
        paras.append(p)
    if not paras:
        p, used = _en_subpara([], eid, autonum_n=num)
        eid += used
        paras.append(p)
    sublist = (
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        'vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" '
        'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        + "".join(paras) +
        "</hp:subList>"
    )
    xml = (
        f'<hp:endNote number="{num}" suffixChar="41" instId="{inst_id}">'
        + sublist +
        "</hp:endNote>"
    )
    return xml, eid - eq_id


def _patched_make_section_xml(problems: list) -> str:
    P = pdf_to_hwpx
    NS = (
        'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
        'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" '
        'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
        'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
        'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
        'xmlns:opf="http://www.idpf.org/2007/opf/"'
    )
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>',
        f'<hs:sec {NS}>',
        '<hp:p id="1" paraPrIDRef="0" styleIDRef="0" '
        'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">{P._SECPR}</hp:run>'
        '<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" '
        'textheight="1000" baseline="850" spacing="600" horzpos="0" '
        'horzsize="42520" flags="393216"/></hp:linesegarray>'
        '</hp:p>',
    ]

    eq_id = 1000

    # ── 미주 준비: role=='solution' 분리 + base 번호별 그룹 ──
    solutions_by_base: dict = {}
    body_problems: list = []
    for prob in problems:
        if isinstance(prob, dict) and prob.get("role") == "solution":
            solutions_by_base.setdefault(_en_base(prob), []).append(prob)
        else:
            body_problems.append(prob)
    endnote_done: set = set()
    endnote_num = 0
    inst_seq = 1800000000

    def _attach(para_xml: str, base: str) -> str:
        """문제 본문 run 맨 앞에 미주 마커 삽입. endNote 는 반드시 <hp:ctrl>
        로 감싸야 한컴이 인식한다."""
        nonlocal eq_id, endnote_num, inst_seq
        if (not base) or base in endnote_done or base not in solutions_by_base:
            return para_xml
        endnote_num += 1
        inst_seq += 1
        en_xml, used = _en_make_endnote(
            solutions_by_base[base], eq_id, endnote_num, inst_seq)
        eq_id += used
        endnote_done.add(base)
        marker = "<hp:ctrl>" + en_xml + "</hp:ctrl>"
        # _para_from_segments 의 run 여는 태그는 항상 '<hp:run charPrIDRef="0">'.
        # 그 직후(= 문제 본문 맨 앞)에 마커 삽입.
        return para_xml.replace(
            '<hp:run charPrIDRef="0">',
            '<hp:run charPrIDRef="0">' + marker,
            1,
        )

    for p_idx, prob in enumerate(body_problems):
        segments = prob.get("segments", [])

        if prob.get("type") == "image":
            lines.append(P.make_image_para(
                bin_name=prob["bin_name"],
                org_w=prob.get("org_w", 800),
                org_h=prob.get("org_h", 600),
                display_w=prob.get("display_w", None),
                display_h=prob.get("display_h", None),
                pic_id=eq_id + 8000,
            ))
            continue

        is_box = prob.get("box", False)
        prefix = ""  # 원본과 동일하게 prefix 없음

        if not segments:
            text = str(prob.get("text", ""))
            formulas = prob.get("formulas_hwpeq") or [
                P.latex_to_hwpeq(f) for f in prob.get("formulas", [])
            ]
            segments = [{"type": "text", "content": prefix + text}]
            for f in formulas:
                segments.append({"type": "formula", "content": f})
            para_xml, used, _ = P._para_from_segments(segments, eq_id)
            eq_id += used
            lines.append(_attach(para_xml, _en_base(prob)))

        elif is_box:
            rows = [list(r) for r in segments]
            if rows and rows[0] and isinstance(rows[0][0], dict) and rows[0][0].get("type") == "text":
                rows[0] = [{"type": "text", "content": prefix + rows[0][0]["content"]}] + rows[0][1:]
            else:
                rows[0] = [{"type": "text", "content": prefix}] + rows[0]
            box_xml, used = P.make_box_xml(rows, eq_id, box_type=prob.get("box_type", "condition"))
            eq_id += used
            lines.append(box_xml)

        else:
            if segments[0].get("type") == "text":
                segments = [{"type": "text", "content": prefix + segments[0]["content"]}] + segments[1:]
            else:
                segments = [{"type": "text", "content": prefix}] + segments
            para_xml, used, _ = P._para_from_segments(segments, eq_id)
            eq_id += used
            lines.append(_attach(para_xml, _en_base(prob)))

    # 본문에 못 붙인 미주(대응 문단 없음) → 문서 끝 단독 문단으로
    for base, sols in solutions_by_base.items():
        if base in endnote_done:
            continue
        endnote_num += 1
        inst_seq += 1
        en_xml, used = _en_make_endnote(sols, eq_id, endnote_num, inst_seq)
        eq_id += used
        endnote_done.add(base)
        lines.append(
            '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" '
            'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0"><hp:ctrl>{en_xml}</hp:ctrl></hp:run>'
            f'{P._lineseg(1000)}'
            '</hp:p>'
        )

    lines.append("</hs:sec>")
    return "\n".join(lines)


pdf_to_hwpx.make_section_xml = _patched_make_section_xml

# ────────────────────────────────────────────────────────────
# 상수 / 설정
# ────────────────────────────────────────────────────────────

VISION_MODEL = "claude-opus-4-5"
EXTRACT_MODEL = "claude-opus-4-5"
# 모델별 max_tokens 한도에 맞춰 설정. 한도를 넘으면 BadRequestError.
VISION_MAX_TOKENS = 8000
STRUCT_MAX_TOKENS = 8000
VERIFY_MAX_TOKENS = 6000  # 검수 응답 (JSON 리포트)
MAX_TOKENS = 8000  # 하위 호환용 (기존 참조)

# ────────────────────────────────────────────────────────────
# 정확도 프리셋
# ────────────────────────────────────────────────────────────
# 각 키:
#   verify         : 검수 실행 여부
#   max_retries    : 검수 후 자동 수정 최대 재시도 횟수
#   double_pass    : 이중 변환 (동일 이미지 2회 변환 후 비교) 여부
#   default_dpi    : 권장 DPI
ACCURACY_PRESETS = {
    "빠름": {
        "verify": False,
        "max_retries": 0,
        "double_pass": False,
        "default_dpi": 150,
        "desc": "검수 없음 — 가장 저렴, 단순 변환만",
    },
    "균형": {
        "verify": True,
        "max_retries": 1,
        "double_pass": False,
        "default_dpi": 150,
        "desc": "검수 + 자동 수정 1회 — 누락 보정 (비용 약 2~3배)",
    },
    "정확": {
        "verify": True,
        "max_retries": 3,
        "double_pass": False,
        "default_dpi": 200,
        "desc": "검수 + 자동 수정 3회 — 오타까지 수정 (비용 약 4배)",
    },
    "최정밀": {
        "verify": True,
        "max_retries": 3,
        "double_pass": True,
        "default_dpi": 200,
        "desc": "정확 모드 + 이중 변환 비교 — 최고 정확도 (비용 약 8배)",
    },
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PDF_EXTS = {".pdf"}
DEFAULT_TEMPLATE = HERE / "template.hwpx"

VISION_SCOPE_PROBLEMS_ONLY = r"""## ⚠️ 인식 범위 — 문제 본문만
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
- 짧은 문항이라도 건너뛰지 말 것"""

VISION_SCOPE_ALL = r"""## ⚠️ 인식 범위 — 보이는 것 모두
- 페이지에 있는 **모든 텍스트·수식·표·손글씨**를 그대로 옮겨 적으세요
- 문항(문제), 풀이, 해설, 답, 교사 주석 등 **전부 포함**
- 문단·줄바꿈 구조도 가능한 그대로 유지
- 손글씨 필기가 있으면 그 내용도 읽어서 포함

## ⚠️ 누락 금지
페이지의 **어떤 내용도 빠뜨리지 말 것**:
- 작은 주석, 각주, 페이지 하단 글자까지
- 여러 박스/섹션이 있으면 모두 포함
- 손글씨 메모도 읽어서 기록"""

VISION_PROMPT_TEMPLATE = r"""이 이미지에 있는 **수학 내용**을 정확히 인식하세요.

{SCOPE_SECTION}

## 🌟 LaTeX 작성 규칙 (반드시 준수)

### ① 모든 알파벳·숫자·수학기호는 수식($...$)으로
본문 텍스트에 알파벳·숫자 절대 넣지 말 것.
- ❌ "최댓값을 M이라 하면, M=2"
- ✅ "최댓값을 $M$이라 하면, $M=2$"

### ② 단일 대문자의 글꼴 구분 (같은 문자라도 용도에 따라 다름)
- **점·꼭짓점·원점·중심** (점 A, 꼭짓점 P, 원점 O): `\mathrm{A}`, `\mathrm{P}`, `\mathrm{O}` (로만체, 필수)
- **변수** (최댓값 M, 최솟값 m, 적분값 I, 합 S, 함수 f): `M`, `m`, `I`, `S`, `f` (그대로, 자동 이탤릭)
- **도형·집합·곡선·영역·확률변수** (구 S, 원 C, 영역 D, 집합 A, 확률변수 X): `\mathit{S}`, `\mathit{C}`, `\mathit{D}`, `\mathit{A}`, `\mathit{X}` (이탤릭 명시)

**판별법**:
- "점/꼭짓점/원점/중심 X" → `\mathrm{X}` (로만체)
- "구/원/평면/영역/곡선/타원 X" → `\mathit{X}` (이탤릭 명시)
- "**집합 X**", "**원소가 ~인 집합 X**" → `\mathit{X}` (이탤릭 명시)
- "**확률변수 X**", "**X 가 정규분포를 따른다**" 같은 확률통계 맥락 → `\mathit{X}` (이탤릭 명시)
- "최댓값/적분값/합/함수 X", 그냥 변수 X → `X` (그대로)

### ③ 벡터·선분·점쌍은 \mathrm 필수 ⚠️ **매우 중요**

이미지에서 **글자 위에 작은 화살표(→)가 있으면 무조건 벡터**.
**"AP", "BQ" 처럼만 보여도 위에 화살표가 있다면 반드시 `\overrightarrow{\mathrm{AP}}` 로 쓸 것.**

#### A) 점쌍 벡터 — 대문자 두 글자 (\overrightarrow)
- 기본: `\overrightarrow{\mathrm{AB}}` (화살표 있는 AB)
- 합 벡터: `\overrightarrow{\mathrm{AP}} + \overrightarrow{\mathrm{BQ}}`
- 첨자 점쌍: `\overrightarrow{\mathrm{O}_{1}\mathrm{P}}` (O 위에 1)
- 벡터 크기: `\left|\overrightarrow{\mathrm{AP}}+\overrightarrow{\mathrm{BQ}}\right|`

#### B) 소문자 벡터 — 1글자 (\vec{}) ⚠️ **점쌍과 구분 필수**
- 기본: `\vec{a}`, `\vec{b}`, `\vec{p}`, `\vec{u}`, `\vec{v}`, `\vec{x}` (모두 자동으로 이탤릭 처리)
- 영벡터: `\vec{0}` (숫자 0 은 이탤릭 안 됨)
- 사칙 연산: `\vec{a}+\vec{b}`, `\vec{a}-\vec{b}`, `k\vec{a}`, `-\vec{a}`
- 선형 결합: `k\vec{a}+l\vec{b}`
- 분수 계수: `\dfrac{1}{2}\vec{a}-\dfrac{2}{3}\vec{b}`
- 절댓값: `\left|\vec{a}\right|`, `\left|\vec{a}\right|^{2}`, `\left|2\vec{a}\right|` (반드시 `\left|...\right|`)
- 내적: `\vec{a} \cdot \vec{b}`
- 좌표: `\vec{a}=(2, -1)`

#### C) 선분/각/삼각형/호 (화살표 없을 때)
- 선분: `\overline{\mathrm{AB}}`
- 점 좌표: `\mathrm{A}(2, 0)`
- 각: `\angle \mathrm{ABC}`
- 삼각형: `\triangle \mathrm{ABC}`
- **호**: `\widehat{\mathrm{AB}}` (글자 위 둥근 호 ⌒ — 자동으로 `{arch{rm AB}}` 변환)

⚠️ **자주 놓치는 실수**:
- ❌ 이미지에 화살표 있는데 그냥 `AB`, `\mathrm{AB}` 로 인식 (화살표 누락) → ✅ `\overrightarrow{\mathrm{AB}}`
- ❌ `\vec{AB}` (점쌍에 `\vec` 쓰면 화살표 짧아짐) → ✅ `\overrightarrow{\mathrm{AB}}`
- ❌ `\overrightarrow{a}` (소문자에 `\overrightarrow` 쓰지 말 것) → ✅ `\vec{a}`
- ❌ `\vec a` (중괄호 없음) → ✅ `\vec{a}`
- ❌ `|\vec{a}|` (절댓값에 일반 `|` 사용) → ✅ `\left|\vec{a}\right|`

### ④ 비례식은 하나의 수식으로
- ✅ `$3:1$`, `$1:2:3$`
- ❌ 절대 쪼개지 말 것

### ⑤ 큰 표현 감싸는 괄호는 `\left( \right)` ⚠️ **매우 중요**

⚠️ **벡터·분수·근호·합이 들어간 괄호는 반드시 `\left( \right)`** — 일반 `()` 쓰면 한컴에서 크기 조정 안 됨.

#### A) 기본 사용
- ✅ `\left(\overrightarrow{\mathrm{O}_1\mathrm{P}}+\overrightarrow{\mathrm{O}_3\mathrm{Q}'}\right)`
- ✅ `\left(\dfrac{1}{2}\vec{a}-\dfrac{2}{3}\vec{b}\right)`
- ✅ `\left|\overrightarrow{\mathrm{AP}}+\overrightarrow{\mathrm{BQ}}\right|`
- ❌ `(\overrightarrow{\mathrm{O}_1\mathrm{P}}+\overrightarrow{\mathrm{O}_3\mathrm{Q}'})` — 벡터 합인데 `\left( \right)` 안 씀
- 단, **단순 정수·변수만** 들어가는 경우 (`(2, 0)`, `f(x)`, `(k+1)`) 는 그냥 `()` 도 OK

#### B) `\left/\right` 짝짓기 — 버그 회피 매우 중요

| 구분자 | 여는 쪽 | 닫는 쪽 |
|------|---------|---------|
| 소괄호 | `\left(` | `\right)` |
| 절댓값 | `\left\|` | `\right\|` |
| 중괄호 | `\left\{` | `\right\}` |
| 대괄호 | `\left[` | `\right]` |

**작성 규칙 (반드시 지킬 것)**:
1. `\left` 와 `\right` 는 **항상 1:1 로 짝**. 둘 다 같은 수식 안에.
2. `\left` / `\right` 직후 **즉시 구분자**가 와야 함 (공백 없이): `\left|`, `\right|`
3. 짝의 종류 일치: `\left|` ↔ `\right|`, `\left(` ↔ `\right)` (섞으면 안 됨)
4. 한 수식 안에서 절댓값은 **모두** `\left|/\right|` 로 통일 — 일반 `|` 와 혼용 금지

**잘못된 패턴 (출력이 깨짐)**:
- ❌ `\left|\vec{a}\right + \left|\vec{b}\right|` — `\right` 뒤 `|` 빠뜨림
- ❌ `\left|\vec{a}\right\left|\vec{b}\right|` — `\right\left` 사이에 내용 없음
- ❌ `|\vec{a}|+\left|\vec{b}\right|` — 일반 `|` 와 `\left/\right` 혼용

**올바른 패턴**:
- ✅ `\left|\vec{a}\right|+\left|\vec{b}\right|+\left|\vec{c}\right|`
- ✅ `\left|\overrightarrow{\mathrm{AD}}\right|+\left|\overrightarrow{\mathrm{BE}}\right|`
- ✅ `\left|\vec{a}\right|=4+2\times\left(4-2\sqrt{2}\right)`

### ⑥ LaTeX 공백 명령 금지
- ❌ `\;`, `\,`, `\:`, `\!`, `\quad`, `\qquad` 모두 금지
- ✅ 일반 공백 사용

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
- 그래프·도형이 있는 위치에는 `[그림: 간단한 설명]` 이라고 표시

## ⚠️ 임시 식 번호·기호는 이미지에 보이는 그대로
풀이/해설에서 식을 가리키는 임시 기호(`㉠ ㉡ ㉢ ㉣`, `ⓐ ⓑ ⓒ`, `①②③`,
`(1)(2)(3)`, `가)나)다)` 등)는 **이미지에 표시된 그대로** 옮기세요.

- ❌ 문제가 바뀌었는데 직전 문제 끝 기호에서 이어붙이지 말 것 (예: 1번이 ㉡ 까지 썼다고 2번을 ㉢ 부터 시작 NO).
- ✅ 각 문제는 자기 이미지에 보이는 기호를 그대로 (대부분 문제마다 ㉠ 부터 다시 시작합니다).
- ✅ 기호 종류·순서·표기를 임의로 바꾸지 말 것."""


def build_vision_prompt(mode: str) -> str:
    """모드별 Vision 프롬프트 생성. mode: 'problems_only' | 'all'"""
    scope = VISION_SCOPE_ALL if mode == "all" else VISION_SCOPE_PROBLEMS_ONLY
    return VISION_PROMPT_TEMPLATE.replace("{SCOPE_SECTION}", scope)


STRUCT_SCOPE_PROBLEMS_ONLY = r"""## ⚠️ 변환 범위 — 문항만
- **문항(문제 본문)만** JSON 으로 변환합니다
- 다음은 **절대 JSON 에 포함하지 마세요**:
  - 풀이, 해설, 답안, 정답 설명
  - "풀이:", "해설:", "답:", "Sol)" 같은 라벨 이후 내용
  - 보조 해설이나 교사 주석
- 입력에 풀이/해설이 섞여 있어도 **문항 부분만** 골라서 변환"""

STRUCT_SCOPE_ALL = r"""## ⚠️ 변환 범위 — 모든 내용
- 입력에 있는 **모든 내용**을 JSON 으로 변환합니다
- 문항·풀이·해설·답·교사 주석 **모두 포함**
- 원본의 순서와 구조를 유지하면서 segments 로 분해
- 손글씨 필기·주석도 모두 포함"""

# 미주(endnote) 모드 — '보이는 것 모두' + 토글 ON 일 때만 프롬프트에 추가
STRUCT_ENDNOTE_INSTRUCTION = r"""

## 🔖 풀이 → 미주(endnote) 처리 (특수 모드 ON)
풀이·해설·답·정답 설명에 해당하는 내용은 **일반 problem 이 아니라 미주 엔트리**로 출력하세요.

### 미주 엔트리 형식
```
{"number": "<설명 대상 문제 번호>", "role": "solution", "segments": [ ... ]}
```
- `role` 은 반드시 `"solution"` 으로.
- `number` 는 그 풀이가 **설명하는 문제의 번호**와 동일하게. (예: 3번 문제의 풀이 → `"3"`)
- segments 분해 규칙(text/formula)은 일반 문단과 동일.

### 미주 내용 구조 ⭐ (각 문제의 미주는 아래 순서로 엔트리 배열)
한 문제에 대한 미주 엔트리 그룹은 **정확히 이 순서**로 출력하세요:

1. **첫 엔트리 = `[정답] {정답내용}`**
   - 정답을 segments 에 표시. 예:
     ```
     {"number":"3","role":"solution","segments":[
        {"type":"text","content":"[정답] "},
        {"type":"formula","content":"17"}
     ]}
     ```
   - 원본에 `[정답] X` / `답: X` / `정답: X` 같은 표기가 있으면 그 값을 그대로 사용.
   - 정답이 짧은 식이면 `formula`, 단순 텍스트(예: `참`, `거짓`, `없음`)면 `text` 로.
   - 정답을 찾지 못하면 segments 를 `[{"type":"text","content":"[정답] "}]` 만 두기 (빈 자리표시).

2. **두 번째 엔트리 = `[해설]` 헤더 한 줄**
   ```
   {"number":"3","role":"solution","segments":[{"type":"text","content":"[해설]"}]}
   ```
   - segments 는 정확히 `[해설]` 텍스트 한 개. 다른 내용 포함 금지.

3. **세 번째 이후 엔트리 = 풀이 본문 줄별로 1엔트리**
   - 원본의 `[풀이]` / `풀이)` / `Sol)` 같은 헤더는 **이미 위에서 `[해설]` 로 대체했으니 본문에 다시 적지 말 것**.
   - 풀이 본문의 각 줄을 별도 `role:"solution"` 엔트리로 (모두 같은 number).
   - 본문 줄 안의 수식은 `formula`, 한글 설명은 `text` 로 분해.

### 본문 vs 풀이 구분
- 문제 본문·객관식 보기·조건 박스 → 기존대로 일반 엔트리(`main:true`/`main:false`/`box`).
- "풀이", "해설", "[정답]", "답:", "Sol)" 등으로 시작하거나 그 뒤에 오는 계산 과정 → `role:"solution"`.
- 풀이 엔트리 그룹은 본문 엔트리보다 **뒤에 배치**해도 됩니다. number 로 자동 매칭됩니다.

### 주의
- 풀이 내용을 일반 문단(main)으로 잘못 출력하면 본문에 그대로 찍혀버립니다. 반드시 `role:"solution"` 으로.
- 위 1·2·3 순서를 어기지 마세요 — `[정답]`이 첫 줄, `[해설]`이 두 번째 줄, 나머지가 풀이 본문.
- 어떤 문제의 풀이인지 번호가 불명확하면, 가장 가까운(직전) 문제 번호로 매칭하세요.
- **식 번호 기호(㉠㉡㉢, ⓐⓑⓒ, (1)(2)(3) 등)는 원본에 보이는 그대로** 옮기세요.
  앞 문제의 마지막 기호에서 이어붙이지 말 것 — 각 문제는 자기 원본 표기를 그대로 따라갑니다."""

STRUCT_PROMPT_TEMPLATE = r"""다음 수학 문제 텍스트를 JSON 구조로 변환하세요.

{SCOPE_SECTION}

## ⚠️ 문항 누락 금지 (문항 범위 내에서)
- 문제 번호가 1, 2, 3, ... 이어질 때 **중간 번호를 건너뛰지 말 것**
- 짧은 문항이라도 포함
- 하나의 문항 안에서는 **내용 요약·축약 금지**. 모든 문장을 segments 로 분해

## ⚠️ 임시 기호·식 번호는 원문 그대로 (재번호·이어쓰기 절대 금지)
풀이/해설에서 식을 가리키는 임시 기호 — 예: `㉠ ㉡ ㉢ ㉣`, `ⓐ ⓑ ⓒ`, `①②③`,
`(1) (2) (3)`, `가) 나) 다)` — 는 **원본 PDF/이미지에 보이는 그대로** 출력하세요.

- ❌ **문제가 바뀌었는데 직전 문제의 마지막 기호에서 이어붙여 매기지 말 것.**
  - 예: 1번 풀이가 ㉠·㉡ 까지 썼다고, 2번 풀이를 ㉢ 부터 시작 → **NO**
  - 2번 원본이 ㉠ 부터 다시 시작했다면 출력도 ㉠ 부터 시작.
- ✅ 각 문제는 **자기 원본에 등장하는 기호 그대로** 옮기기.
- ✅ 같은 문제 안에서도 원본의 순서·기호 종류·표기를 그대로 (재정렬·교체 금지).
- 본문·보기·풀이·해설·답 **모든 영역에 공통 적용**.
- 보이지 않는 기호를 추가하지도, 보이는 기호를 빠뜨리지도 말 것.

## 🌟 LaTeX 작성 규칙 (반드시 준수)

### ① 모든 알파벳·숫자·수학기호는 formula 세그먼트로
- ❌ `{"type":"text","content":"최댓값을 M이라 하면, M=2"}`
- ✅ `{"type":"text","content":"최댓값을 "}, {"type":"formula","content":"M"}, {"type":"text","content":"이라 하면, "}, {"type":"formula","content":"M=2"}`

### ② 단일 대문자 글꼴 구분
- **점/꼭짓점/원점/중심**: `\mathrm{A}` (로만체, 필수)
- **변수** (최댓값 M, 함수 f): `M`, `f` (그대로, 자동 이탤릭)
- **도형/집합/곡선/영역/확률변수**: `\mathit{S}`, `\mathit{C}`, `\mathit{A}`, `\mathit{X}` (이탤릭 명시)
  - 집합: `집합 \mathit{A}`, `\mathit{A} \cap \mathit{B}`
  - 확률변수: `확률변수 \mathit{X}`, `\mathit{X} \sim N(\mu, \sigma^2)`

### ③ 벡터/선분/점쌍은 \mathrm 필수 ⚠️ **매우 중요**

이미지/원문에서 **글자 위에 화살표(→)가 있는 것은 무조건 벡터**.

#### A) 점쌍 벡터 (대문자 두 글자 이상) — `\overrightarrow{\mathrm{}}`
- 기본: `\overrightarrow{\mathrm{AB}}` (절대 `\mathrm{AB}` 만 쓰지 말 것)
- 첨자 점쌍: `\overrightarrow{\mathrm{O}_{1}\mathrm{P}}`
- 벡터 합: `\overrightarrow{\mathrm{AP}}+\overrightarrow{\mathrm{BQ}}`
- 벡터 크기: `\left|\overrightarrow{\mathrm{AP}}+\overrightarrow{\mathrm{BQ}}\right|`

#### B) 소문자 벡터 (1글자) — `\vec{}`
- 기본: `\vec{a}`, `\vec{b}`, `\vec{p}`, `\vec{v}`, `\vec{x}`
- 영벡터: `\vec{0}` (0은 이탤릭 안 됨)
- 사칙: `\vec{a}+\vec{b}`, `k\vec{a}+l\vec{b}`, `\dfrac{1}{2}\vec{a}-\dfrac{2}{3}\vec{b}`
- 절댓값: `\left|\vec{a}\right|` (일반 `|` 금지)
- 내적: `\vec{a} \cdot \vec{b}`

#### C) 기타
- 선분 (화살표 없을 때만): `\overline{\mathrm{AB}}`
- 호: `\widehat{\mathrm{AB}}` (글자 위 둥근 호)
- 각: `\angle \mathrm{ABC}`
- 삼각형: `\triangle \mathrm{ABC}`
- 점 좌표: `\mathrm{A}(2, 0)` (점 이름인데 `\mathrm` 누락 금지)

⚠️ **흔한 실수**:
- ❌ `\overrightarrow{a}` (소문자에 `\overrightarrow`) → ✅ `\vec{a}`
- ❌ `\vec{AB}` (점쌍에 `\vec`) → ✅ `\overrightarrow{\mathrm{AB}}`
- ❌ `\vec a` (중괄호 없음) → ✅ `\vec{a}`
- Vision 출력에 화살표 누락돼도, **벡터 컨텍스트면 `\overrightarrow{}`/`\vec{}` 명시적 추가**

### ④ 비례식은 무조건 하나의 formula
- ✅ `{"type":"formula","content":"3:1"}`
- ❌ `{"type":"formula","content":"3"}, {"type":"text","content":" : "}, {"type":"formula","content":"1"}` 절대 쪼개지 말 것

### ⑤ 큰 표현 감싸는 괄호는 `\left( \right)` ⚠️ **매우 중요**

벡터·분수·근호·합이 들어간 괄호는 반드시 `\left( \right)` 로.
- ✅ `\left(\overrightarrow{\mathrm{O}_1\mathrm{P}}+\overrightarrow{\mathrm{O}_3\mathrm{Q}'}\right)`
- ✅ `\left(\dfrac{1}{2}\vec{a}-\dfrac{2}{3}\vec{b}\right)`
- ✅ `\left|\overrightarrow{\mathrm{AP}}+\overrightarrow{\mathrm{BQ}}\right|`
- ❌ `(\overrightarrow{X}+\overrightarrow{Y})` ← 벡터인데 그냥 `()`
- 단순 정수·변수만 (`(2, 0)`, `f(x)`) 는 그냥 `()` 도 OK

#### `\left/\right` 짝짓기 규칙 — 깨짐 방지
1. `\left` 와 `\right` 는 **1:1 짝**, 항상 같은 수식 안에
2. 직후에 구분자 즉시: `\left|`, `\right|`, `\left(`, `\right)` (공백 없이)
3. 짝 종류 일치: `\left| ↔ \right|`, `\left( ↔ \right)`
4. 일반 `|` 와 `\left|/\right|` 혼용 금지 — 한 수식 안에서 통일

❌ `\left|\vec{a}\right + \left|\vec{b}\right|` (right 뒤 `|` 빠뜨림)
❌ `|\vec{a}|+\left|\vec{b}\right|` (혼용)
✅ `\left|\vec{a}\right|+\left|\vec{b}\right|+\left|\vec{c}\right|`

### ⑥ LaTeX 공백 명령 금지
- ❌ `\;`, `\,`, `\:`, `\!`, `\quad`, `\qquad`
- ✅ 일반 공백 사용

### ⑦ 자동 변환 (그대로 쓰면 됨)
- `\cdots` → 자동 공백 추가
- 연속 대문자 2글자+ (AB, ABC) → 자동 `\mathrm`
- 단일 대/소문자 변수 → 자동 이탤릭

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


def build_struct_prompt(mode: str, endnote: bool = False) -> str:
    """모드별 Struct 프롬프트 생성. mode: 'problems_only' | 'all'
    endnote=True 이고 mode=='all' 이면 풀이를 미주로 출력하라는 지시를 덧붙인다."""
    scope = STRUCT_SCOPE_ALL if mode == "all" else STRUCT_SCOPE_PROBLEMS_ONLY
    prompt = STRUCT_PROMPT_TEMPLATE.replace("{SCOPE_SECTION}", scope)
    if endnote and mode == "all":
        prompt += STRUCT_ENDNOTE_INSTRUCTION
    return prompt


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


def vision_recognize(
    client: Anthropic,
    image_paths: list[Path],
    mode: str = "problems_only",
) -> list[str]:
    """이미지들을 Claude Vision 으로 읽어 **페이지별** LaTeX 섞인 평문 리스트 반환.

    mode:
      - "problems_only": 문항(문제 본문)만 추출
      - "all": 풀이·해설·주석 모두 포함
    """
    prompt = build_vision_prompt(mode)
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
                        {"type": "text", "text": prompt},
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
    mode: str = "problems_only",
    endnote: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """
    페이지 하나의 평문을 JSON 으로 구조화.
    반환: (problems, truncated) — truncated 는 응답이 잘렸는지 여부.
    """
    prompt = build_struct_prompt(mode, endnote=endnote)
    resp = client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "user",
                "content": prompt + "\n\n입력:\n" + raw_text,
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


def _spacing_keep_solutions(cleaned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """문제 사이 빈 줄 삽입은 본문 엔트리에만 적용하고, 미주(role=='solution')
    엔트리는 분리해 끝에 그대로 붙인다. (미주 엔트리 사이에 gap dummy 가 끼어
    본문에 빈 줄로 새는 것을 방지; 미주는 빌더에서 번호로 매칭됨)"""
    body = [p for p in cleaned
            if not (isinstance(p, dict) and p.get("role") == "solution")]
    sols = [p for p in cleaned
            if isinstance(p, dict) and p.get("role") == "solution"]
    return _insert_problem_spacing(body) + sols


def structure_problems(
    client: Anthropic,
    raw_texts: list[str],
    mode: str = "problems_only",
    endnote: bool = False,
) -> list[dict[str, Any]]:
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
            page_problems, truncated = structure_single(
                client, page_text, mode=mode, endnote=endnote)
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
                    chunk_probs, _ = structure_single(
                        client, chunk, mode=mode, endnote=endnote)
                    recovered.extend(chunk_probs)
                except json.JSONDecodeError:
                    continue
            # 재시도 결과가 더 많으면 채택, 아니면 원래 결과 유지
            if len(recovered) > len(page_problems):
                page_problems = recovered

        all_problems.extend(page_problems)

    progress.progress(1.0, text="문제 구조화 완료")
    cleaned = sanitize_problems(all_problems)
    return _spacing_keep_solutions(cleaned)


# ════════════════════════════════════════════════════════════
# 검수 (verify) 단계
# ════════════════════════════════════════════════════════════

VERIFY_PROMPT = r"""당신은 수학 문제 변환 결과의 **검수자**입니다.

원본 이미지와 변환된 텍스트(LaTeX 포함)를 비교하여
**누락·오타·잘못된 수식**을 찾으세요.

## 검수 기준
1. **누락**: 원본에 있는데 변환 결과에 빠진 문제 / 보기 / 수식
2. **오타**: 한글 단어가 잘못 변환된 경우 ("최댓값" ↔ "최솟값" 등)
3. **수식 오류**: LaTeX 수식이 원본과 다른 경우 (숫자 틀림, 변수 틀림, 첨자 누락 등)
4. **구조 오류**: 객관식 보기가 본문에 붙어버린 경우 등

## 응답 형식 — 반드시 JSON
```json
{
  "is_clean": false,
  "issues_count": 3,
  "missing": [
    {"problem_number": "12", "description": "12번 문제 전체 누락"}
  ],
  "errors": [
    {"problem_number": "5", "type": "오타", "original": "최솟값", "current": "최댓값", "fix": "최솟값"},
    {"problem_number": "8", "type": "수식", "original": "x^2+y^2=4", "current": "x^2+y^2=5", "fix": "x^2+y^2=4"}
  ],
  "summary": "문제 12 누락, 문제 5 오타, 문제 8 수식 오류"
}
```

오타가 전혀 없으면:
```json
{"is_clean": true, "issues_count": 0, "missing": [], "errors": [], "summary": "검수 통과"}
```

## 주의
- 사소한 공백·줄바꿈 차이는 무시
- 한컴 수식 명령(`rm{}`, `vec{}` 등)은 hwpEQ 변환 산물이므로 무시 — **수식 의미**만 비교
- JSON 만 반환 (마크다운 코드펜스 금지)
"""


def _problems_to_text(problems: list[dict[str, Any]]) -> str:
    """problems 리스트를 검수용 평문(LaTeX 포함)으로 직렬화."""
    lines: list[str] = []
    for prob in problems:
        if prob.get("type") == "image_placeholder":
            lines.append(f"[그림: {prob.get('description', '')}]")
            continue
        num = prob.get("number", "")
        segs = prob.get("segments")
        if not isinstance(segs, list):
            continue
        # 박스 — 2차원
        if prob.get("box") and segs and isinstance(segs[0], list):
            lines.append(f"\n[{num} {prob.get('box_type', 'box')}]")
            for row in segs:
                row_text = "".join(
                    f"${s['content']}$" if s.get("type") == "formula" else s.get("content", "")
                    for s in row if isinstance(s, dict)
                )
                lines.append(row_text)
            continue
        # 일반 문단
        body = "".join(
            f"${s['content']}$" if s.get("type") == "formula" else s.get("content", "")
            for s in segs if isinstance(s, dict)
        )
        if num:
            lines.append(f"\n{num}. {body}")
        else:
            lines.append(body)
    return "\n".join(lines)


def verify_problems(
    client: Anthropic,
    image_paths: list[Path],
    problems: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    원본 이미지(들) 와 변환된 problems 비교 검수.
    반환: {"is_clean": bool, "issues_count": int, "missing": [...], "errors": [...]}
    """
    converted_text = _problems_to_text(problems)

    # 모든 이미지를 한 번의 요청에 첨부 (페이지 적을 때) 또는 첫 페이지만
    # 간단히 모든 이미지 첨부. Vision 한도(20장) 넘으면 처음 5장만.
    use_images = image_paths[:5] if len(image_paths) > 5 else image_paths

    image_blocks = []
    for path in use_images:
        img_bytes, mime = prepare_image_for_vision(path)
        img_b64 = base64.b64encode(img_bytes).decode()
        image_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": img_b64},
        })

    content_blocks = image_blocks + [
        {"type": "text", "text": VERIFY_PROMPT + "\n\n## 변환된 텍스트\n\n" + converted_text}
    ]

    resp = client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=VERIFY_MAX_TOKENS,
        messages=[{"role": "user", "content": content_blocks}],
    )
    text = resp.content[0].text
    try:
        data = _parse_json_loose(text)
    except json.JSONDecodeError:
        # 파싱 실패 시 안전한 기본값
        return {"is_clean": True, "issues_count": 0, "missing": [], "errors": [],
                "summary": "검수 응답 파싱 실패 - 검수 건너뜀"}
    # 기본 필드 채우기
    data.setdefault("is_clean", data.get("issues_count", 0) == 0)
    data.setdefault("issues_count", len(data.get("missing", [])) + len(data.get("errors", [])))
    data.setdefault("missing", [])
    data.setdefault("errors", [])
    data.setdefault("summary", "")
    return data


def _verification_to_feedback(verification: dict[str, Any]) -> str:
    """검수 결과를 다음 구조화 호출에 전달할 피드백 텍스트로 변환."""
    lines = ["## ⚠️ 이전 변환 검수 결과 (반드시 반영하여 수정)"]

    missing = verification.get("missing", [])
    if missing:
        lines.append("\n### 누락된 항목 — 이번에는 반드시 포함")
        for m in missing:
            num = m.get("problem_number", "?")
            desc = m.get("description", "")
            lines.append(f"- 문제 {num}: {desc}")

    errors = verification.get("errors", [])
    if errors:
        lines.append("\n### 오타·오류 — 이번에는 다음과 같이 수정")
        for e in errors:
            num = e.get("problem_number", "?")
            etype = e.get("type", "")
            orig = e.get("original", "")
            curr = e.get("current", "")
            fix = e.get("fix", "")
            lines.append(
                f"- 문제 {num} ({etype}): 잘못 '{curr}' → 올바름 '{fix or orig}'"
            )

    lines.append("\n위 사항을 모두 반영해서 처음부터 다시 정확하게 변환하세요.")
    return "\n".join(lines)


def regenerate_with_feedback(
    client: Anthropic,
    raw_texts: list[str],
    verification: dict[str, Any],
    mode: str = "problems_only",
    endnote: bool = False,
) -> list[dict[str, Any]]:
    """검수 피드백을 반영해 problems 재생성."""
    feedback = _verification_to_feedback(verification)

    all_problems: list[dict[str, Any]] = []
    progress = st.progress(0.0, text="검수 결과 반영 재변환 중…")
    total = len(raw_texts)
    for idx, page_text in enumerate(raw_texts, 1):
        progress.progress((idx - 1) / total, text=f"재변환 중… ({idx}/{total})")
        if not page_text.strip():
            continue
        # 피드백을 입력 텍스트 앞에 추가
        augmented_text = feedback + "\n\n## 원본 입력 텍스트\n" + page_text
        try:
            page_problems, _ = structure_single(
                client, augmented_text, mode=mode, endnote=endnote)
        except json.JSONDecodeError:
            continue
        all_problems.extend(page_problems)
    progress.progress(1.0, text="재변환 완료")
    cleaned = sanitize_problems(all_problems)
    return _spacing_keep_solutions(cleaned)


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

    # 0-d) \left( / \right) 는 유지 (PDF 규칙 ⑤)
    #     큰 표현식(분수/벡터/근호) 감쌀 때 자동 크기 조정 필요.
    #     후처리에서 'left ( ... right )' → 'left( ... right)' 로 공백만 제거.

    # 0-e) 불필요한 LaTeX 공백 명령 제거 (PDF 규칙 ⑥)
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

    # 0-g) 소문자 변수 + \vec 사이 공백 강제 (스칼라 곱 m\vec{a} 처리)
    #     m\vec{a} → m \vec{a}  : 후처리에서 m 이 단일 소문자로 인식되어 it{m} 변환되도록
    s = re.sub(r"([a-z])(?=\\vec\b)", r"\1 ", s)

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

        extract_mode_label = st.radio(
            "📝 추출 범위",
            options=["문제만", "보이는 것 모두"],
            index=0,
            help=(
                "• 문제만: 문항(본문)만 추출하고 풀이·해설·답은 제외\n"
                "• 보이는 것 모두: 풀이·해설·손글씨·주석 포함 전부 옮겨 적음"
            ),
        )
        extract_mode = "problems_only" if extract_mode_label == "문제만" else "all"

        # 풀이를 미주(endnote)로 처리 — '보이는 것 모두' 모드에서만 의미 있음
        endnote_toggle = st.checkbox(
            "🔖 풀이를 미주로 처리",
            value=False,
            disabled=(extract_mode != "all"),
            help=(
                "켜면 풀이·해설·답을 본문에 펼치지 않고, 해당 문제 뒤에 미주 번호"
                "(1) 2) …)로 달고 내용은 문서 맨 끝에 모아 표시합니다.\n"
                "'보이는 것 모두' 모드에서만 동작합니다."
            ),
        )
        endnote = endnote_toggle and extract_mode == "all"
        if endnote_toggle and extract_mode != "all":
            st.caption("⚠️ 미주 처리는 '보이는 것 모두' 모드에서만 적용됩니다.")

        # 정확도 프리셋
        accuracy_label = st.radio(
            "🎚️ 정확도 레벨",
            options=list(ACCURACY_PRESETS.keys()),
            index=0,  # 기본: 빠름 (비용 절감)
            help="\n".join(f"• {k}: {v['desc']}" for k, v in ACCURACY_PRESETS.items()),
        )
        accuracy = ACCURACY_PRESETS[accuracy_label]
        st.caption(f"📝 {accuracy['desc']}")

        # DPI - 정확도 레벨에서 권장값 자동 적용 (사용자 수정 가능)
        dpi = st.slider(
            "PDF 렌더링 DPI",
            min_value=100, max_value=250,
            value=accuracy["default_dpi"],
            step=10,
            help="높을수록 인식 정확도↑ / 요청 크기↑.",
        )
        show_raw = st.checkbox("Vision 인식 원문 보기", value=False)
        show_json = st.checkbox("구조화 JSON 보기", value=False)
        show_verify = st.checkbox(
            "검수 리포트 보기",
            value=accuracy["verify"],
            help="검수 모드일 때 누락/오타 리포트 표시",
        )

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
            raw_texts = vision_recognize(client, image_paths, mode=extract_mode)
            status.update(label=f"Vision 인식 완료 ({len(raw_texts)}장)", state="complete")

        if show_raw:
            with st.expander("📝 Vision 인식 원문"):
                for i, t in enumerate(raw_texts, 1):
                    st.markdown(f"**페이지 {i}**")
                    st.code(t, language="markdown")

        # 4) 구조화 (페이지별로 나눠 호출 → 병합)
        with st.status("문제 구조화 중…", expanded=False) as status:
            try:
                problems = structure_problems(
                    client, raw_texts, mode=extract_mode, endnote=endnote)
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

        # 4-b) 검수 + 자동 수정 (정확도 프리셋에 따라)
        verify_history: list[dict[str, Any]] = []
        if accuracy["verify"]:
            for attempt in range(accuracy["max_retries"] + 1):
                label = "검수 실행 중…" if attempt == 0 else f"재검수 ({attempt}회차)…"
                with st.status(label, expanded=False) as status:
                    verification = verify_problems(client, image_paths, problems)
                    status.update(
                        label=f"검수 완료 — {verification.get('summary', '')}",
                        state="complete",
                    )
                verify_history.append(verification)

                if verification.get("is_clean", False):
                    st.success("✅ 검수 통과 — 오타·누락 없음")
                    break

                # 마지막 시도면 재변환 안 함
                if attempt >= accuracy["max_retries"]:
                    st.warning(
                        f"⚠️ 검수 후에도 {verification.get('issues_count', 0)}건 남음. "
                        "추가 재시도 한도(`{}`)에 도달.".format(accuracy["max_retries"])
                    )
                    break

                # 재변환
                st.info(
                    f"🔄 {verification.get('issues_count', 0)}건 발견 — 자동 수정 후 재변환합니다."
                )
                with st.status("검수 결과 반영 재변환 중…", expanded=False) as status:
                    problems = regenerate_with_feedback(
                        client, raw_texts, verification,
                        mode=extract_mode, endnote=endnote
                    )
                    status.update(label="재변환 완료", state="complete")

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

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "⬇️ HWPX 다운로드",
                data=data,
                file_name="converted.hwpx",
                mime="application/vnd.hancom.hwpx",
                type="primary",
            )
        # 검수 리포트 마크다운 (검수 실행했을 때만)
        if verify_history:
            report_md = _build_verify_report_md(verify_history, accuracy_label)
            with col_dl2:
                st.download_button(
                    "📋 검수 리포트 (.md)",
                    data=report_md.encode("utf-8"),
                    file_name="verify_report.md",
                    mime="text/markdown",
                )
            if show_verify:
                with st.expander("📋 검수 리포트 (전체 이력)", expanded=True):
                    st.markdown(report_md)


def _build_verify_report_md(history: list[dict[str, Any]], preset_name: str) -> str:
    """검수 이력을 마크다운 리포트로."""
    lines = [
        "# 검수 리포트",
        f"\n- 정확도 레벨: **{preset_name}**",
        f"- 검수 시도 횟수: **{len(history)}회**\n",
    ]
    for i, v in enumerate(history, 1):
        lines.append(f"\n## {i}차 검수")
        lines.append(f"- 상태: {'✅ 통과' if v.get('is_clean') else '⚠️ 보정 필요'}")
        lines.append(f"- 발견 건수: {v.get('issues_count', 0)}건")
        lines.append(f"- 요약: {v.get('summary', '')}")

        missing = v.get("missing", [])
        if missing:
            lines.append("\n### 누락")
            for m in missing:
                lines.append(
                    f"- 문제 {m.get('problem_number', '?')}: {m.get('description', '')}"
                )
        errors = v.get("errors", [])
        if errors:
            lines.append("\n### 오타·오류")
            for e in errors:
                lines.append(
                    f"- 문제 {e.get('problem_number', '?')} ({e.get('type', '?')}): "
                    f"`{e.get('current', '')}` → `{e.get('fix') or e.get('original', '')}`"
                )

    # 최종 상태
    final = history[-1]
    lines.append("\n---\n## 최종 상태")
    if final.get("is_clean"):
        lines.append("✅ **모든 오타 자동 수정 완료**")
    else:
        lines.append(
            f"⚠️ **{final.get('issues_count', 0)}건 잔여** — 수동 확인 권장"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
