---
name: math-pdf-to-hwpx
description: 수학 문제 이미지/PDF를 한글 문서(.hwpx) 파일로 변환하는 스킬. 점·선분 로만체, 프라임, 집합 기호, 극한, 괄호 구조 등 한컴 수식편집기 친화적으로 후처리합니다. 트리거 - "한글로 변환", "hwpx로", "한글 파일", "수학 문제 타이핑", "HWPX", "한컴 오피스로"
---

# 수학 문제 이미지/PDF → 한글(HWPX) 변환 스킬

## 언제 이 스킬을 쓰나요?

사용자가 **수학 문제 이미지 또는 PDF** 를 주면서 다음 같이 요청할 때:

- "한글 파일(hwpx)로 변환해줘"
- "수학 문제를 한컴 오피스로 옮겨줘"
- "이 문제 타이핑해줘 (한글 수식편집기로)"
- "HWPX 로 만들어줘"

---

## 🔰 사용자에게 먼저 물어볼 것

1. **추출 범위** 중 선택:
   - **문제만** (기본): 문항 본문만, 풀이·해설 제외
   - **보이는 것 모두**: 풀이·해설·손글씨·주석까지 전부

2. **템플릿 업로드 여부**:
   - 기본 template.hwpx 는 `assets/template.hwpx` 에 포함됨
   - 사용자가 별도 템플릿 있으면 업로드 요청

---

## 🌟 LaTeX 작성 규칙 (반드시 준수)

### ① 모든 알파벳·숫자·수학기호는 무조건 formula 세그먼트로 분리

본문 텍스트(text) 에 알파벳·숫자를 **절대 넣지 말 것**.

```
❌ {"type":"text","content":"최댓값을 M이라 하면, M=2"}
✅ {"type":"text","content":"최댓값을 "},
   {"type":"formula","content":"M"},
   {"type":"text","content":"이라 하면, "},
   {"type":"formula","content":"M=2"}

❌ {"type":"text","content":"한 변의 길이가 4인 정삼각형"}
✅ {"type":"text","content":"한 변의 길이가 "},
   {"type":"formula","content":"4"},
   {"type":"text","content":"인 정삼각형"}
```

### ② 단일 대문자의 글꼴 구분 — 같은 문자라도 용도에 따라 다름

| 용도 | LaTeX | 출력 글꼴 |
|------|-------|----------|
| **점·꼭짓점·원점·중심** (점 A, 꼭짓점 P, 원점 O, 중심 O) | `\mathrm{A}`, `\mathrm{P}`, `\mathrm{O}` | 로만체 (필수) |
| **변수** (최댓값 M, 최솟값 m, 적분값 I, 합 S, 함수 f) | `M`, `m`, `I`, `S`, `f` | 자동 이탤릭 (그대로) |
| **도형·곡선·영역** (구 S, 원 C, 영역 D, 타원 C) | `\mathit{S}`, `\mathit{C}`, `\mathit{D}` | 이탤릭 명시 |
| **집합** (집합 A, 집합 B, $A \cap B$) | `\mathit{A}`, `\mathit{B}` | 이탤릭 명시 |
| **확률변수** (확률변수 X, Y, Z) | `\mathit{X}`, `\mathit{Y}`, `\mathit{Z}` | 이탤릭 명시 |

**판별법**:
- "점 X / 꼭짓점 X / 원점 X / 중심 X" → `\mathrm{X}` (로만)
- "구 X / 원 X / 평면 X / 영역 X / 곡선 X / 타원 X" → `\mathit{X}` (이탤릭 명시)
- "**집합 X**", "원소가 ~인 집합 X", `\mathit{A} \cap \mathit{B}` → `\mathit{X}` (이탤릭 명시)
- "**확률변수 X**", "X가 정규분포를 따른다", `\mathit{X} \sim N(\mu, \sigma^2)` → `\mathit{X}` (이탤릭 명시)
- "최댓값/적분값/합/함수 X", 그냥 변수 X → `X` (그대로)

