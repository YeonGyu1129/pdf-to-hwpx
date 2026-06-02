#!/usr/bin/env python3
"""
pdf_to_hwpx.py  —  PDF 수학 문제 → HWPX 변환
한컴오피스 공식 문서 기준 hwpEQ 코드 사용.
텍스트 사이 인라인 수식(변수, 숫자 포함) 완전 지원.

사용법:
    python pdf_to_hwpx.py input.pdf template.hwpx output.hwpx
    python pdf_to_hwpx.py --test
"""

import os, sys, re, json, zipfile, base64, subprocess, tempfile, shutil
import urllib.request


# ══════════════════════════════════════════════════════════
# 0. 이미지 크롭 유틸리티
# ══════════════════════════════════════════════════════════

def crop_graph(image_path: str, output_path: str,
               text_end_y: int = None,
               bogi_start_y: int = None,
               padding_bottom: int = 40) -> tuple:
    """
    이미지에서 그래프 영역만 크롭하고 하단에 흰 여백을 추가합니다.

    핵심 로직:
    - 본문 텍스트 끝 자동 탐지 (연속 빈 행)
    - 보기 라벨 시작 자동 탐지 (dark > 80)
    - 크롭 하단 = 그래프 내 마지막 의미있는 픽셀 + padding_bottom
      (보기 라벨 직전까지 자르는 방식 사용 안 함 → 여백 1px 문제 해결)

    Returns
    -------
    (crop_w, crop_h, top_y, bottom_y)
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        raise ImportError("Pillow와 numpy가 필요합니다: pip install Pillow numpy")

    img = Image.open(image_path)
    w, h = img.size
    arr = np.array(img.convert('L'))
    dark = (arr < 200).sum(axis=1)

    # ── 본문 텍스트 끝 자동 탐지 ──
    if text_end_y is None:
        for y in range(50, h // 2):
            if all(dark[y:y+20] < 10):
                text_end_y = y
                break
        if text_end_y is None:
            text_end_y = 0

    # ── 보기 라벨/박스 시작 자동 탐지 ──
    if bogi_start_y is None:
        for y in range(text_end_y + 50, h):
            if dark[y] > 80:
                bogi_start_y = y
                break
        if bogi_start_y is None:
            bogi_start_y = h

    # ── 그래프 영역 내 마지막 의미있는 픽셀 탐지 ──
    # 보기 라벨(bogi_start_y) 이전까지만 탐색
    last_content_y = text_end_y
    for y in range(text_end_y, bogi_start_y):
        if dark[y] > 3:
            last_content_y = y

    # 크롭: 본문텍스트끝 ~ 마지막픽셀
    # 하단 여백은 padding_bottom만큼 흰색으로 추가
    # (보기라벨 위치와 무관하게 항상 padding_bottom 확보)
    crop_top    = text_end_y
    crop_bottom = last_content_y + 1   # 마지막 픽셀까지만 크롭

    graph = img.crop((0, crop_top, w, crop_bottom))
    gw, gh = graph.size

    # 항상 padding_bottom만큼 흰 여백 추가
    padded = Image.new('RGB', (gw, gh + padding_bottom), (255, 255, 255))
    padded.paste(graph, (0, 0))
    padded.save(output_path)
    final_size = padded.size

    print(f"  → 그래프 크롭: y={crop_top}~{crop_bottom} + 여백{padding_bottom}px "
          f"= {final_size[1]}px 총높이")
    return (final_size[0], final_size[1], crop_top, crop_bottom)


# ══════════════════════════════════════════════════════════
# 1. PDF 유형 판별
# ══════════════════════════════════════════════════════════

def is_scanned_pdf(pdf_path: str) -> bool:
    r = subprocess.run(
        ['pdftotext', '-f', '1', '-l', '1', pdf_path, '-'],
        capture_output=True, text=True
    )
    return len(r.stdout.strip()) < 20


# ══════════════════════════════════════════════════════════
# 2. 텍스트 추출
# ══════════════════════════════════════════════════════════

def extract_text_pdf(pdf_path: str) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join(p.extract_text() for p in pdf.pages if p.extract_text())
    except ImportError:
        r = subprocess.run(['pdftotext', '-layout', pdf_path, '-'],
                           capture_output=True, text=True)
        return r.stdout


def rasterize_pdf(pdf_path: str, dpi: int = 150) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, 'page')
        subprocess.run(['pdftoppm', '-jpeg', '-r', str(dpi), pdf_path, prefix],
                       check=True, capture_output=True)
        import glob
        pages = sorted(glob.glob(f'{tmp}/page-*.jpg'))
        result = []
        for i, p in enumerate(pages):
            dst = f'/tmp/hwpx_page_{i}.jpg'
            shutil.copy(p, dst)
            result.append(dst)
    return result


def vision_extract(image_paths: list) -> str:
    PROMPT = """이미지의 수학 문제를 정확히 인식하세요.
수식은 LaTeX 형식($수식$)으로, 문제 번호와 텍스트는 그대로 유지하세요.
그림/도형은 [그림]으로 표시하세요."""
    results = []
    for path in image_paths:
        with open(path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514", "max_tokens": 4000,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": PROMPT}
            ]}]
        }).encode()
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload, headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req) as resp:
            results.append(json.loads(resp.read())['content'][0]['text'])
    return "\n\n".join(results)


# ══════════════════════════════════════════════════════════
# 3. AI로 문제 구조화 (세그먼트 단위 — 인라인 수식 포함)
# ══════════════════════════════════════════════════════════

EXTRACT_PROMPT = """수학 문제 텍스트를 분석하여 JSON으로 반환하세요.

각 문제의 내용을 텍스트(text)와 수식(formula) 세그먼트로 분리하세요.

## 세그먼트 규칙
- 분수, 첨자, 루트, 기호가 포함된 수식 → type="formula", LaTeX 형식
- f(x), f(7), n(n+1) 같은 함수 표현 → type="formula"
- x, a, b, n 같은 수학 변수 (수학적으로 쓰인 것) → type="formula"
- 105, 2021 같이 수학적 의미의 숫자 → type="formula"
- 순수 한국어/조사/동사 → type="text"
- 독립된 수식(줄 전체가 수식)도 별도 세그먼트

## 박스 규칙
원본 문제에 테두리(박스)로 묶인 내용이 있으면 별도 problem으로 분리하고 "box": true를 표시.

box_type 결정:
- "보 기" 라벨이 있거나 ㄱ/ㄴ/ㄷ 항목이 박스 안에 있으면 → box_type: "bogi"
- 그 외 박스(㈎㈏㈐, 조건, 주어진 정보 등) → box_type: "condition"
- **박스 안 내용이 보기/조건이라는 명시적 표현이 없어도** 테두리로 묶여 있으면 박스로 처리

박스의 각 줄은 segments의 배열(2차원):
  "segments": [
    [{"type":"text","content":"ㄱ. "}, {"type":"formula","content":"f(x)>0"}],
    [{"type":"text","content":"ㄴ. "}, {"type":"formula","content":"g(x)<0"}]
  ]

## 이미지/그림
원본에 그래프나 그림이 있는 위치에는 다음을 삽입:
  {"type": "image_placeholder", "description": "그래프 설명"}

