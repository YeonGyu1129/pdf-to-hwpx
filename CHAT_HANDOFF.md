# 🔄 PDF→HWPX 변환기 — 이전 채팅 이어받기

> 이 문서를 새 채팅 첫 메시지로 그대로 붙여넣으세요. Claude 가 전체 맥락을 잡습니다.

---

## 📁 프로젝트 위치
`/Users/yeon-gyukim/클로드 코드 작업/pdf를 hwpx로 변환`

## 🔗 배포 정보
- **GitHub**: github.com/YeonGyu1129/pdf-to-hwpx
- **Streamlit 앱**: yeongyu-pdf-to-hwpx.streamlit.app
- **Claude.ai 스킬 파일**: `~/Downloads/math-pdf-to-hwpx.skill` (재업로드 가능)

## 📌 마지막 작업 시점
- **마지막 commit**: `fcd9730` — Sync skill post_process with latest app post-processors
- 모든 task 완료, 진행 중 작업 없음

---

## 🔧 핵심 파일 / 역할

| 파일 | 역할 |
|------|------|
| `app.py` | Streamlit 앱 + `pdf_to_hwpx` monkey-patch 다수 (latex_to_hwpeq, make_section_xml, build_hwpx, make_box_xml, _SECPR) |
| `pdf_to_hwpx.py` | HWPX 빌더 라이브러리 (원본은 "외부 스킬, 수정 금지" — 단 `_SECPR landscape` 만 직접 정정함) |
| `math-pdf-to-hwpx.skill/` | Claude.ai 업로드용 스킬 패키지 |
| `math-pdf-to-hwpx.skill/scripts/pdf_to_hwpx.py` | 스킬용 빌더 (앱과 동일 효과, 직접 수정) |
| `math-pdf-to-hwpx.skill/scripts/post_process.py` | 스킬 후처리 (sanitize + 보기 흡수 + 미주 정규화) |
| `math-pdf-to-hwpx.skill/scripts/box_templates.py` | rect 도형 박스 템플릿 (조건/보기) |
| `math-pdf-to-hwpx.skill/SKILL.md` | Claude.ai 스킬 지시문 |
| `box_templates.py` | 프로젝트 루트 사본 (앱이 import) |
| `template.hwpx` | 기본 HWPX 템플릿 (landscape WIDELY = portrait) |
| `참고_조건상자와_보기상자.hwpx` | rect 박스 추출 원본 (사용자 제공) |
| `샘플_시험지_템플릿.hwpx`, `_2단.hwpx` | 사용자 정의 템플릿 샘플 (1단/2단) |
| `math-pdf-to-hwpx.zip` | 스킬 zip 패키지 (재업로드용) |

---

## ✅ 구현된 기능 (전체)

### 수식 변환 정확도
- 유니코드 수학 기호 사전 변환 (`×`, `÷`, `≠`, `≤`, `≥`, `±`, `∴`, `∵`, `∞`, `∈`, `∪`, `⊥`, `∠`, `△` 등 ~30종) → LaTeX 명령
- `\frac{...}{...}` 임의 깊이 중첩 — balanced-brace 매처 (분자/분모 안 `\sqrt`, `\left/\right` OK)
- `\sqrt{...}` 임의 깊이 중첩 — balanced-brace 매처 (이중근호 etc.)
- 점쌍 벡터: `\overrightarrow{\mathrm{AB}}` → `{vec{rm AB}}` (외부 그룹으로 한컴 파싱 안정화)
- 점쌍 윗줄: `\overline{\mathrm{AB}}` → `{bar{rm AB}}` (vec와 동일 패턴, 연산자 뒤 bar 사라짐 방지)
- 소문자 벡터: `\vec{a}` → `{vec{it a}}`
- `\mathit` 사전 보호 (skill 변환기가 따옴표 변환하면서 이탤릭 명시가 사라지는 문제)
- `\widehat`/호 → `{arch{rm AB}}`
- 임시 기호 `㉠㉡㉢` / `ⓐⓑⓒ` 등은 원본 그대로 (문제마다 ㄱ부터 다시 시작, 재번호 금지)

