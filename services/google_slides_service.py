import os
import datetime
import streamlit as st  # Flask 대신 Streamlit 사용
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive"
]

# Client Config (환경변수 기반)
CLIENT_CONFIG = {
    "web": {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"
    }
}

REDIRECT_URI = os.environ["OAUTH_REDIRECT_URI"]
USER_EMAIL = os.environ["USER_EMAIL"]
FOLDER_ID = os.environ["FOLDER_ID"]

def create_flow():
    # .env에서 공백 없이 잘 가져오는지 확인 필수
    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI", "http://localhost:8501").strip()
    
    return Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

def get_credentials():
    """Streamlit 세션 상태에서 인증 정보를 가져옴"""
    if "credentials" not in st.session_state:
        return None

    creds_data = st.session_state["credentials"]
    return Credentials(
        token=creds_data["token"],
        refresh_token=creds_data["refresh_token"],
        token_uri=creds_data["token_uri"],
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
        scopes=creds_data["scopes"]
    )

def create_praise_slides(cart_items):
    creds = get_credentials()

    if not creds:
        st.error("로그인이 필요합니다.")
        return None

    # static_discovery=False를 추가하여 에러 방지
    slides_service = build("slides", "v1", credentials=creds, static_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, static_discovery=False)

    now = datetime.datetime.now().strftime("%Y%m%d_%H%M")

    # 1. 구글 드라이브에 파일 생성
    file_metadata = {
        "name": f"콘티_{now}",
        "mimeType": "application/vnd.google-apps.presentation",
        "parents": [FOLDER_ID] if FOLDER_ID else []
    }
    
    file = drive_service.files().create(body=file_metadata, fields="id").execute()
    presentation_id = file["id"]

    # 2. 권한 부여 (필요 시)
    drive_service.permissions().create(
        fileId=presentation_id,
        body={"type": "user", "role": "writer", "emailAddress": USER_EMAIL}
    ).execute()

    # 3. 슬라이드 작업 (기존 로직 동일)
    requests = []
    for idx, item in enumerate(cart_items):
        image_url = item.get("image_url")
        if not image_url: continue
        
        page_id = f"slide_p_{idx}_{datetime.datetime.now().microsecond}"
        requests.append({
            "createSlide": {
                "objectId": page_id,
                "insertionIndex": str(idx),
                "slideLayoutReference": {"predefinedLayout": "BLANK"}
            }
        })
        requests.append({
            "createImage": {
                "url": image_url,
                "elementProperties": {
                    "pageObjectId": page_id,
                    "size": {"width": {"magnitude": 720, "unit": "PT"}, "height": {"magnitude": 405, "unit": "PT"}},
                    "transform": {"scaleX": 1, "scaleY": 1, "translateX": 0, "translateY": 0, "unit": "PT"}
                }
            }
        })

    if requests:
        slides_service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests}
        ).execute()

    return f"https://docs.google.com/presentation/d/{presentation_id}"