**예시**:
- ✅ `\mathit{X} \sim N(0, 1)` — 확률변수 X
- ✅ `\mathit{A} = \{1, 2, 3\}` — 집합 A
- ✅ `\mathit{A} \cup \mathit{B} = \mathit{C}` — 집합 합집합
- ✅ `P(\mathit{X} \leq 2) = 0.5` — 확률변수가 인자

### ③ 벡터·선분·점쌍 ⚠️ **매우 중요**

이미지에서 **글자 위에 작은 화살표(→)가 있으면 무조건 벡터**.

#### A) 점쌍 벡터 (대문자 두 글자 이상) — `\overrightarrow{\mathrm{}}`

| 종류 | LaTeX | hwpEQ 출력 |
|------|-------|----------|
| 점쌍 벡터 | `\overrightarrow{\mathrm{AB}}` | `{vec{rm AB}}` |
| 벡터 합 | `\overrightarrow{\mathrm{AP}}+\overrightarrow{\mathrm{BQ}}` | `{vec{rm AP}}+{vec{rm BQ}}` |
| 첨자 점쌍 | `\overrightarrow{\mathrm{O}_{1}\mathrm{P}}` | `{vec{rm O _{1} rm P}}` |
| 벡터 크기 | `\left|\overrightarrow{\mathrm{AP}}+\overrightarrow{\mathrm{BQ}}\right|` | `left | {vec{rm AP}}+{vec{rm BQ}} right |` |

#### B) 소문자 벡터 (1글자) — `\vec{}` ⚠️ **점쌍과 구분**

| 종류 | LaTeX | hwpEQ 출력 |
|------|-------|----------|
| 기본 벡터 | `\vec{a}`, `\vec{b}`, `\vec{v}` | `{vec{it a}}` (it 자동) |
| 영벡터 | `\vec{0}` | `{vec{0}}` (0은 그대로) |
| 벡터 합 | `\vec{a}+\vec{b}` | `{vec{it a}}+{vec{it b}}` |
| 스칼라 곱 | `k\vec{a}` | `it{k}{vec{it a}}` |
| 선형 결합 | `k\vec{a}+l\vec{b}` | `it{k}{vec{it a}}+it{l}{vec{it b}}` |
| 분수 계수 | `\dfrac{1}{2}\vec{a}-\dfrac{2}{3}\vec{b}` | `{1} over {2}{vec{it a}}-...` |
| 절댓값 | `\left|\vec{a}\right|` | `left | {vec{it a}} right |` |
| 절댓값² | `\left|\vec{a}\right|^{2}` | `left | {vec{it a}} right |^{2}` |
| 내적 | `\vec{a} \cdot \vec{b}` | `{vec{it a}} cdot {vec{it b}}` |
| 좌표 벡터 | `\vec{a}=(2, -1)` | `{vec{it a}}=(2,~ -1)` |

#### C) 기타

| 종류 | LaTeX | hwpEQ 출력 |
|------|-------|----------|
| 선분 (화살표 없을 때) | `\overline{\mathrm{AB}}` | `{bar{rm AB}}` |
| **호** (글자 위 둥근 호 ⌒) | `\widehat{\mathrm{AB}}` | `{arch{rm AB}}` |
| 점 좌표 | `\mathrm{A}(2, 0)` | `rm{A}(2, 0)` |
| 각 | `\angle \mathrm{ABC}` | `ANGLE rm{ABC}` |
| 삼각형 | `\triangle \mathrm{ABC}` | `TRIANGLE rm{ABC}` |

**잘못된 예** (자주 발생):
- ❌ 이미지에 화살표 있는데 `\mathrm{AB}` 만 (화살표 누락) → ✅ `\overrightarrow{\mathrm{AB}}`
- ❌ `\vec{AB}` (점쌍에 `\vec`, 화살표가 짧아짐) → ✅ `\overrightarrow{\mathrm{AB}}`
- ❌ `\overrightarrow{a}` (소문자에 `\overrightarrow`) → ✅ `\vec{a}`
- ❌ `\vec a` (중괄호 없음) → ✅ `\vec{a}`
- ❌ `vec{a}` (백슬래시 누락) → ✅ `\vec{a}`
- ❌ `|\vec{a}|` (절댓값에 일반 `|`) → ✅ `\left|\vec{a}\right|`
- ❌ `A(2, 0)` (점 이름인데 `\mathrm` 누락) → ✅ `\mathrm{A}(2, 0)`