## JSON 형식
반드시 JSON만 반환 (마크다운 없이):
{
  "problems": [
    {
      "number": "1",
      "main": true,
      "segments": [
        {"type": "text", "content": "x에 대한 다항식 "},
        {"type": "formula", "content": "f(x)"},
        {"type": "text", "content": "가 "},
        {"type": "formula", "content": "(ax+b)(x+c)^2"},
        {"type": "text", "content": "으로 인수분해될 때, "},
        {"type": "formula", "content": "f(7)"},
        {"type": "text", "content": "의 값을 구하시오."}
      ]
    },
    {
      "number": "1-box",
      "box": true,
      "box_type": "bogi",
      "segments": [
        [{"type":"text","content":"ㄱ. "}, {"type":"formula","content":"f(1)>0"}],
        [{"type":"text","content":"ㄴ. "}, {"type":"formula","content":"f(2)<0"}]
      ]
    },
    {
      "number": "1-graph",
      "type": "image_placeholder",
      "description": "x축과 y축이 있는 함수 그래프"
    }
  ]
}
"""


def ai_extract_problems(raw_text: str) -> list:
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514", "max_tokens": 4000,
        "messages": [{"role": "user",
                      "content": EXTRACT_PROMPT + "\n\n입력:\n" + raw_text}]
    }).encode()
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=payload, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req) as resp:
        text = json.loads(resp.read())['content'][0]['text'].strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(text).get('problems', [])


# ══════════════════════════════════════════════════════════
# 4. LaTeX → 한컴 hwpEQ 변환
#
# 처리 순서 (핵심):
#   1. left/right, 텍스트/장식
#   2. 루트 \sqrt  (앞에 공백 추가 → 인접 기호와 분리)
#   3. 극한 \lim, 적분 \int/\oint, 합/곱 \sum/\prod  (구조 명령어)
#   4. 분수 \frac  (구조 명령어 처리 후)
#   5. 행렬/경우/조합
#   6. 단순 기호 치환 (\pm, \times, \alpha 등)
#      ※ \\[a-zA-Z]+ 제거는 여기서 맨 마지막에
#   7. 다자리 지수/아래첨자 → 중괄호 자동 추가
# ══════════════════════════════════════════════════════════

def latex_to_hwpeq(latex: str) -> str:
    """LaTeX → 한컴 수식 편집기 코드(hwpEQ) 변환"""
    s = latex.strip().strip('$').strip()

    # ── 1. left/right 크기 조절 괄호 ──
    s = re.sub(r'\\left\s*\(',   ' left ( ', s)
    s = re.sub(r'\\right\s*\)',  ' right )', s)
    s = re.sub(r'\\left\s*\[',   ' left [ ', s)
    s = re.sub(r'\\right\s*\]',  ' right ]', s)
    s = re.sub(r'\\left\s*\\{',  ' left { ', s)
    s = re.sub(r'\\right\s*\\}', ' right }', s)
    s = re.sub(r'\\left\s*\|',   ' left | ', s)
    s = re.sub(r'\\right\s*\|',  ' right |', s)

    # ── 절댓값 |...| → left | ... right | ──
    # 한컴 수식에서 || 안의 글씨체 보호 + 크기 자동 조절
    # 단, 이미 left | 로 처리된 것은 제외
    s = re.sub(r'(?<!left )\|([^|]+)\|', r' left | \1 right |', s)

    # ── 텍스트 삽입 ──
    s = re.sub(r'\\(?:text|mathrm|mbox)\{([^}]*)\}', r'"\1"', s)

    # ── 글자 장식 ──
    for pat, rep in [
        (r'\\overline\{([^}]+)\}', r'bar {\1}'),
        (r'\\vec\{([^}]+)\}',      r'vec \1'),
        (r'\\hat\{([^}]+)\}',      r'hat \1'),
        (r'\\tilde\{([^}]+)\}',    r'tilde \1'),
        (r'\\dot\{([^}]+)\}',      r'dot \1'),
        (r'\\ddot\{([^}]+)\}',     r'ddot \1'),
        (r'\\bar\{([^}]+)\}',      r'bar \1'),
        (r'\\acute\{([^}]+)\}',    r'acute \1'),
        (r'\\check\{([^}]+)\}',    r'check \1'),
    ]:
        s = re.sub(pat, rep, s)

    # ── 2. 루트 — 앞에 공백을 넣어 인접 기호(\pm 등)와 분리 ──
    # 예: \pm\sqrt{x} → \pm sqrt {x} (공백 있음 → \pm\b 정상 매칭)
    # 중첩 \sqrt (이중근호 등)을 위해 balanced-brace 매처로 임의 깊이 처리.
    # (단순 regex 1회만 돌리면 안쪽 \sqrt 가 그대로 남아 step 8 catch-all
    #  `\\[a-zA-Z]+` 에 잡혀 통째로 사라지는 버그 발생:
    #  예) `\sqrt{(4\sqrt{3})^2+2^2}` → `sqrt {(4{3})^{2}+2^{2}}` 처럼
    #      안쪽 √3 가 소멸.)
    def _convert_sqrt_balanced(src: str) -> str:
        i, out = 0, []
        n = len(src)
        while i < n:
            mn = re.match(r'\\sqrt\[([^\]]+)\]\{', src[i:])
            m1 = re.match(r'\\sqrt\{', src[i:])
            m = mn or m1
            if m:
                idx_arg = mn.group(1) if mn else None
                start = i + m.end()
                depth, j = 1, start
                while j < n and depth > 0:
                    if src[j] == '{':
                        depth += 1
                    elif src[j] == '}':
                        depth -= 1
                    j += 1
                content = src[start:j-1]
                content = _convert_sqrt_balanced(content)  # 재귀 내부 처리
                if idx_arg is not None:
                    out.append(f' sqrt [{idx_arg}] {{{content}}}')
                else:
                    out.append(f' sqrt {{{content}}}')
                i = j
            else:
                out.append(src[i])
                i += 1
        return ''.join(out)

    s = _convert_sqrt_balanced(s)

    # ── 3. 극한 구조 ──
    # 중괄호 포함 패턴 (x→-1/2 같이 frac이 있는 경우도 처리)
    s = re.sub(r'\\lim_\{((?:[^{}]|\{[^{}]*\})+?)\\to\s*((?:[^{}]|\{[^{}]*\})+?)\}',
               r'lim _{\1 rarrow \2}', s)
    s = re.sub(r'\\lim_\{((?:[^{}]|\{[^{}]*\})+)\}', r'lim _{\1}', s)
    s = re.sub(r'\\lim\b', 'lim ', s)

    # ── 4. 적분 구조 ──
    s = re.sub(r'\\int_\{([^{}]+)\}\^\{([^{}]+)\}', r' INT _{\1}^{\2}', s)  # _{...}^{...}
    s = re.sub(r'\\int_([^{^\s,]+)\^\{([^{}]+)\}',  r' INT _{\1}^{\2}', s)  # _x^{...}
    s = re.sub(r'\\int_([^{^\s,]+)\^([^{^\s,]+)',    r' INT _{\1}^{\2}', s)  # _x^y
    s = re.sub(r'\\oint\b', ' OINT ', s)
    s = re.sub(r'\\int\b',  ' INT ',  s)

    # ── 5. 합/곱 구조 ──
    s = re.sub(r'\\sum_\{([^{}]+)\}\^\{([^{}]+)\}',  r' SUM _{\1}^{\2}', s)
    s = re.sub(r'\\sum_([^{^\s,]+)\^\{([^{}]+)\}',   r' SUM _{\1}^{\2}', s)  # _x^{...}
    s = re.sub(r'\\sum_([^{^\s,]+)\^([^{^\s,]+)',     r' SUM _{\1}^{\2}', s)
    s = re.sub(r'\\sum\b',  ' SUM ',  s)
    s = re.sub(r'\\prod_\{([^{}]+)\}\^\{([^{}]+)\}', r' PROD _{\1}^{\2}', s)
    s = re.sub(r'\\prod_([^{^\s,]+)\^\{([^{}]+)\}',  r' PROD _{\1}^{\2}', s)
    s = re.sub(r'\\prod\b', ' PROD ', s)

    # ── 6. 분수 — 구조 명령어 모두 처리된 후 ──
    for _ in range(4):
        s = re.sub(
            r'\\[df]?frac\{((?:[^{}]|\{[^{}]*\})*)\}\{((?:[^{}]|\{[^{}]*\})*)\}',
            r'{\1} over {\2}', s
        )

    # ── 7. 행렬 / 경우 / 조합 ──
    def _matrix(m):
        kind = m.group(1).lower()
        inner = re.sub(r'\\\\', ' # ', m.group(2)).replace('&', ' &')
        cmd = {'pmatrix': 'PMATRIX', 'bmatrix': 'BMATRIX',
               'vmatrix': 'DMATRIX'}.get(kind, 'MATRIX')
        return f'{cmd} {{{inner}}}'
    s = re.sub(r'\\begin\{(p?b?v?matrix)\}(.*?)\\end\{\1\}',
               _matrix, s, flags=re.DOTALL)
    s = re.sub(
        r'\\begin\{cases\}(.*?)\\end\{cases\}',
        lambda m: 'CASES {' + re.sub(r'\\\\', ' # ', m.group(1)) + '}',
        s, flags=re.DOTALL
    )
    s = re.sub(r'\\binom\{([^{}]+)\}\{([^{}]+)\}', r'\1 CHOOSE \2', s)
    s = re.sub(r'\\dbinom\{([^{}]+)\}\{([^{}]+)\}', r'\1 BINOM \2', s)

    # ── 8. 단순 기호 치환 ──
    simple = [
        # 화살표
        (r'\\Leftrightarrow\b', 'LRARROW'), (r'\\Rightarrow\b',  'RARROW'),
        (r'\\Leftarrow\b',      'LARROW'),  (r'\\leftrightarrow\b','lrarrow'),
        (r'\\rightarrow\b',     'rarrow'),  (r'\\leftarrow\b',   'larrow'),
        (r'\\to\b',             'rarrow'),  (r'\\mapsto\b',      'mapsto'),
        (r'\\uparrow\b',        'uparrow'), (r'\\downarrow\b',   'downarrow'),
        # 연산자 (앞뒤 공백으로 인접 문자 분리)
        (r'\\times\b',  ' TIMES '),  (r'\\div\b',    ' DIV '),
        (r'\\pm\b',     ' PLUSMINUS '), (r'\\mp\b',  ' MINUSPLUS '),
        (r'\\cdot\b',   ' cdot '),   (r'\\circ\b',   ' CIRC '),
        (r'\\bullet\b', ' BULLET '),
        # 부등호 (뒤에 공백 — 한컴 LEQ/GEQ 파싱 규칙)
        (r'\\geqq?\b',  ' GEQ '),    (r'\\ge\b',     ' GEQ '),
        (r'\\leqq?\b',  ' LEQ '),    (r'\\le\b',     ' LEQ '),
        (r'\\neq\b',    ' neq '),    (r'\\ne\b',     ' neq '),
        (r'\\approx\b', ' APPROX '), (r'\\equiv\b',  ' EQUIV '),
        (r'\\sim\b',    ' SIM '),    (r'\\simeq\b',  ' SIMEQ '),
        (r'\\cong\b',   ' CONG '),   (r'\\propto\b', ' PROPTO '),
        (r'\\asymp\b',  ' ASYMP '),
        # 집합/논리
        (r'\\forall\b',    'FORALL '),  (r'\\exists\b',   'EXIST '),
        (r'\\in\b',        ' IN '),     (r'\\notin\b',    ' notin '),
        (r'\\subset\b',    ' SUBSET '), (r'\\supset\b',   ' SUPSET '),
        (r'\\subseteq\b',  ' SUBSETEQ '), (r'\\supseteq\b', ' SUPSETEQ '),
        (r'\\cup\b',       ' UNION '),  (r'\\cap\b',      ' INTER '),
        (r'\\emptyset\b',  'EMPTYSET'), (r'\\varnothing\b','EMPTYSET'),
        (r'\\therefore\b', 'THEREFORE'),(r'\\because\b',  'BECAUSE'),
        # 기타 기호
        (r'\\infty\b',   'inf'),      (r'\\partial\b', 'PARTIAL'),
        (r'\\nabla\b',   'nabla'),    (r'\\prime\b',   'prime'),
        (r'\\angle\b',   'ANGLE'),    (r'\\triangle\b','TRIANGLE'),
        (r'\\perp\b',    'BOT'),
        (r'\\ldots\b',   'LDOTS'),    (r'\\cdots\b',   'cdots'),
        (r'\\vdots\b',   'VDOTS'),    (r'\\ddots\b',   'DDOTS'),
        # 그리스 소문자 (뒤에 공백으로 ^, _ 와 분리)
        (r'\\alpha\b',   ' alpha '),   (r'\\beta\b',    ' beta '),
        (r'\\gamma\b',   ' gamma '),   (r'\\delta\b',   ' delta '),
        (r'\\epsilon\b', ' epsilon '), (r'\\varepsilon\b',' epsilon '),
        (r'\\zeta\b',    ' zeta '),    (r'\\eta\b',     ' eta '),
        (r'\\theta\b',   ' theta '),   (r'\\vartheta\b',' vartheta '),
        (r'\\iota\b',    ' iota '),    (r'\\kappa\b',   ' kappa '),
        (r'\\lambda\b',  ' lambda '),  (r'\\mu\b',      ' mu '),
        (r'\\nu\b',      ' nu '),      (r'\\xi\b',      ' xi '),
        (r'\\pi\b',      ' pi '),      (r'\\varpi\b',   ' varpi '),
        (r'\\rho\b',     ' rho '),     (r'\\varrho\b',  ' rho '),
        (r'\\sigma\b',   ' sigma '),   (r'\\varsigma\b',' varsigma '),
        (r'\\tau\b',     ' tau '),     (r'\\upsilon\b', ' upsilon '),
        (r'\\phi\b',     ' phi '),     (r'\\varphi\b',  ' phi '),
        (r'\\chi\b',     ' chi '),     (r'\\psi\b',     ' psi '),
        (r'\\omega\b',   ' omega '),
        # 그리스 대문자
        (r'\\Gamma\b',   ' Gamma '),   (r'\\Delta\b',   ' Delta '),
        (r'\\Theta\b',   ' Theta '),   (r'\\Lambda\b',  ' Lambda '),
        (r'\\Xi\b',      ' Xi '),      (r'\\Pi\b',      ' Pi '),
        (r'\\Sigma\b',   ' Sigma '),   (r'\\Phi\b',     ' Phi '),
        (r'\\Psi\b',     ' Psi '),     (r'\\Omega\b',   ' Omega '),
        # 삼각/지수/로그 함수
        (r'\\sin\b',' sin '),  (r'\\cos\b',' cos '),  (r'\\tan\b',' tan '),
        (r'\\cot\b',' cot '),  (r'\\sec\b',' sec '),  (r'\\csc\b',' cosec '),
        (r'\\arcsin\b',' arcsin '),(r'\\arccos\b',' arccos '),(r'\\arctan\b',' arctan '),
        (r'\\sinh\b',' sinh '), (r'\\cosh\b',' cosh '), (r'\\tanh\b',' tanh '),
        (r'\\coth\b',' coth '),
        # \log_N (밑수 있는 로그) — \log\b 보다 먼저 처리
        (r'\\log_\{([^}]+)\}', r' log _{\1} '),
        (r'\\log_([0-9a-zA-Z])', r' log _{\1} '),
        (r'\\log\b',' log '),  (r'\\ln\b',' ln '),   (r'\\lg\b',' lg '),
        (r'\\exp\b',' exp '),  (r'\\Exp\b',' Exp '),
        (r'\\max\b',' max '),  (r'\\min\b',' min '),
        (r'\\sup\b',' sup '),  (r'\\inf\b',' inf '),
        (r'\\det\b',' det '),  (r'\\gcd\b',' gcd '),
        (r'\\mod\b',' mod '),  (r'\\deg\b',' deg '),
        (r'\\lim\b',' lim '),  (r'\\ker\b',' ker '),
        # 괄호 이스케이프
        (r'\\\{', ' left { '), (r'\\\}', ' right }'),  # 보이는 중괄호
        # 공백 (한컴: ~ = 빈칸)
        (r'\\,', '~'), (r'\\;', '~'), (r'\\:', '~'), (r'\\!', ''),
        (r'\\quad\b',  ' & '), (r'\\qquad\b', ' & & '),
        # rm/it/bold/rmbold — 한컴 글꼴 명령어로 변환
        (r'\\rm\b',      'rm '),
        (r'\\it\b',      'it '),
        (r'\\bold\b',    'bold '),
        (r'\\rmbold\b',  'rmbold '),
        # 나머지 LaTeX 명령어 제거 — 반드시 맨 마지막에
        (r'\\[a-zA-Z]+', ''),
    ]
    for pat, rep in simple:
        s = re.sub(pat, rep, s)

    # ── 9. 지수/아래첨자 → 모두 중괄호로 묶기 ──
    # 원본 파일 패턴: x ^{2}, a ^{3}, f_{1}(x) — 항상 중괄호 사용
    # 한컴이 _1( 를 _{1(} 로 파싱하는 오류 방지
    # 이미 ^{...}, _{...} 있으면 건드리지 않음
    s = re.sub(r'\^(?!\{)([0-9a-zA-Z])', r'^{\1}', s)
    s = re.sub(r'_(?!\{)([0-9a-zA-Z])', r'_{\1}', s)
    # 다자리도 처리 (위에서 단일 처리 후 나머지)
    s = re.sub(
        r'\^(?!\{)([a-zA-Z][0-9]|[0-9][a-zA-Z]|[a-zA-Z]{2,}|[0-9]{2,})',
        r'^{\1}', s
    )
    s = re.sub(
        r'_(?!\{)([a-zA-Z][0-9]|[0-9][a-zA-Z]|[a-zA-Z]{2,}|[0-9]{2,})',
        r'_{\1}', s
    )

    # ── 지수 뒤 공백 추가 (한컴 파싱 오류 방지) ──
    # 원본 패턴: "a^2 +b^2", "x^4 +x^3 -9x^2", "(a+b)^2 -2ab"
    # 규칙1: ^단일 뒤에 ), ], }, 공백 제외하고 뭔가 오면 공백 추가
    # 규칙2: ^{...} 뒤에 +,-,= 앞 공백
    # 규칙3: 닫힌 괄호 ), ] 뒤에 =,+,- 가 오면 공백 (한컴 파싱 오류 방지)
    #        예: (x^2-3y^2)=x → (x^2 -3y^2) =x
    s = re.sub(r'(\^[0-9a-zA-Z])(?=[^)\]}\'\s^_])', r'\1 ', s)
    s = re.sub(r'(\^\{[^{}]+\})(?=[+\-=])', r'\1 ', s)
    s = re.sub(r'([)\]])([=+\-])', r'\1 \2', s)

    # ── 중복 공백 정리 ──
    s = re.sub(r'  +', ' ', s).strip()
    return s


# ══════════════════════════════════════════════════════════
# 5. 수식 크기 추정
# ══════════════════════════════════════════════════════════

def estimate_eq_size(hwpeq: str) -> tuple:
    """(width, height, baseLine) 추정 — hwpUnit 단위"""
    has_frac   = 'over' in hwpeq
    has_sqrt   = 'sqrt' in hwpeq
    has_int    = any(x in hwpeq for x in ['INT', 'OINT'])
    has_sum    = any(x in hwpeq for x in ['SUM', 'PROD'])
    has_matrix = any(x in hwpeq for x in ['MATRIX', 'PMATRIX', 'BMATRIX', 'DMATRIX'])
    has_cases  = 'CASES' in hwpeq
    length     = len(hwpeq)

    if has_matrix or has_cases:
        return (12000, 3000, 87)
    elif has_int or has_sum:
        h = 2500 if (has_frac or has_sqrt) else 2000
        return (10000, h, 87)
    elif has_frac or has_sqrt:
        return (8000, 1800, 87)
    elif length <= 5:
        return (max(1500, length * 400), 1100, 85)
    elif length <= 20:
        return (5000, 1100, 85)
    else:
        return (min(10000, length * 220), 1100, 85)


# ══════════════════════════════════════════════════════════
# 6. HWPX section0.xml 생성
# ══════════════════════════════════════════════════════════

_SECPR = (
    '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" '
    'tabStop="8000" tabStopVal="4000" tabStopUnit="HWPUNIT" '
    'outlineShapeIDRef="1" memoShapeIDRef="0" '
    'textVerticalWidthHead="0" masterPageCnt="0">'
    '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
    '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
    '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" '
    'hideFirstMasterPage="0" border="SHOW_ALL" fill="SHOW_ALL" '
    'hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
    '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
    '<hp:pagePr landscape="NARROWLY" width="59528" height="84188" gutterType="LEFT_ONLY">'
    '<hp:margin header="4252" footer="4252" gutter="0" '
    'left="8504" right="8504" top="5668" bottom="4252"/>'
    '</hp:pagePr>'
    '<hp:footNotePr>'
    '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
    '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
    '<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
    '<hp:numbering type="CONTINUOUS" newNum="1"/>'
    '<hp:placement place="EACH_COLUMN" beneathText="0"/>'
    '</hp:footNotePr>'
    '<hp:endNotePr>'
    '<hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
    '<hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/>'
    '<hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>'
    '<hp:numbering type="CONTINUOUS" newNum="1"/>'
    '<hp:placement place="END_OF_DOCUMENT" beneathText="0"/>'
    '</hp:endNotePr>'
    '<hp:pageBorderFill type="BOTH" borderFillIDRef="1" textBorder="PAPER" '
    'headerInside="0" footerInside="0" fillArea="PAPER">'
    '<hp:offset left="1417" right="1417" top="1417" bottom="1417"/>'
    '</hp:pageBorderFill>'
    '</hp:secPr>'
    '<hp:ctrl>'
    '<hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/>'
    '</hp:ctrl>'
)


def _xt(t: str) -> str:
    """hp:t 안 텍스트 XML 이스케이프"""
    return t.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')


def _xs(s: str) -> str:
    """hp:script 안 hwpEQ 코드 XML 이스케이프
    - & → &amp;  (필수)
    - < → &lt;   (XML 태그 오인 방지 필수!)
    - > 는 그대로 (rarrow 등 hwpEQ 코드에 사용)
    """
    return s.replace('&', '&amp;').replace('<', '&lt;')


def _eq_block(eid: int, hwpeq: str) -> str:
    """hp:equation XML 블록 생성"""
    w, h, bl = estimate_eq_size(hwpeq)
    return (
        f'<hp:equation id="{eid}" zOrder="{eid}" numberingType="EQUATION" '
        f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
        f'version="Equation Version 60" baseLine="{bl}" textColor="#000000" '
        f'baseUnit="1100" lineMode="CHAR" font="HYhwpEQ">'
        f'<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" '
        f'heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
        f'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="56" right="56" top="0" bottom="0"/>'
        f'<hp:shapeComment>수식입니다.</hp:shapeComment>'
        f'<hp:script>{_xs(hwpeq)}</hp:script>'
        f'</hp:equation>'
    )


def _lineseg(h: int = 1100) -> str:
    bl = int(h * 0.85)
    return (
        f'<hp:linesegarray>'
        f'<hp:lineseg textpos="0" vertpos="0" vertsize="{h}" textheight="{h}" '
        f'baseline="{bl}" spacing="600" horzpos="0" horzsize="42520" flags="393216"/>'
        f'</hp:linesegarray>'
    )


def _para_from_segments(segments: list, eq_id: int) -> tuple:
    """
    segments 리스트로 문단 XML 생성.
    반환: (xml, 사용된 eq_id 수, 최대 수식 높이)
    """
    eid = eq_id
    parts = []
    max_h = 1100

    for seg in segments:
        if seg['type'] == 'text':
            txt = _xt(seg['content'])
            if txt:
                parts.append(f'<hp:t>{txt}</hp:t>')
        else:
            # formula: content가 LaTeX면 변환, 아니면 그대로
            raw = seg['content']
            hwpeq = latex_to_hwpeq(raw)
            _, h, _ = estimate_eq_size(hwpeq)
            max_h = max(max_h, h)
            parts.append(_eq_block(eid, hwpeq))
            eid += 1

    xml = (
        f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">{"".join(parts)}</hp:run>'
        f'{_lineseg(max_h)}'
        f'</hp:p>'
    )
    return xml, eid - eq_id, max_h



# ══════════════════════════════════════════════════════════
# 6-1. 박스(테두리 있는 1x1 표) 생성
#      보기 박스, 조건 박스 등에 사용
# ══════════════════════════════════════════════════════════

def _cell(bf_id, col_addr, row_addr, col_span, row_span,
           cell_w, cell_h, inner_para, margin="141"):
    """hp:tc (표 셀) XML 생성"""
    return (
        f'<hp:tc name="" header="0" hasMargin="1" protect="0" editable="0" dirty="0" borderFillIDRef="{bf_id}">'
        f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        f'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
        f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        + inner_para +
        f'</hp:subList>'
        f'<hp:cellAddr colAddr="{col_addr}" rowAddr="{row_addr}"/>'
        f'<hp:cellSpan colSpan="{col_span}" rowSpan="{row_span}"/>'
        f'<hp:cellSz width="{cell_w}" height="{cell_h}"/>'
        f'<hp:cellMargin left="{margin}" right="{margin}" top="{margin}" bottom="{margin}"/>'
        f'</hp:tc>'
    )


def _empty_para(horzsize=1000):
    return (
        f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0"/>'
        f'<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" '
        f'textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="{horzsize}" flags="393216"/>'
        f'</hp:linesegarray></hp:p>'
    )


# ──────────────────────────────────────────────────────────
# rect 기반 박스 — 참고 hwpx 의 도형 박스를 그대로 복사해 내용만 교체
# ──────────────────────────────────────────────────────────

try:
    from box_templates import CONDITION_BOX_RECT, BOGI_BOX_RECT
    _RECT_TEMPLATES_OK = True
except Exception:
    _RECT_TEMPLATES_OK = False


def _fill_template(template: str, **kwargs) -> str:
    """템플릿의 {KEY} 자리표시자들을 str.replace 로 치환.
    str.format 을 쓰지 않는 이유: 내용 paragraphs 가 hwpEQ 의 {} 를 포함해
    이중 escape 가 번거롭기 때문."""
    result = template
    for k, v in kwargs.items():
        result = result.replace('{' + k + '}', str(v))
    return result


def _box_inner_para(segments: list, eq_id: int,
                    horzsize: int = 40012, vertpos: int = 0) -> tuple:
    """박스 안 subList 의 문단 1개 생성 (id=2147483648, sub-list 관용)."""
    eid = eq_id
    parts = []
    max_h = 1000
    for seg in segments:
        if seg.get('type') == 'text':
            txt = _xt(seg['content'])
            if txt:
                parts.append(f'<hp:t>{txt}</hp:t>')
        else:
            hwpeq = latex_to_hwpeq(seg['content'])
            _, h, _ = estimate_eq_size(hwpeq)
            max_h = max(max_h, h)
            parts.append(_eq_block(eid, hwpeq))
            eid += 1
    bl = int(max_h * 0.85)
    xml = (
        f'<hp:p id="2147483648" paraPrIDRef="0" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">{"".join(parts)}</hp:run>'
        f'<hp:linesegarray><hp:lineseg textpos="0" vertpos="{vertpos}" '
        f'vertsize="{max_h}" textheight="{max_h}" baseline="{bl}" spacing="600" '
        f'horzpos="0" horzsize="{horzsize}" flags="393216"/></hp:linesegarray>'
        f'</hp:p>'
    )
    return xml, eid - eq_id


def _substitute_outer_sublist(rect_xml: str, paragraphs_xml: str) -> str:
    """rect 의 OUTERMOST <hp:subList>...</hp:subList> 내부를 새 paragraphs 로 교체.
    nested subList 있는 rect (보기 외곽) 에는 호출하지 말 것 — 라벨이 사라짐."""
    m = re.search(r'<hp:subList\b[^>]*>', rect_xml)
    if not m:
        return rect_xml
    sl_start = m.end()
    sl_end = rect_xml.rfind('</hp:subList>')  # outermost (가장 늦게 닫힘)
    if sl_end < sl_start:
        return rect_xml
    return rect_xml[:sl_start] + paragraphs_xml + rect_xml[sl_end:]


def make_rect_box(rows_segments: list, eq_id_start: int,
                  box_type: str = "condition") -> tuple:
    """rect (도형) 기반 박스 생성 — 참고 hwpx 와 동일한 모양.
    box_type: 'condition' = 단순 사각형 / 'bogi' = [보 기] 라벨이 위에 달린 박스.
    rows_segments: 2차원 [[seg1, seg2, ...], ...]
    반환: (paragraph_xml, used_eq_id)
    """
    eid = eq_id_start
    inner_horz = 39212 if box_type == "condition" else 40012
    paras = []
    vp = 0
    for row in rows_segments:
        p, used = _box_inner_para(row, eid, horzsize=inner_horz, vertpos=vp)
        eid += used
        paras.append(p)
        vp += 1600
    content_xml = "".join(paras)

    # ID 충돌 회피용 베이스 (rect 마다 3개씩 사용)
    base = 1100000000 + eid * 13
    inst = 26000000 + eid * 13
    zo = eid * 13

    if box_type == "condition":
        rect = _fill_template(
            CONDITION_BOX_RECT,
            ID_0_RECT=base, ID_0_INST=inst, ID_0_Z=zo,
            COND_CONTENT=content_xml,
        )
        eid += 1
    else:  # bogi
        # bogi 는 1 outer + 2 nested (label, content) = 3 개 rect, 각각 3 IDs
        rect = _fill_template(
            BOGI_BOX_RECT,
            ID_0_RECT=base,     ID_0_INST=inst,     ID_0_Z=zo,
            ID_1_RECT=base + 1, ID_1_INST=inst + 1, ID_1_Z=zo + 1,
            ID_2_RECT=base + 2, ID_2_INST=inst + 2, ID_2_Z=zo + 2,
            BOGI_CONTENT=content_xml,
        )
        eid += 3

    # rect 를 담는 paragraph
    p_xml = (
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" '
        'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">{rect}<hp:t/></hp:run>'
        '<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" '
        'textheight="1000" baseline="850" spacing="600" horzpos="0" '
        'horzsize="42520" flags="393216"/></hp:linesegarray>'
        '</hp:p>'
    )
    return p_xml, eid - eq_id_start


def make_box_xml(rows_segments: list, eq_id_start: int,
                 width: int = 42520, box_type: str = "condition") -> tuple:
    """
    박스 생성. box_type 에 따라 두 가지 스타일:
      "condition" : 단순 실선 테두리 박스 (조건 박스)
      "bogi"      : 상단 중앙에 [보 기] 라벨이 있는 박스

    rows_segments: 각 줄의 segments 리스트 [[row1_segs], [row2_segs], ...]
    반환: (hp:p 전체 xml, 사용된 eq_id 수)

    rect 템플릿이 로드돼 있으면 도형 기반 박스 (참고 hwpx 와 동일 모양) 로
    출력. 아니면 기존 표(table) 기반으로 fallback.
    """
    if _RECT_TEMPLATES_OK:
        return make_rect_box(rows_segments, eq_id_start, box_type=box_type)
    # ── fallback: 표 기반 ──
    eid = eq_id_start
    para_xmls = []
    for row_segs in rows_segments:
        para_xml, used, _ = _para_from_segments(row_segs, eid)
        eid += used
        para_xmls.append(para_xml)

    tbl_id = eid + 9000

    if box_type == "condition":
        # ── 단순 실선 박스 (1행 1열) ──
        inner = "".join(para_xmls)
        tbl_xml = (
            f'<hp:tbl id="{tbl_id}" zOrder="{tbl_id+1}" numberingType="TABLE" '
            f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
            f'pageBreak="CELL" repeatHeader="1" rowCnt="1" colCnt="1" '
            f'cellSpacing="0" borderFillIDRef="15" noAdjust="0">'
            f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="1000" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
            f'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:inMargin left="0" right="0" top="850" bottom="850"/>'
            f'<hp:tr>'
            f'<hp:tc name="" header="0" hasMargin="1" protect="0" editable="0" dirty="0" borderFillIDRef="15">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
            f'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
            f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            + inner +
            f'</hp:subList>'
            f'<hp:cellAddr colAddr="0" rowAddr="0"/>'
            f'<hp:cellSpan colSpan="1" rowSpan="1"/>'
            f'<hp:cellSz width="{width}" height="282"/>'
            f'<hp:cellMargin left="850" right="850" top="283" bottom="283"/>'
            f'</hp:tc></hp:tr></hp:tbl>'
        )

        full_p = (
            f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">'
            + tbl_xml +
            f'<hp:t/></hp:run>'
            f'<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="1100" textheight="1100" '
            f'baseline="935" spacing="600" horzpos="0" horzsize="{width}" flags="393216"/>'
            f'</hp:linesegarray>'
            f'</hp:p>'
        )
        return full_p, eid - eq_id_start

    else:  # box_type == "bogi"
        # ── 보기 박스: 3행 3열 표 (라벨 문단 없음) ──
        #
        # 수정사항:
        # 1. 라벨 문단 제거 (표 안에만 [보  기] 표시)
        # 2. 행1/행2 중앙 병합 셀 너비 축소 (보기 라벨 글자 폭만큼)
        # 3. 행3 상단선 제거 (bf=23: 좌+우+하단만)
        #
        # 열0(자동) | 열1(라벨용 좁게) | 열2(자동)
        # 행1: [bf=17 NONE] | [bf=17 "[보  기]" rowspan=2] | [bf=17 NONE]
        # 행2: [bf=11 ↑←]  | (rowspan)                    | [bf=13 ↑→]
        # 행3: [bf=23 colspan=3 내용 3mm여백]  ← 상단선 없음

        content_inner = "".join(para_xmls)
        M = 850    # 3mm 여백
        # 라벨 셀 너비: "[ 보  기 ]" 글자 폭 (약 4000 hwpUnit)
        c1 = 4500  # 중앙 라벨 셀 (좁게)
        c0 = (width - c1) // 2   # 좌 잔여
        c2 = width - c1 - c0     # 우 잔여
        # 행 높이
        h1 = 500   # 보기 라벨 행
        h2 = 300   # 모서리 행

        def _sub(inner):
            return (
                f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
                f'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
                f'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                + inner +
                f'</hp:subList>'
            )

        def _tc(bf, ca, ra, cs, rs, w, h, sub, hm="0", ml=141, mr=141, mt=141, mb=141):
            return (
                f'<hp:tc name="" header="0" hasMargin="{hm}" protect="0" editable="0" dirty="0" borderFillIDRef="{bf}">'
                + sub +
                f'<hp:cellAddr colAddr="{ca}" rowAddr="{ra}"/>'
                f'<hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/>'
                f'<hp:cellSz width="{w}" height="{h}"/>'
                f'<hp:cellMargin left="{ml}" right="{mr}" top="{mt}" bottom="{mb}"/>'
                f'</hp:tc>'
            )

        def _ep(w):
            return (
                f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                f'<hp:run charPrIDRef="0"/>'
                f'<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" '
                f'textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="{w}" flags="393216"/>'
                f'</hp:linesegarray></hp:p>'
            )

        # [보  기] 라벨 문단 (행1 중앙 셀용)
        bogi_para = (
            f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0"><hp:t>[ 보 기 ]</hp:t></hp:run>'
            f'<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" '
            f'textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="{c1}" flags="393216"/>'
            f'</hp:linesegarray></hp:p>'
        )

        # 행1: 좌(bf=17) | 중앙 rowspan=2(bf=17 "[보  기]") | 우(bf=17)
        row1 = (
            f'<hp:tr>'
            + _tc(17, 0, 0, 1, 1, c0, h1, _sub(_ep(c0)))
            + _tc(17, 1, 0, 1, 2, c1, h1+h2, _sub(bogi_para))  # rowspan=2
            + _tc(17, 2, 0, 1, 1, c2, h1, _sub(_ep(c2)))
            + f'</hp:tr>'
        )
        # 행2: 좌상모서리(bf=11) | (rowspan 계속) | 우상모서리(bf=13)
        row2 = (
            f'<hp:tr>'
            + _tc(11, 0, 1, 1, 1, c0, h2, _sub(_ep(c0)))
            + _tc(13, 2, 1, 1, 1, c2, h2, _sub(_ep(c2)))
            + f'</hp:tr>'
        )
        # 행3: 전체 colspan=3, bf=23 (좌+우+하단, 상단없음), 내용, 3mm 여백
        row3 = (
            f'<hp:tr>'
            + _tc(23, 0, 2, 3, 1, width, 1000, _sub(content_inner),
                  hm="1", ml=M, mr=M, mt=M, mb=M)
            + f'</hp:tr>'
        )

        # 표 (라벨 문단 없음, 표 자체가 완결)
        tbl_xml = (
            f'<hp:tbl id="{tbl_id}" zOrder="{tbl_id+1}" numberingType="TABLE" '
            f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
            f'pageBreak="NONE" repeatHeader="1" rowCnt="3" colCnt="3" '
            f'cellSpacing="0" borderFillIDRef="15" noAdjust="0">'
            f'<hp:sz width="{width}" widthRelTo="ABSOLUTE" height="1000" heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
            f'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
            f'<hp:inMargin left="141" right="141" top="141" bottom="141"/>'
            + row1 + row2 + row3 +
            f'</hp:tbl>'
        )

        full_p = (
            f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0">'
            + tbl_xml +
            f'<hp:t/></hp:run>'
            f'<hp:linesegarray>'
            f'<hp:lineseg textpos="0" vertpos="0" vertsize="1100" textheight="1100" '
            f'baseline="935" spacing="600" horzpos="0" horzsize="{width}" flags="393216"/>'
            f'</hp:linesegarray>'
            f'</hp:p>'
        )
        return full_p, eid - eq_id_start


# ══════════════════════════════════════════════════════════
# 6-2. 미주(endnote) 생성
# ══════════════════════════════════════════════════════════

def _solution_base(prob: dict) -> str:
    """problem 의 number 앞 숫자만 추출 (미주 매칭용). 예: '3-①' → '3'."""
    m = re.match(r"^\s*(\d+)", str(prob.get("number", "")))
    return m.group(1) if m else ""


def _endnote_subpara(segments: list, eq_id: int, autonum_n=None) -> tuple:
    """
    미주 subList 안의 문단 1개 생성.
    autonum_n 이 주어지면 그 문단 맨 앞에 미주 번호(autoNum) 마커를 넣는다.
    문단/run 속성은 템플릿의 '작동하는' 미주와 동일하게 맞춘다
    (id=2147483648 = 미주 sub-list 전용, paraPrIDRef/styleIDRef/charPrIDRef 도 동일).
    반환: (xml, 사용된 eq_id 수)
    """
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
        if seg.get('type') == 'text':
            txt = _xt(seg['content'])
            if txt:
                parts.append(f'<hp:t>{txt}</hp:t>')
        else:
            hwpeq = latex_to_hwpeq(seg['content'])
            _, h, _ = estimate_eq_size(hwpeq)
            max_h = max(max_h, h)
            parts.append(_eq_block(eid, hwpeq))
            eid += 1
    parts.append('<hp:t/>')  # 템플릿과 동일하게 run 끝 빈 텍스트
    # 첫 문단(autoNum 포함)과 이후 문단의 스타일 ref 를 템플릿과 동일하게
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
        f'</hp:p>'
    )
    return xml, eid - eq_id


def make_endnote_xml(sol_entries: list, eq_id: int, num: int, inst_id: int) -> tuple:
    """
    풀이 엔트리 리스트 → <hp:endNote> XML.
    본문에 인라인 삽입되는 마커 + 문서 끝에 표시될 풀이 내용(subList)을 함께 담는다.
    반환: (xml, 사용된 eq_id 수)
    """
    eid = eq_id
    paras = []
    for i, entry in enumerate(sol_entries):
        segs = entry.get('segments', []) or []
        p, used = _endnote_subpara(segs, eid, autonum_n=num if i == 0 else None)
        eid += used
        paras.append(p)
    if not paras:  # 안전망: 내용 없으면 빈 마커 문단 1개
        p, used = _endnote_subpara([], eid, autonum_n=num)
        eid += used
        paras.append(p)
    sublist = (
        '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        'vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" '
        'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
        + ''.join(paras) +
        '</hp:subList>'
    )
    xml = (
        f'<hp:endNote number="{num}" suffixChar="41" instId="{inst_id}">'
        + sublist +
        '</hp:endNote>'
    )
    return xml, eid - eq_id


def make_section_xml(problems: list) -> str:
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
        # 섹션 설정 문단 (필수)
        '<hp:p id="1" paraPrIDRef="0" styleIDRef="0" '
        'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">{_SECPR}</hp:run>'
        '<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" '
        'textheight="1000" baseline="850" spacing="600" horzpos="0" '
        'horzsize="42520" flags="393216"/></hp:linesegarray>'
        '</hp:p>',
    ]

    eq_id = 1000
    # 미주번호 카운터 (①②③...)
    CIRCLED = ['①','②','③','④','⑤','⑥','⑦','⑧','⑨','⑩',
               '⑪','⑫','⑬','⑭','⑮','⑯','⑰','⑱','⑲','⑳',
               '㉑','㉒','㉓','㉔','㉕','㉖','㉗','㉘','㉙','㉚']
    circled_idx = 0

    # ── 미주(endnote) 준비 ──
    # role=='solution' 엔트리를 본문에서 분리하고 문제 번호(base)별로 모음.
    # 본문 첫 문단에 미주 마커를 인라인 삽입하고, 풀이 내용은 문서 끝(subList)에 표시.
    solutions_by_base: dict = {}
    body_problems: list = []
    for prob in problems:
        if isinstance(prob, dict) and prob.get('role') == 'solution':
            solutions_by_base.setdefault(_solution_base(prob), []).append(prob)
        else:
            body_problems.append(prob)
    endnote_done = set()   # 이미 미주를 붙인 base
    endnote_num = 0        # 미주 일련번호 (1-base)
    inst_seq = 1800000000  # endNote instId 시드

    def _attach_endnote(para_xml: str, base: str) -> str:
        """para_xml(단일 run 문단)의 run 맨 앞에 base 에 해당하는 미주를 삽입.
        endNote 컨트롤은 반드시 <hp:ctrl> 로 감싸야 한컴이 인식한다."""
        nonlocal eq_id, endnote_num, inst_seq
        if (not base) or base in endnote_done or base not in solutions_by_base:
            return para_xml
        endnote_num += 1
        inst_seq += 1
        en_xml, used = make_endnote_xml(
            solutions_by_base[base], eq_id, endnote_num, inst_seq)
        eq_id += used
        endnote_done.add(base)
        marker = '<hp:ctrl>' + en_xml + '</hp:ctrl>'
        # _para_from_segments 의 run 여는 태그는 항상 '<hp:run charPrIDRef="0">'.
        # 그 직후(= 문제 본문 맨 앞)에 마커 삽입.
        return para_xml.replace(
            '<hp:run charPrIDRef="0">',
            '<hp:run charPrIDRef="0">' + marker,
            1,
        )

    for p_idx, prob in enumerate(body_problems):
        num      = prob.get('number', p_idx + 1)
        segments = prob.get('segments', [])

        # ── 이미지 삽입 타입 ──
        if prob.get('type') == 'image':
            img_p = make_image_para(
                bin_name  = prob['bin_name'],
                org_w     = prob.get('org_w', 800),
                org_h     = prob.get('org_h', 600),
                display_w = prob.get('display_w', None),
                display_h = prob.get('display_h', None),
                pic_id    = eq_id + 8000,
            )
            lines.append(img_p)
            # (이전 버전은 이미지 뒤 빈 줄을 1개 append 했으나 통일성 위해 제거.
            # 간격이 필요하면 insert_problem_gaps 또는 호출측에서 명시적으로 추가.)
            continue

        is_box = prob.get('box', False)
        # 미주번호: main=True 인 문제에만 ① 카운터 증가
        if prob.get('main', False):
            circled_idx += 1
            prefix = ''   # 원형 번호 없음
        elif prob.get('no_prefix', False) or is_box:
            prefix = ''   # 보조 문단/박스는 prefix 없음
        else:
            prefix = ''   # 기본적으로 prefix 없음 (디버그용 번호 제거)

        if not segments:
            # 구형 호환
            text = str(prob.get('text', ''))
            formulas = prob.get('formulas_hwpeq') or [
                latex_to_hwpeq(f) for f in prob.get('formulas', [])
            ]
            segments = [{'type': 'text', 'content': prefix + text}]
            for f in formulas:
                segments.append({'type': 'formula', 'content': f})
            para_xml, used, _ = _para_from_segments(segments, eq_id)
            eq_id += used
            para_xml = _attach_endnote(para_xml, _solution_base(prob))
            lines.append(para_xml)

        elif is_box:
            # 박스 타입: segments = [[row1_segs], [row2_segs], ...]
            rows = [list(r) for r in segments]  # 깊은 복사
            # 첫 행 앞에 번호 prefix 추가
            if rows and rows[0] and isinstance(rows[0][0], dict) and rows[0][0].get('type') == 'text':
                rows[0] = [{'type': 'text',
                            'content': prefix + rows[0][0]['content']}
                           ] + rows[0][1:]
            else:
                rows[0] = [{'type': 'text', 'content': prefix}] + rows[0]
            box_xml, used = make_box_xml(rows, eq_id, box_type=prob.get('box_type','condition'))
            eq_id += used
            lines.append(box_xml)

        else:
            # 일반 문단
            if segments[0].get('type') == 'text':
                segments = [{'type': 'text',
                             'content': prefix + segments[0]['content']}
                            ] + segments[1:]
            else:
                segments = [{'type': 'text', 'content': prefix}] + segments
            para_xml, used, _ = _para_from_segments(segments, eq_id)
            eq_id += used
            para_xml = _attach_endnote(para_xml, _solution_base(prob))
            lines.append(para_xml)

        # (이전에는 problem 마다 빈 줄을 1개씩 append 했으나, 같은 문제의 풀이
        # 여러 줄이 별개 problem 으로 들어올 때 줄과 줄 사이에 빈 줄이 보이는
        # 문제가 있어 제거함. 문제 사이 간격은 insert_problem_gaps 의
        # dummy 문단이 처리.)

    # ── 본문에 붙지 못한 미주 처리 (안전망) ──
    # 대응하는 본문 문단(일반 문단)을 못 찾은 풀이는 문서 끝에 단독 문단으로 미주 삽입.
    for base, sols in solutions_by_base.items():
        if base in endnote_done:
            continue
        endnote_num += 1
        inst_seq += 1
        en_xml, used = make_endnote_xml(sols, eq_id, endnote_num, inst_seq)
        eq_id += used
        endnote_done.add(base)
        lines.append(
            '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" '
            'pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="0"><hp:ctrl>{en_xml}</hp:ctrl></hp:run>'
            f'{_lineseg(1000)}'
            '</hp:p>'
        )

    lines.append('</hs:sec>')
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════
# 7. HWPX 패키징 (템플릿 기반)
# ══════════════════════════════════════════════════════════

def make_image_para(bin_name: str, org_w: int, org_h: int,
                    display_w: int = None, display_h: int = None,
                    pic_id: int = 9900) -> str:
    """
    이미지를 인라인으로 삽입하는 hp:p 문단 XML 생성.

    bin_name    : BinData 폴더 내 파일명 (확장자 없이), 예: 'image5'
    org_w/org_h : 원본 이미지 실제 픽셀 크기 (hwpUnit으로 환산됨)
                  1px ≈ 96dpi 기준 → 1inch = 2540 hwpUnit, 1px = 2540/96 ≈ 26.46
                  편의상 픽셀 크기 그대로 넣으면 내부에서 자동 환산
    display_w   : 문서에 표시할 너비(hwpUnit). None이면 페이지 너비(42520)에 맞춤
    display_h   : 문서에 표시할 높이(hwpUnit). None이면 비율 유지
    """
    PAGE_W = 42520  # 기본 페이지 너비 (hwpUnit)

    # 원본 크기 → hwpUnit (픽셀을 96dpi 기준 hwpUnit으로)
    PX_TO_HWP = 2540 / 96
    org_w_hwp = int(org_w * PX_TO_HWP)
    org_h_hwp = int(org_h * PX_TO_HWP)

    # 표시 크기 결정
    if display_w is None:
        display_w = min(PAGE_W, org_w_hwp)
    if display_h is None:
        # 비율 유지
        if org_w_hwp > 0:
            display_h = int(display_w * org_h_hwp / org_w_hwp)
        else:
            display_h = org_h_hwp

    scale = display_w / org_w_hwp if org_w_hwp > 0 else 1.0

    cx = display_w // 2
    cy = display_h // 2

    pic_xml = (
        f'<hp:pic id="{pic_id}" zOrder="{pic_id+1}" numberingType="PICTURE" '
        f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
        f'dropcapstyle="None" href="" groupLevel="0" instid="{pic_id+2}" reverse="0">'
        f'<hp:offset x="0" y="0"/>'
        f'<hp:orgSz width="{org_w_hwp}" height="{org_h_hwp}"/>'
        f'<hp:curSz width="{display_w}" height="{display_h}"/>'
        f'<hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{cx}" centerY="{cy}" rotateimage="1"/>'
        f'<hp:renderingInfo>'
        f'<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:scaMatrix e1="{scale:.4f}" e2="0" e3="0" e4="0" e5="{scale:.4f}" e6="0"/>'
        f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'</hp:renderingInfo>'
        f'<hc:img binaryItemIDRef="{bin_name}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
        f'<hp:imgRect>'
        f'<hc:pt0 x="0" y="0"/>'
        f'<hc:pt1 x="{org_w_hwp}" y="0"/>'
        f'<hc:pt2 x="{org_w_hwp}" y="{org_h_hwp}"/>'
        f'<hc:pt3 x="0" y="{org_h_hwp}"/>'
        f'</hp:imgRect>'
        f'<hp:imgClip left="0" right="{org_w_hwp}" top="0" bottom="{org_h_hwp}"/>'
        f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:imgDim dimwidth="{org_w_hwp}" dimheight="{org_h_hwp}"/>'
        f'<hp:effects/>'
        f'<hp:sz width="{display_w}" widthRelTo="ABSOLUTE" '
        f'height="{display_h}" heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
        f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" '
        f'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:shapeComment>그림</hp:shapeComment>'
        f'</hp:pic>'
    )

    # hp:p > hp:run > hp:pic 구조
    full_p = (
        f'<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">'
        + pic_xml +
        f'<hp:t/></hp:run>'
        f'<hp:linesegarray>'
        f'<hp:lineseg textpos="0" vertpos="0" vertsize="{display_h}" '
        f'textheight="{display_h}" baseline="{int(display_h*0.85)}" '
        f'spacing="600" horzpos="0" horzsize="{display_w}" flags="393216"/>'
        f'</hp:linesegarray>'
        f'</hp:p>'
    )
    return full_p


# ── 템플릿 자리표시자(placeholder) 시스템 ──
# 사용자가 한글에서 템플릿을 만들 때 본문에 `{{문제삽입}}` 같은 표식을 입력하면
# 그 문단만 생성된 문제 문단들로 치환되고, 템플릿의 나머지 본문(제목·머리말·꼬리말
# ·학생정보·이미지 등)은 그대로 유지된다. 표식이 없으면 기존 동작(전체 교체).

TEMPLATE_PLACEHOLDERS = ['{{문제삽입}}', '{{문제입력}}', '{{CONTENT}}', '{{문제}}']


def _find_marker_paragraph(section_xml: str, markers: list) -> tuple:
    """첫 표식 paragraph (start, end). 없으면 None. (호환용)."""
    found = _find_all_marker_paragraphs(section_xml, markers)
    return found[0] if found else None


def _find_all_marker_paragraphs(section_xml: str, markers: list) -> list:
    """section_xml 안에서 markers 를 포함하는 모든 <hp:p>...</hp:p> 의
    (start, end) 리스트 (등장 순서). 중첩 <hp:p> 균형 매칭."""
    n = len(section_xml)
    open_re = re.compile(r'<hp:p\b[^>]*>')
    results = []
    i = 0
    while i < n:
        m = open_re.search(section_xml, i)
        if not m:
            break
        para_start = m.start()
        depth = 1
        scan = m.end()
        while scan < n and depth > 0:
            nxt_open = open_re.search(section_xml, scan)
            nxt_close = section_xml.find('</hp:p>', scan)
            if nxt_close == -1:
                return results
            if nxt_open and nxt_open.start() < nxt_close:
                depth += 1
                scan = nxt_open.end()
            else:
                depth -= 1
                scan = nxt_close + len('</hp:p>')
        para_end = scan
        text = ''.join(re.findall(r'<hp:t>([^<]*)</hp:t>',
                                  section_xml[para_start:para_end]))
        if any(mk in text for mk in markers):
            results.append((para_start, para_end))
        i = para_end
    return results


def _extract_style_ids(paragraph_xml: str) -> tuple:
    """paragraph XML 에서 paraPrIDRef, styleIDRef, 첫 run 의 charPrIDRef 추출.
    표식 문단의 글꼴·크기·굵기를 그대로 따라가는 데 사용. 없으면 None."""
    p_id = re.search(r'\bparaPrIDRef="(\d+)"', paragraph_xml)
    s_id = re.search(r'\bstyleIDRef="(\d+)"', paragraph_xml)
    c_id = re.search(r'<hp:run\b[^>]*?\bcharPrIDRef="(\d+)"', paragraph_xml)
    return (p_id.group(1) if p_id else None,
            s_id.group(1) if s_id else None,
            c_id.group(1) if c_id else None)


def _apply_template_styles(blob: str, paraPrID: str, styleID: str, charPrID: str) -> str:
    """문제 문단들 blob 의 외곽 paragraph(시작 패턴: `<hp:p id="0" paraPrIDRef="0"
    styleIDRef="0" ... <hp:run charPrIDRef="0">`) 의 스타일 ID 들을 사용자 표식
    문단에서 추출한 ID 로 치환. nested(미주 subList·박스 rect 안 등)는 id 가
    달라서 영향 없음."""
    if paraPrID is None and styleID is None and charPrID is None:
        return blob
    old = ('<hp:p id="0" paraPrIDRef="0" styleIDRef="0" '
           'pageBreak="0" columnBreak="0" merged="0">'
           '<hp:run charPrIDRef="0">')
    new = (
        f'<hp:p id="0" paraPrIDRef="{paraPrID or "0"}" '
        f'styleIDRef="{styleID or "0"}" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{charPrID or "0"}">'
    )
    return blob.replace(old, new)


def _group_problems(problems: list) -> list:
    """problems 를 '문제 단위'로 그룹핑. 새 main:True 가 등장할 때마다 새 그룹.
    같은 문제의 본문·보기·박스·풀이(미주) 는 한 그룹으로 묶임."""
    groups = []
    cur = []
    for p in problems:
        if isinstance(p, dict) and p.get('main', False) and cur:
            groups.append(cur)
            cur = []
        cur.append(p)
    if cur:
        groups.append(cur)
    return groups


def _problem_paragraphs_only(problems: list) -> str:
    """make_section_xml 결과에서 헤더(<?xml>, <hs:sec>, secPr 문단) 와
    꼬리(</hs:sec>) 를 제거한 순수 문제 문단들만 반환. splice 용."""
    full = make_section_xml(problems)
    # 첫 번째 </hp:p> 는 secPr 문단 끝 → 그 다음부터가 문제 문단들
    secpr_end = full.find('</hp:p>') + len('</hp:p>')
    sec_close = full.rfind('</hs:sec>')
    return full[secpr_end:sec_close].strip()


def build_hwpx(template_path: str, output_path: str, problems: list,
               extra_images: dict = None):
    """
    기존 .hwpx 파일 복사 후 section0.xml 처리.
    - 템플릿에 `{{문제삽입}}` 같은 표식이 있으면 그 문단만 문제로 치환
      (템플릿의 제목·머리말·여백 설정 등 모두 유지).
    - 표식이 없으면 section0.xml 전체 교체 (기존 동작).
    extra_images: {'image5': '/path/to/image.jpg', ...} 형태로
                  BinData에 추가할 이미지 파일 지정
    """
    # 1) 템플릿 section 먼저 읽어서 자리표시자 유무 판정
    with zipfile.ZipFile(template_path, 'r') as _src:
        template_section = _src.read('Contents/section0.xml').decode('utf-8')

    markers_pos = _find_all_marker_paragraphs(template_section, TEMPLATE_PLACEHOLDERS)
    if markers_pos:
        # 자리표시자 모드: N개 표식에 문제 그룹 N개 1:1 분배 (앞에서부터)
        # 문제 그룹 > 표식: 초과 그룹은 마지막 표식에 모음.
        # 문제 그룹 < 표식: 남는 표식은 원본 그대로 (사용자가 직접 채울 자리).
        groups = _group_problems(problems)
        N = len(markers_pos)
        M = len(groups)
        # 표식별 할당
        assignments = []  # list of (start, end, group_index_range)
        for i in range(N):
            if M == 0:
                assignments.append((markers_pos[i][0], markers_pos[i][1], None))
            elif i < min(N - 1, M):
                assignments.append((markers_pos[i][0], markers_pos[i][1], [i]))
            elif i == N - 1:
                # 마지막 표식: 나머지 모든 그룹
                if i < M:
                    assignments.append(
                        (markers_pos[i][0], markers_pos[i][1], list(range(i, M))))
                else:
                    assignments.append((markers_pos[i][0], markers_pos[i][1], None))
            else:
                # 남는 표식 — 원본 그대로
                assignments.append((markers_pos[i][0], markers_pos[i][1], None))
        # 뒤에서부터 splice (offset 어긋남 방지)
        spliced = template_section
        for ps, pe, gidx in reversed(assignments):
            if gidx is None:
                continue  # 원본 표식 유지
            # 표식 문단의 스타일 ID 추출 → 삽입 문단도 동일 스타일 적용
            mk_xml = template_section[ps:pe]
            pp_id, st_id, cp_id = _extract_style_ids(mk_xml)
            # 해당 그룹들의 문제들을 합쳐 paragraphs 로
            merged = []
            for g in gidx:
                merged.extend(groups[g])
            problem_blob = _problem_paragraphs_only(merged) if merged else ''
            problem_blob = _apply_template_styles(problem_blob, pp_id, st_id, cp_id)
            spliced = spliced[:ps] + problem_blob + spliced[pe:]
        new_section = spliced.encode('utf-8')
        filled = sum(1 for a in assignments if a[2] is not None)
        print(f"  → 템플릿 자리표시자 {N}개 감지, 문제 {M}그룹 → {filled}개 자리에 분배")
    else:
        # 기존 동작: section0.xml 전체 교체
        new_section = make_section_xml(problems).encode('utf-8')

    # extra_images가 있으면 content.hpf에도 opf:item 등록 필요
    MIME = {
        '.jpg': 'image/jpg', '.jpeg': 'image/jpg',
        '.png': 'image/png', '.bmp': 'image/bmp',
        '.gif': 'image/gif', '.tif': 'image/tiff',
        '.tiff': 'image/tiff', '.webp': 'image/webp',
    }

    with zipfile.ZipFile(template_path, 'r') as src, \
         zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as dst:

        for item in src.infolist():
            if item.filename == 'Contents/section0.xml':
                dst.writestr(item.filename, new_section)
            elif item.filename == 'Contents/content.hpf' and extra_images:
                # content.hpf에 이미지 항목 추가 등록
                hpf = src.read(item.filename).decode('utf-8')
                # </opf:manifest> 바로 앞에 새 item 삽입
                new_items = ''
                for bin_name, img_path in extra_images.items():
                    ext = os.path.splitext(img_path)[1].lower()
                    mime = MIME.get(ext, 'image/png')
                    new_items += (
                        f'<opf:item id="{bin_name}" '
                        f'href="BinData/{bin_name}{ext}" '
                        f'media-type="{mime}" isEmbeded="1"/>'
                    )
                hpf = hpf.replace('</opf:manifest>', new_items + '</opf:manifest>')
                dst.writestr(item.filename, hpf.encode('utf-8'))
                print(f"  → content.hpf 업데이트 ({len(extra_images)}개 이미지 등록)")
            else:
                dst.writestr(item, src.read(item.filename))

        # BinData에 이미지 파일 실제 삽입
        if extra_images:
            for bin_name, img_path in extra_images.items():
                ext = os.path.splitext(img_path)[1].lower()
                archive_name = f'BinData/{bin_name}{ext}'
                with open(img_path, 'rb') as f:
                    dst.writestr(archive_name, f.read())
                print(f"  → 이미지 추가: {archive_name}")

    kb = os.path.getsize(output_path) / 1024
    print(f"✅ HWPX 생성 완료: {output_path} ({kb:.1f} KB)")


# ══════════════════════════════════════════════════════════
# 8. 전체 파이프라인
# ══════════════════════════════════════════════════════════

# 지원 이미지 확장자
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}

def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS

def vision_extract_images(image_paths: list) -> str:
    """이미지 파일 목록 → Vision AI로 텍스트+수식 추출 (PNG/JPG 모두 지원)"""
    import mimetypes
    PROMPT = """이미지의 수학 문제를 정확히 인식하세요.
