# 1. 파이썬 환경 구축
FROM python:3.11-slim

# 2. PPT 변환용 리눅스 LibreOffice와 한글 폰트 강제 설치 (가장 중요)
RUN apt-get update && apt-get install -y \
    libreoffice \
    fonts-nanum \
    && apt-get clean

WORKDIR /app

# 3. 필요한 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 소스 코드 전체 복사
COPY . .

# 5. 실행 설정
EXPOSE 8080
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]