### ④ 비례식은 무조건 하나의 수식 세그먼트로

```
✅ {"type":"formula","content":"3:1"}
✅ {"type":"formula","content":"1:2:3"}

❌ {"type":"formula","content":"3"},
   {"type":"text","content":" : "},
   {"type":"formula","content":"1"}
```

절대 쪼개지 말 것.

### ⑤ 수식을 감싸는 괄호는 무조건 `\left( \right)` ⚠️ **매우 중요**

벡터·분수·근호·합·절댓값을 감쌀 때 반드시 `\left( \right)`. 일반 `()` 쓰면 한컴 수식편집기에서 크기 조정 안 됨.

```
✅ \left(\overrightarrow{\mathrm{O}_1\mathrm{P}}+\overrightarrow{\mathrm{O}_3\mathrm{Q}'}\right)
✅ \left(\dfrac{1}{2}\vec{a}-\dfrac{2}{3}\vec{b}\right)
✅ \left|\overrightarrow{\mathrm{AP}}+\overrightarrow{\mathrm{BQ}}\right|
✅ \left\{-\vec{a}+(k+1)\vec{b}\right\}

❌ (\overrightarrow{X}+\overrightarrow{Y})    ← 벡터 합인데 그냥 ()
❌ (\dfrac{1}{2}\vec{a}-\dfrac{2}{3}\vec{b})
❌ |\overrightarrow{\mathrm{AP}}|
```

**단**, 단순 정수·변수만 들어가는 경우 (예: `(2, 0)`, `f(x)`, `(k+1)`) 는 그냥 `()` 도 OK.

#### `\left / \right` 짝짓기 — 깨짐 방지 핵심

`\left` 와 `\right` 는 독립적으로 쓸 수 없음. 짝이 어긋나면 출력 전체가 깨짐.

| 구분자 | 여는 쪽 | 닫는 쪽 |
|------|---------|---------|
| 소괄호 | `\left(` | `\right)` |
| 절댓값 | `\left\|` | `\right\|` |
| 중괄호 | `\left\{` | `\right\}` |
| 대괄호 | `\left[` | `\right]` |

**규칙**:
1. `\left` 와 `\right` 는 **항상 1:1 짝**. 같은 수식 안에 같은 개수.
2. `\left` / `\right` **직후에 구분자가 즉시** 와야 함 (공백 없이): `\left|`, `\right|`
3. 짝의 종류 일치: `\left|` ↔ `\right|`, `\left(` ↔ `\right)` — 섞으면 안 됨
4. 한 수식 안에서 절댓값은 **모두** `\left|/\right|` 로 통일 — 일반 `|` 와 혼용 금지

**잘못된 패턴 (출력 깨짐)**:
```
❌ \left|\vec{a}\right + \left|\vec{b}\right|   (\right 뒤 | 빠뜨림)
❌ \left|\vec{a}\right\left|\vec{b}\right|       (right \left 사이 내용 없음)
❌ |\vec{a}|+\left|\vec{b}\right|                (혼용)
```

**올바른 패턴**:
```
✅ \left|\vec{a}\right|+\left|\vec{b}\right|+\left|\vec{c}\right|
✅ \left|\overrightarrow{\mathrm{AD}}\right|+\left|\overrightarrow{\mathrm{BE}}\right|
✅ \left|\vec{a}\right|=4+2\times\left(4-2\sqrt{2}\right)
```

#### 자가 점검 체크리스트
1. ✅ `\left` 개수 = `\right` 개수
2. ✅ `\left` / `\right` 뒤에 구분자가 공백 없이 붙어 있음
3. ✅ 짝의 종류 일치
4. ✅ 일반 `|` 와 `\left|/\right|` 혼용 안 함

### ⑥ 불필요한 LaTeX 공백 명령 금지

```
❌ \therefore\;
❌ (2,\,0)
❌ x \quad y
❌ \because\;

✅ \therefore
✅ (2, 0)
✅ \because
```