수식은 LaTeX 형식($수식$)으로, 문제 번호와 텍스트는 그대로 유지하세요.
그림/도형은 [그림]으로 표시하세요."""
    results = []
    for path in image_paths:
        ext = os.path.splitext(path)[1].lower()
        mime = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png',  '.webp': 'image/webp',
            '.bmp': 'image/jpeg', '.tiff': 'image/jpeg', '.tif': 'image/jpeg',
        }.get(ext, 'image/jpeg')

        with open(path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()

        payload = json.dumps({
            "model": "claude-sonnet-4-20250514", "max_tokens": 4000,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime, "data": img_b64}},
                {"type": "text", "text": PROMPT}
            ]}]
        }).encode()
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload, headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req) as resp:
            results.append(json.loads(resp.read())['content'][0]['text'])
    return "\n\n".join(results)


def convert_to_hwpx(input_path: str, template_hwpx: str, output_path: str):
    """
    PDF 또는 이미지(JPG/PNG 등) 파일을 HWPX로 변환.
    입력 파일 형식을 자동 감지합니다.
    """
    print(f"📄 변환 시작: {input_path}")

    if is_image_file(input_path):
        # ── 이미지 파일 직접 처리 ──
        print(f"  → 이미지 파일 감지 ({os.path.splitext(input_path)[1]})")
        print(f"  → Vision AI 인식 중...")
        raw_text = vision_extract_images([input_path])

    else:
        # ── PDF 처리 ──
        scanned = is_scanned_pdf(input_path)
        print(f"  → {'스캔형' if scanned else '텍스트형'} PDF")
        if scanned:
            pages = rasterize_pdf(input_path)
            print(f"  → {len(pages)}페이지 Vision 인식 중...")
            raw_text = vision_extract(pages)
            for p in pages:
                try: os.remove(p)
                except: pass
        else:
            raw_text = extract_text_pdf(input_path)

    print(f"  → 텍스트 {len(raw_text)}자 추출")
    print("  → 문제 구조 분석 중...")
    problems = ai_extract_problems(raw_text)
    print(f"  → {len(problems)}개 문제 감지")
    build_hwpx(template_hwpx, output_path, problems)


# 하위 호환성 유지
def convert_pdf_to_hwpx(pdf_path: str, template_hwpx: str, output_path: str):
    convert_to_hwpx(pdf_path, template_hwpx, output_path)


# ══════════════════════════════════════════════════════════
# 단위 테스트
# ══════════════════════════════════════════════════════════

def test_conversion():
    print("=== LaTeX → hwpEQ 변환 테스트 ===\n")
    cases = [
        (r'\frac{-b\pm\sqrt{b^2-4ac}}{2a}',
         '{-b PLUSMINUS sqrt {b^2-4ac}} over {2a}'),
        (r'x^{2n+1}+y^{2n+1}',
         'x^{2n+1}+y^{2n+1}'),
        (r'2(1^{11}+2^{11}+\cdots+n^{11})',
         '2(1^{11}+2^{11}+cdots+n^{11})'),
        (r'\lim_{x\to 0}\frac{\sin x}{x}=1',
         'lim _{x rarrow 0} {sin x} over {x}=1'),
        (r'\int_0^1 x^2\,dx=\frac{1}{3}',
         'INT _{0}^{1} x^2~dx={1} over {3}'),
        (r'\sum_{k=1}^{n} k^2',
         'SUM _{k=1}^{n} k^2'),
        (r'\sum_{n=1}^{\infty}\frac{1}{n^2}=\frac{\pi^2}{6}',
         'SUM _{n=1}^{inf} {1} over {n^2}={pi ^2} over {6}'),
        (r'x\geq 0', 'x GEQ 0'),
        (r'n^{11}', 'n^{11}'),
        (r'n^11', 'n^{11}'),
        (r'x^2n', 'x^{2n}'),
        (r'b^2', 'b^2'),
        (r'\alpha^2+\beta^2', 'alpha ^2+beta ^2'),
        (r'(x+1)(x+a)^2', '(x+1)(x+a)^2'),
        (r'\sqrt{3}', 'sqrt {3}'),
        (r'\frac{x^{n+1}}{n+1}+C', '{x^{n+1}} over {n+1}+C'),
        (r'\frac{\sqrt{x+1}-\sqrt{x}}{1}', '{sqrt {x+1}- sqrt {x}} over {1}'),
        (r'x^{2n}-x^{2n-1}y', 'x^{2n}-x^{2n-1}y'),
        (r'\Rightarrow', 'RARROW'),
        (r'|r| < 1', '|r| < 1'),
        (r'a\times b^2\times c^3', 'a TIMES b^2 TIMES c^3'),
        (r'\frac{d}{dx}\int_a^{g(x)} f(t)\,dt=f(g(x))',
         '{d} over {dx} INT _a^{g(x)} f(t)~dt=f(g(x))'),
        (r'x^{2n+1}+y^{2n+1}=(x+y)(x^{2n}-x^{2n-1}y-\cdots)',
         'x^{2n+1}+y^{2n+1}=(x+y)(x^{2n}-x^{2n-1}y-cdots)'),
    ]
    ok = err = 0
    norm = lambda s: re.sub(r' +', ' ', s.strip())
    for latex, expected in cases:
        result = latex_to_hwpeq(latex)
        match = norm(result) == norm(expected)
        sym = "✅" if match else "⚠ "
        if match: ok += 1
        else: err += 1
        print(f"{sym} {latex[:60]}")
        if not match:
            print(f"   결과: {result}")
            print(f"   기대: {expected}")
    print(f"\n통과: {ok}/{len(cases)}")


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == '--test':
        test_conversion()
    elif len(sys.argv) == 4:
        convert_to_hwpx(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        print("사용법:")
        print("  변환(PDF): python pdf_to_hwpx.py input.pdf  template.hwpx output.hwpx")
        print("  변환(이미지): python pdf_to_hwpx.py input.jpg  template.hwpx output.hwpx")
        print("  변환(이미지): python pdf_to_hwpx.py input.png  template.hwpx output.hwpx")
        print("  테스트:    python pdf_to_hwpx.py --test")
        print(f"\n  지원 이미지 형식: {', '.join(sorted(IMAGE_EXTS))}")
        sys.exit(1)