### 페이지 / 레이아웃
- **`landscape="WIDELY"` = portrait(세로)** — 한컴 enum 이 직관과 반대 (`NARROWLY`=가로). 모든 출력 세로.
- 풀이 줄 사이 빈 줄 자동 제거 (problem 마다 trailing empty `<hp:p>` 안 붙임)
- 문제 사이 간격은 `_insert_problem_spacing` 의 gap dummy 가 처리

### 박스 (조건상자 / 보기상자)
- **rect 도형 기반** — 참고 hwpx 에서 추출한 템플릿 사용 (`box_templates.py`)
  - `CONDITION_BOX_RECT` — 조건상자
  - `BOGI_BOX_RECT` — 보기상자 (외곽 rect + nested 라벨 rect + nested 내용 rect)
- balanced-brace 매처로 nested 정확 처리
- **보기 박스 흡수** — AI 가 `[보 기]` 만 박스로 분류하고 `ㄱ./ㄴ./ㄷ.` 를 별도 일반 문단으로 출력해도, `_absorb_bogi_items_into_box()` 가 자동으로 박스 segments 에 흡수. 박스 안 `[보 기]` 라벨 row 는 제거(템플릿에 내장).

### 미주(풀이 endnote) 처리
- 사이드바 토글: **🔖 풀이를 미주로 처리** ("보이는 것 모두" 모드에서만)
- **해설 파일 별도 업로드 영역** (📖 해설 파일) — 올리면 자동 미주 처리
- `<hp:endNote>` 는 `<hp:ctrl>` 로 감싸야 한컴이 인식 — 누락하면 미주 통째 사라짐
- 본문 마커는 **문제 시작 위치** (run 맨 앞)에 인라인 삽입
- subList 문단 속성 (`id="2147483648"` paraPrIDRef 10/22 등) 템플릿과 일치
- **`[정답] {답}` → `[풀이]` → 풀이 본문 줄별** 구조 강제
  - `_normalize_solution_groups()` 후처리: AI 가 `[해설]` 쓰거나 `[정답]` 빠뜨려도 자동 보정
- 같은 문제 번호 그룹 자동 매칭

### 사용자 정의 템플릿 (자리표시자 splice)
- 표식: `{{문제삽입}}`, `{{문제입력}}`, `{{문제}}`, `{{CONTENT}}`
- 한글에서 자유롭게 디자인 → 표식 위치에 문제 자동 삽입
- 템플릿의 secPr·머리말·꼬리말·페이지 설정 그대로 보존
- **다단(2단/3단) 지원** — colPr colCount 자동 보존
- **N개 표식 → 문제 N개 1:1 분배** (M>N: 마지막 표식에 초과 모음 / M<N: 남는 표식 그대로)
- **표식 문단의 paraPr/styleIDRef/charPrIDRef 자동 상속** → 사용자가 표식 자리에 본문 글꼴로 적으면 그 글꼴로 삽입
- 샘플 템플릿 제공: `샘플_시험지_템플릿.hwpx` (1단), `_2단.hwpx`

### 추출 정확도 / UX
- 사이드바: 추출 범위(문제만/보이는 것 모두), 정확도 4단계, 미주 토글
- 검수 + 자동 수정 루프 (정확도 균형 이상)
- 클립보드 붙여넣기 (Cmd+V/Ctrl+V) — 비우기 버튼 dynamic key 로 재추가 방지
- 별도 문제/해설 업로드 영역
- 사이드바에 템플릿 만드는 법 expander

---

## 🏗️ 아키텍처 핵심

### 앱 ↔ 스킬 동일 효과
- **앱 (Streamlit)**: `pdf_to_hwpx` 의 함수들을 monkey-patch 로 교체 (원본 보존)
  - `_patched_latex_to_hwpeq`, `_patched_make_section_xml`, `_patched_build_hwpx`, `_patched_make_box_xml`
  - `pdf_to_hwpx._SECPR` 도 import 직후 치환