LaTeX 공백 명령 `\;`, `\,`, `\:`, `\!`, `\quad`, `\qquad` 모두 금지. 일반 공백 사용.
(후처리에서 자동으로 제거되지만, 처음부터 안 쓰는 게 안전)

### ⑦ 자동 변환되는 항목 (입력 그대로 쓰면 됨)

- `\cdots` → 자동으로 양옆에 공백 추가
- **연속 대문자 2글자+** (AB, ABC) → 자동 `\mathrm` wrap
- **단일 대문자/소문자 변수** → 자동 이탤릭

---

## ⚠️ 임시 식 번호·기호는 원본 그대로 (재번호·이어쓰기 금지)

풀이/해설에서 식을 가리키는 임시 기호 — 예: `㉠ ㉡ ㉢ ㉣`, `ⓐ ⓑ ⓒ`, `①②③`,
`(1) (2) (3)`, `가) 나) 다)` — 는 **이미지/원본에 보이는 그대로** 옮기세요.

- ❌ **문제가 바뀌었는데 직전 문제의 마지막 기호에서 이어붙이지 말 것.**
  예: 1번 풀이가 `㉠ ㉡` 까지 썼다고 2번 풀이를 `㉢` 부터 시작 → **NO**
- ✅ 각 문제는 자기 원본에 보이는 기호 그대로 (대부분 문제마다 `㉠` 부터 다시 시작).
- ✅ 같은 문제 안에서도 원본의 기호 종류·순서·표기를 그대로 유지.
- 본문·보기·풀이·해설·답 모든 영역에 공통 적용.

---

## JSON 구조 규칙

### 1. 일반 문단 (문제 본문)

