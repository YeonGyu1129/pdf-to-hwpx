#!/bin/bash
# 맥에서 Finder 더블클릭으로 Streamlit 앱 실행

# 이 스크립트가 있는 폴더로 이동
cd "$(dirname "$0")"

# 가상환경 활성화
source .venv/bin/activate

# API 키 로드 (.env 파일이 있으면 읽음)
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# API 키 확인
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "❌ ANTHROPIC_API_KEY 가 설정되지 않았습니다."
    echo "   같은 폴더에 .env 파일을 만들고 다음 줄을 넣으세요:"
    echo "   ANTHROPIC_API_KEY=sk-ant-..."
    read -p "계속하려면 엔터를 누르세요..."
    exit 1
fi

# Streamlit 실행
streamlit run app.py
