# 수학 문제 PDF/이미지 → HWPX 변환 Streamlit 앱

Claude Vision 으로 수식·본문을 인식해서 한글(.hwpx) 문서로 변환하는 웹 앱.

## 구성

```
.
├── app.py                       # Streamlit 엔트리
├── pdf_to_hwpx.py               # HWPX 빌더 라이브러리 (별도 제공 — 아래 참조)
├── template.hwpx                # HWPX 템플릿 (없으면 업로드 UI 로 대체)
├── requirements.txt
├── packages.txt                 # Streamlit Cloud 용 apt 패키지 (poppler)
└── .streamlit/
    └── secrets.toml.example
```

### 반드시 준비해야 하는 두 파일

1. **`pdf_to_hwpx.py`** — HWPX 빌더 스크립트. `build_hwpx(template, output, problems)` 함수를 제공합니다. 이 저장소에는 포함하지 않았으니, 이미 가지고 있는 파일을 그대로 `app.py` 와 같은 디렉터리에 두세요.
2. **`template.hwpx`** — 스타일·레이아웃이 이미 잡혀있는 빈 HWPX 파일. 루트에 두거나, 실행 중 UI에서 업로드해도 됩니다.

## 로컬 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 맥: poppler 필요 (PDF 지원)
brew install poppler

export ANTHROPIC_API_KEY="sk-ant-..."
streamlit run app.py
```

## Streamlit Cloud 배포

1. 이 폴더를 GitHub 저장소로 push.
2. [share.streamlit.io](https://share.streamlit.io) 에서 New app → 저장소/브랜치 선택, 엔트리 `app.py`.
3. **Settings → Secrets** 에 다음 줄 붙여넣기:
   ```
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
4. `packages.txt` 의 `poppler-utils` 가 자동 설치되어 PDF 렌더링이 동작합니다.

## 동작 흐름

```
업로드 (PDF/JPG/PNG)
   │
   ├─ PDF → pdf2image 로 페이지별 PNG
   │
   ▼
Claude Vision (claude-opus-4-5)
   └─ LaTeX 포함 평문 추출
   │
   ▼
Claude (구조화) → JSON
   └─ number / segments / box / image_placeholder
   │
   ▼
pdf_to_hwpx.build_hwpx()
   └─ template.hwpx 의 section0.xml 교체
   │
   ▼
다운로드 버튼
```

## 환경변수

| 이름 | 설명 |
|------|------|
| `ANTHROPIC_API_KEY` | Claude API 키 (필수) |

## 참고

- Vision/구조화 모델은 `app.py` 상단 `VISION_MODEL`, `EXTRACT_MODEL` 에서 조정.
- 사이드바에서 PDF 렌더링 DPI, Vision 원문·JSON 표시 토글 가능.
- 이미지 자동 크롭(그래프 분리)은 이 앱에 포함하지 않음 — 필요하면 `pdf_to_hwpx.crop_graph` 를 호출하는 전처리 단계를 `collect_input_images` 에 추가하세요.