```json
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

### 2. 객관식 보기 (①②③④⑤)

**각 보기는 반드시 별도 problem 엔트리**로 분리하세요. `main: false` 로 표시.

```json
[
  {
    "number": "1",
    "main": true,
    "segments": [{"type": "text", "content": "다음 중 옳은 것은?"}]
  },
  {
    "number": "1-①",
    "main": false,
    "segments": [
      {"type": "text", "content": "① "},
      {"type": "formula", "content": "-2"}
    ]
  },
  {
    "number": "1-②",
    "main": false,
    "segments": [
      {"type": "text", "content": "② "},
      {"type": "formula", "content": "-1"}
    ]
  }
]
```

### 3. 박스 (보기/조건)

- `box_type`: `"bogi"` (ㄱ/ㄴ/ㄷ) 또는 `"condition"` (㈎㈏㈐)
- `segments` 는 **2차원 배열** (줄별)

```json
{
  "number": "1-박스",
  "box": true,
  "box_type": "bogi",
  "segments": [
    [
      {"type": "text", "content": "ㄱ. "},
      {"type": "formula", "content": "f(1)>0"}
    ],
    [
      {"type": "text", "content": "ㄴ. "},
      {"type": "formula", "content": "f(2)<0"}
    ]
  ]
}
```

### 4. 그림/도형 자리표시자

```json
{"type": "image_placeholder", "description": "좌표평면 그래프"}
```

### 5. 풀이 → 미주(endnote)  ⭐ 선택

사용자가 **"풀이를 미주로"**, **"풀이를 각주/미주 처리"**, **"문제 뒤에 풀이 번호 달기"** 같이
요청할 때만 사용합니다. 풀이·해설·답을 본문에 펼치지 않고, 해당 문제 뒤에 미주 번호
`1)` `2)` … 를 달고 내용은 **문서 맨 끝**에 모아 표시합니다.

기본 엔트리 형식:
```json
{"number": "1", "role": "solution", "segments": [ ... ]}
```

#### 미주 내용 구조 ⭐ (각 문제마다 아래 순서로)

한 문제의 미주 엔트리 그룹은 **정확히 이 순서**로 배치:

**1. 첫 엔트리 = `[정답] {정답내용}`**
```json
{"number":"3","role":"solution","segments":[
    {"type":"text","content":"[정답] "},
    {"type":"formula","content":"17"}
]}
```
- 원본에 `[정답] X` / `답: X` / `정답: X` 가 있으면 그 값 사용.
- 짧은 식이면 `formula`, 단순 텍스트("참", "거짓" 등)면 `text`.
- 정답을 못 찾으면 `[{"type":"text","content":"[정답] "}]` 만 (빈 자리).

**2. 두 번째 엔트리 = `[해설]` 헤더 한 줄**
```json
{"number":"3","role":"solution","segments":[{"type":"text","content":"[해설]"}]}
```
- 정확히 `[해설]` 텍스트 한 개. 다른 내용 섞지 말 것.

**3. 세 번째 이후 = 풀이 본문 줄별로 1엔트리**
- 원본의 `[풀이]` / `풀이)` / `Sol)` 헤더는 위에서 `[해설]` 로 대체했으니 본문에 다시 적지 않음.
- 풀이 본문 각 줄을 별도 `role:"solution"` 엔트리로 (모두 같은 number).
- 줄 안의 수식은 `formula`, 한글 설명은 `text` 로 분해.

#### 기타 규칙
- `role` 은 반드시 `"solution"`.
- `number` 는 그 풀이가 **설명하는 문제 번호**와 동일하게.
- 본문/보기/박스는 기존대로 일반 엔트리. 풀이만 `role:"solution"`.
- 풀이 엔트리 그룹은 본문 엔트리들보다 **뒤에 배치**해도 number 로 자동 매칭됨.
- 사용자가 별도로 요청하지 않으면 풀이는 기존처럼 일반 문단으로 출력.

---

## 🖋️ 사용자 정의 템플릿 (시험지·학습지 디자인)

기본 `template.hwpx` 는 빈 문서지만, **사용자가 직접 디자인한 템플릿**에 문제만
원하는 위치에 끼워 넣고 싶을 때 사용합니다. (제목·학교명·학생정보·머리말·꼬리말
·로고 등을 한글에서 자유롭게 디자인 후, 문제 들어갈 자리만 표식으로 지정)

### 사용 절차 (사용자에게 안내)
1. **한글에서 `template.hwpx` 를 열기** (또는 새 .hwpx 문서 생성).
2. 원하는 디자인 적용 — 제목, 학교명, "성명: ___" 같은 줄, 머리말/꼬리말, 페이지
   번호, 여백, 글꼴 등 자유롭게.
3. **문제가 들어갈 위치에 다음 표식을 새 문단으로 입력**:
   ```
   {{문제삽입}}
   ```
   (대안: `{{문제}}`, `{{CONTENT}}` 도 인식)
4. 다른 이름으로 저장 (예: `시험지_템플릿_2026.hwpx`).
5. 변환 호출 시 이 파일을 `template_path` 로 지정.

### 동작
- 빌더가 템플릿 안에서 `{{문제삽입}}` 가 있는 문단을 찾아 그 자리에만
  생성된 문제 문단들을 끼워 넣음.
- **나머지 본문**(제목, 학생정보, 머리말, 꼬리말, 이미지, 페이지 설정)은
  그대로 유지됨.
- 표식이 없는 템플릿은 기존 동작(전체 본문 교체)으로 처리.

### 주의
- 표식은 **한 군데만** (여러 개면 첫 번째 발견 위치만 사용).
- 표식 문단에 다른 텍스트를 같이 적지 말 것 (`<{{문제삽입}}>` ❌, 단독 문단으로).
- 표식 문단 자체는 사라지고, 그 자리에 문제들이 새 문단으로 들어감.

### 다단(Multi-column) 지원
한글에서 **`쪽 → 단 → 2단/3단`** 설정한 템플릿도 그대로 작동합니다.
- 템플릿의 `<hp:colPr colCount="N">` 설정이 secPr 와 함께 보존됨.
- 변환된 문제 문단들이 **자동으로 다단 배치로 흘러들어감**.
- 매우 긴 수식은 단 폭을 넘을 수 있으니, 좁은 단(3단 이상)은 짧은 문제에 권장.

---

## 전체 워크플로우

```
[사용자가 이미지/PDF 제공]
       ↓
Claude 가 직접 이미지를 보고 내용 파악
       ↓
LaTeX 포함 JSON 구조 작성 (위 7가지 규칙 따라)
       ↓
