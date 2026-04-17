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

# pdf_to_hwpx 스크립트를 라이브러리로 로드 (수정 없이 그대로 사용)
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import pdf_to_hwpx  # noqa: E402

# ────────────────────────────────────────────────────────────
# 상수 / 설정
# ────────────────────────────────────────────────────────────

VISION_MODEL = "claude-opus-4-5"
EXTRACT_MODEL = "claude-opus-4-5"
MAX_TOKENS = 8000

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PDF_EXTS = {".pdf"}
DEFAULT_TEMPLATE = HERE / "template.hwpx"

VISION_PROMPT = """이 이미지에 있는 수학 문제를 정확히 인식하세요.

요구사항:
- 문제 번호(예: 1., 2., 23.)를 그대로 유지
- 본문 텍스트는 한국어 그대로
- 수식은 LaTeX 인라인 형식(`$...$`)으로 표기
- 박스로 묶인 보기(ㄱ/ㄴ/ㄷ) 또는 조건(㈎㈏㈐)은 `[박스시작]` / `[박스끝]`으로 감쌉니다
- 그래프·도형이 있는 위치에는 `[그림: 간단한 설명]`이라고 표시
- 페이지 머리글/바닥글/페이지 번호는 생략"""

STRUCT_PROMPT = r"""다음 수학 문제 텍스트를 JSON 구조로 변환하세요.

## 출력 규칙
- 반드시 **JSON만** 반환 (마크다운 코드펜스 금지)
- 최상위 키는 `"problems"`, 값은 배열
- 각 항목은 아래 세 종류 중 하나

### 1) 일반 문단
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

### 2) 박스 (보기/조건)
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

### 3) 그림 자리표시자
`[그림: …]` 표기가 있는 위치에 삽입:
```
{"type": "image_placeholder", "description": "…원문 그림 설명…"}
```

## 세그먼트 분리 기준
- 함수식(`f(x)`), 변수(`x, a, n`), 분수, 지수, 루트 등 수학 기호를 포함하면 `formula`
- 조사·명사·동사 등 순수 한국어는 `text`
- `formula`의 `content`는 LaTeX 본문 (달러 기호 제외)
"""

# ────────────────────────────────────────────────────────────
# Claude 호출 헬퍼
# ────────────────────────────────────────────────────────────


def get_client() -> Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY") or st.secrets.get("ANTHROPIC_API_KEY", "")
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


def vision_recognize(client: Anthropic, image_paths: list[Path]) -> str:
    """이미지들을 Claude Vision 으로 읽어 LaTeX 섞인 평문 반환."""
    outputs: list[str] = []
    for idx, path in enumerate(image_paths, 1):
        with path.open("rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        resp = client.messages.create(
            model=VISION_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": _mime_for(path),
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": VISION_PROMPT},
                    ],
                }
            ],
        )
        outputs.append(resp.content[0].text)
    return "\n\n".join(outputs)


def structure_problems(client: Anthropic, raw_text: str) -> list[dict[str, Any]]:
    resp = client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": STRUCT_PROMPT + "\n\n입력:\n" + raw_text,
            }
        ],
    )
    text = resp.content[0].text.strip()
    # 혹시 모를 코드펜스 제거
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    return sanitize_problems(data.get("problems", []))


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
    return {"type": seg_type, "content": content}


def sanitize_problems(problems: list[Any]) -> list[dict[str, Any]]:
    """
    Claude 가 준 JSON 을 build_hwpx 가 기대하는 모양으로 정리:
      - 모든 segment 는 {"type": "text"|"formula", "content": str} 형태
      - image_placeholder 는 problems 레벨에서만 허용 (segments 안에 섞여 있으면 빼냄)
      - 빈 segment/problem 은 제거
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


def collect_input_images(uploaded, workdir: Path) -> list[Path]:
    """업로드된 파일(이미지 혹은 PDF) → 이미지 경로 리스트."""
    image_paths: list[Path] = []
    for up in uploaded:
        suffix = Path(up.name).suffix.lower()
        saved = workdir / up.name
        saved.write_bytes(up.getvalue())

        if suffix in IMAGE_EXTS:
            image_paths.append(saved)
        elif suffix in PDF_EXTS:
            st.write(f"📄 PDF 렌더링: `{up.name}`")
            image_paths.extend(pdf_to_images(saved, workdir))
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
        dpi = st.slider("PDF 렌더링 DPI", min_value=100, max_value=300, value=200, step=10)
        show_raw = st.checkbox("Vision 인식 원문 보기", value=False)
        show_json = st.checkbox("구조화 JSON 보기", value=False)

    uploaded = st.file_uploader(
        "문제 파일 업로드 (여러 개 가능)",
        type=["pdf", "jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
    )

    uploaded_template = st.file_uploader(
        "HWPX 템플릿 업로드 (선택 — 기본 template.hwpx 가 있으면 생략 가능)",
        type=["hwpx"],
        accept_multiple_files=False,
    )

    if not uploaded:
        st.info("변환할 파일을 업로드하세요.")
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
            image_paths = collect_input_images(uploaded, workdir)
            if not image_paths:
                status.update(label="처리할 이미지가 없습니다.", state="error")
                return
            status.update(label=f"이미지 {len(image_paths)}장 준비 완료", state="complete")

        # 3) Vision 인식
        with st.status("Claude Vision 인식 중…", expanded=False) as status:
            raw_text = vision_recognize(client, image_paths)
            status.update(label="Vision 인식 완료", state="complete")

        if show_raw:
            with st.expander("📝 Vision 인식 원문"):
                st.code(raw_text, language="markdown")

        # 4) 구조화
        with st.status("문제 구조화 중…", expanded=False) as status:
            try:
                problems = structure_problems(client, raw_text)
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
