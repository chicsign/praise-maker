# 1. 파이썬 환경 설정 (가벼운 slim 버전 유지)
FROM python:3.11-slim

# 2. 필수 시스템 도구 설치 (최소화)
# LibreOffice를 삭제하고, 혹시 모를 한글 처리를 위한 폰트만 남기거나 아예 비워도 됩니다.
# 현재는 구글 API를 쓰므로 fonts-nanum도 사실 필수는 아니지만, 안전을 위해 폰트만 남겨둡니다.
RUN apt-get update && apt-get install -y \
    fonts-nanum \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. 작업 디렉토리 설정
WORKDIR /app

# 4. 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 소스 코드 복사
COPY . .

# 6. 실행 설정 (Port 8080)
EXPOSE 8080
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