post_process.convert_problems_to_hwpx() 호출
       ↓
HWPX 파일 생성
       ↓
사용자에게 다운로드 링크 제공
```

---

## 실행 코드 템플릿

Claude 는 이미지를 보고 `problems` 리스트를 만든 다음, 아래 코드로 HWPX 생성:

```python
import sys, shutil
from pathlib import Path

# 1. 스킬 파일들을 작업 디렉터리로 복사
skill_dir = Path("math-pdf-to-hwpx")
skill_dir.mkdir(exist_ok=True)
shutil.copy("scripts/pdf_to_hwpx.py", skill_dir / "pdf_to_hwpx.py")
shutil.copy("scripts/post_process.py", skill_dir / "post_process.py")

# 템플릿 (사용자가 업로드 안 했으면 기본 사용)
template = Path("template.hwpx")
if not template.exists():
    shutil.copy("assets/template.hwpx", template)

# 2. post_process 모듈 import
sys.path.insert(0, str(skill_dir))
from post_process import convert_problems_to_hwpx

# 3. problems 리스트 작성 (Claude 가 이미지 보고 만듦)
problems = [
    {
        "number": "1",
        "main": True,
        "segments": [
            {"type": "text", "content": "다항식 "},
            {"type": "formula", "content": "f(x)"},
            {"type": "text", "content": "의 값은?"}
        ]
    },
    # ... 나머지 문제
]

# 4. HWPX 생성
output = convert_problems_to_hwpx(
    problems=problems,
    template_path="template.hwpx",
    output_path="output.hwpx",
)
print(f"✅ HWPX 생성: {output}")
```

---

## 모드별 동작

### "문제만" 모드 (기본)

인식할 내용:
- ✅ 문항 번호 (1., 2., 23., 서답형1)
- ✅ 문제 본문 텍스트
- ✅ 객관식 보기 (①②③④⑤)
- ✅ 박스 안의 보기/조건

**제외**:
- ❌ 풀이(Solution), 해설, 답
- ❌ "풀이:", "해설:", "답:", "Sol)" 이후 내용
- ❌ 손글씨 필기
- ❌ 교사 설명 박스, 해답지

### "보이는 것 모두" 모드

- 페이지에 있는 **모든 텍스트·수식·표·손글씨** 그대로 옮겨 적음
- 풀이·해설·답·주석 모두 포함
- 원본의 순서와 구조 유지

---

## 📦 스킬 파일 구조

```
math-pdf-to-hwpx.skill/
├── SKILL.md                    (이 파일 — Claude 가 읽는 지침서)
├── scripts/
│   ├── pdf_to_hwpx.py         (HWPX 빌더)
│   └── post_process.py        (후처리 + 메인 API)
└── assets/
    └── template.hwpx          (기본 템플릿)
```

---

## 최종 체크리스트

Claude 가 HWPX 생성 전에 확인할 것:

1. ✅ 추출 범위 모드 확인 (문제만 / 모두)
2. ✅ 각 수식의 괄호 짝이 맞는지
3. ✅ **점·꼭짓점은 `\mathrm{}`** 으로 감쌌는지 (PDF 규칙 ②)
4. ✅ **도형·집합·곡선은 `\mathit{}`** 으로 감쌌는지 (PDF 규칙 ②)
5. ✅ **벡터·선분은 `\overrightarrow{\mathrm{}}`, `\overline{\mathrm{}}`** 형식인지 (PDF 규칙 ③)
6. ✅ **분수·벡터·근호 감싸는 큰 괄호는 `\left( \right)`** 인지 (PDF 규칙 ⑤)
7. ✅ **공백 명령 `\,`, `\;`, `\quad`** 등 안 썼는지 (PDF 규칙 ⑥)
8. ✅ **알파벳·숫자가 모두 formula 세그먼트**인지 (PDF 규칙 ①)
9. ✅ **비례식은 하나의 formula 로 묶었는지** (PDF 규칙 ④)
10. ✅ 객관식 보기가 별도 엔트리로 분리됐는지
11. ✅ 박스(보기/조건) 의 `box: true` + `box_type` 지정됐는지