- **스킬 (Claude.ai)**: `scripts/pdf_to_hwpx.py` 직접 수정 + `scripts/post_process.py` 에 후처리 추가
- 두 경로 모두 동일한 후처리 함수 보유 (이름·동작 일치)

### 후처리 파이프라인 (양쪽 동일)
```
AI 추출 결과
  → sanitize_problems        (정상 형식)
  → _absorb_bogi_items_into_box   (보기 박스 흡수)
  → _normalize_solution_groups    ([정답]/[풀이] 강제)
  → _insert_problem_spacing (body) + sols (미주는 끝에)
  → build_hwpx
      → make_section_xml      (미주 ctrl 래핑, 빈 줄 없음)
      → make_box_xml          (rect 도형 박스)
      → 자리표시자 splice     (템플릿 모드)
```

---

## 🎯 작업 컨벤션 (사용자가 선호하는 패턴)

1. **한국어 대화** — 모든 응답·코멘트·커밋 메시지 한국어
2. **작업 트래킹**: 3단계 이상 작업은 `TaskCreate` / `TaskUpdate` 사용
3. **자동 commit + push**: 변경 후 항상 GitHub 푸시 (Streamlit Cloud 자동 재배포)
4. **양쪽 동기화**:
   - 코드 변경 시 앱(app.py)+스킬(math-pdf-to-hwpx.skill/) 모두 적용
   - zip 재패키징 → `~/Downloads/math-pdf-to-hwpx.zip` + `.skill` 양쪽 갱신
5. **테스트 결과 파일**: 검증용 .hwpx 는 `~/Downloads/` 에 두기
6. **명확한 진단**: 사용자 문제 보고 시 코드 직접 추적 (regex/XML 구조 분석)
7. **응답 형식**: 표·이모지·짧은 섹션 선호. 장황한 설명 지양

## ⚠️ 알아둘 함정 / 한컴 hwpEQ 특이사항

1. **`landscape` enum**: `WIDELY`=portrait(세로), `NARROWLY`=landscape(가로). 직관과 반대.
2. **컨트롤 요소는 `<hp:ctrl>` 로 감싸야** 한컴이 인식 — `<hp:endNote>`, `<hp:autoNum>` 등.
3. **rect 박스 nested 구조**: 보기상자는 외곽 rect 안에 라벨 rect + 내용 rect 가 nested. balanced 매처 필수.
4. **vec/bar 외부 그룹**: `vec rm{X}` 단독은 한컴이 잘못 파싱 (vec 가 다음 토큰 먹음). `{vec{rm X}}` 로 감싸야.
5. **`\frac`/`\sqrt` 1-level regex 한계**: 분자/분모/내용에 깊은 nested 있으면 매치 실패 → catch-all 이 삭제. balanced 매처 필수.
6. **catch-all `\\[a-zA-Z]+`**: 변환 안 된 LaTeX 명령 삭제. 변환기 자체에 누락 명령 있으면 사라짐.
7. **AI 추출 일관성 ≠ 100%**: 같은 PDF 라도 다른 결과 나올 수 있어 후처리 안전망 필수.
8. **paste_image_button** 컴포넌트는 고정 key 면 클립보드 상태 보존 → 비우기 후 재추가됨. dynamic key 필요.

---

## 🚀 새 채팅에서 작업 이어가기

새 채팅에서 이 문서를 첫 메시지로 붙여넣고, 그 다음에 원하는 작업을 말씀하시면 됩니다. 예:

> "현재 X 가 안 되는데 고쳐줘"
> "Y 기능을 추가하고 싶어"
> "결과물 보여줄게, 이 부분이 이상한데 진단해줘"

---

_마지막 갱신: 2026-06-04 / commit fcd9730 시점_
