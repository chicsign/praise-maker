# 1. 파이썬 환경 설정
FROM python:3.11-slim

# 2. 필수 시스템 도구 설치 (LibreOffice 및 한글 폰트)
# 이 과정이 있어야 서버에 'soffice' 명령어가 생성됩니다.
RUN apt-get update && apt-get install -y \
    libreoffice \
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