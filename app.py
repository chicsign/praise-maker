import streamlit as st
import datetime
import os

from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides
from google.oauth2.credentials import Credentials
from streamlit_cookies_manager import EncryptedCookieManager

# [보안] 로컬 테스트 허용
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

st.set_page_config(page_title="Praise Maker", layout="wide")

# -------------------------------
# COOKIE MANAGER
# -------------------------------
cookies = EncryptedCookieManager(
    prefix="praise_maker/",
    password=os.environ.get("COOKIE_SECRET", "dev-secret")
)
if not cookies.ready():
    st.stop()

# -------------------------------
# SESSION INIT
# -------------------------------
if "credentials" not in st.session_state:
    st.session_state["credentials"] = None
if "page" not in st.session_state:
    st.session_state["page"] = "main"
if "cart" not in st.session_state:
    st.session_state["cart"] = []
if "slide_url" not in st.session_state:
    st.session_state["slide_url"] = None

# -------------------------------
# COOKIE → SESSION 복원
# -------------------------------
if st.session_state["credentials"] is None:
    token = cookies.get("token")
    refresh = cookies.get("refresh_token")
    if token and refresh:
        st.session_state["credentials"] = {
            "token": token,
            "refresh_token": refresh,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "scopes": ["https://www.googleapis.com/auth/presentations", "https://www.googleapis.com/auth/drive"]
        }

# -------------------------------
# OAUTH CALLBACK
# -------------------------------
if st.query_params.get("code") and st.session_state["credentials"] is None:
    try:
        flow = create_flow()
        flow.fetch_token(code=st.query_params["code"])
        creds = flow.credentials
        st.session_state["credentials"] = {
            "token": creds.token, "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri, "client_id": creds.client_id,
            "client_secret": creds.client_secret, "scopes": creds.scopes
        }
        cookies["token"], cookies["refresh_token"] = creds.token, creds.refresh_token
        cookies.save()
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"로그인 오류: {e}")

# -------------------------------
# HELPER FUNCTIONS
# -------------------------------
def go_to_main():
    st.session_state["page"] = "main"
    st.rerun()

def go_to_add():
    st.session_state["page"] = "add_song"
    st.rerun()

# -------------------------------
# PAGE: ADD SONG (곡 추가 화면)
# -------------------------------
def show_add_song_page():
    st.title("➕ 새 곡 추가")
    
    if st.button("⬅️ 돌아가기"):
        go_to_main()

    with st.form("add_song_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        title = col1.text_input("곡 이름 *", placeholder="곡 제목을 입력하세요")
        start_key = col2.text_input("Key", placeholder="예: G, Ab")
        
        youtube_url = st.text_input("YouTube 링크", placeholder="https://www.youtube.com/watch?v=...")
        
        st.write("---")
        st.subheader("📁 파일 업로드")
        image_file = st.file_uploader("악보 이미지 (JPG, PNG)", type=["jpg","jpeg","png"])
        ppt_file = st.file_uploader("가사 PPT (PPTX)", type=["ppt","pptx"])

        submitted = st.form_submit_button("저장하기", type="primary", use_container_width=True)

        if submitted:
            if not title:
                st.error("곡 제목은 필수입니다!")
            else:
                with st.spinner("Firebase 저장 중..."):
                    try:
                        data = {
                            "title": title,
                            "start_key": start_key,
                            "youtube_url": youtube_url,
                            "tags": [],
                            "created_at": datetime.datetime.now(),
                            "image_url": "",
                            "ppt_url": ""
                        }

                        if image_file:
                            blob = bucket.blob(f"songs/images/{datetime.datetime.now().strftime('%H%M%S')}_{image_file.name}")
                            blob.upload_from_file(image_file, content_type=image_file.type)
                            blob.make_public()
                            data["image_url"] = blob.public_url

                        if ppt_file:
                            blob = bucket.blob(f"songs/ppts/{datetime.datetime.now().strftime('%H%M%S')}_{ppt_file.name}")
                            blob.upload_from_file(ppt_file, content_type=ppt_file.type)
                            blob.make_public()
                            data["ppt_url"] = blob.public_url

                        db.collection("songs").add(data)
                        st.success("✅ 곡이 추가되었습니다!")
                        # 성공 후 메인으로 이동
                        st.session_state["page"] = "main"
                        st.rerun()
                    except Exception as e:
                        st.error(f"저장 실패: {e}")

# -------------------------------
# SIDEBAR
# -------------------------------
with st.sidebar:
    st.header("🔐 계정")
    if st.session_state["credentials"] is None:
        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration:none;"><div style="background-color:white; color:#757575; border-radius:4px; border:1px solid #dadce0; padding:10px; text-align:center; font-weight:500;">Google 로그인</div></a>', unsafe_allow_html=True)
    else:
        st.success("로그인 유지 중")
        if st.button("로그아웃"):
            st.session_state["credentials"] = None
            cookies["token"] = ""; cookies["refresh_token"] = ""; cookies.save(); st.rerun()

    st.divider(); st.header("🛒 콘티 리스트")
    if not st.session_state["cart"]:
        st.caption("곡을 담아주세요.")
    else:
        for idx, item in enumerate(st.session_state["cart"]):
            st.write(f"{idx+1}. {item['title']}")
        if st.button("🗑️ 전체 초기화"):
            st.session_state["cart"] = []; st.rerun()

# -------------------------------
# MAIN ROUTING (여기가 중요!)
# -------------------------------
# 1. 곡 추가 페이지인 경우
if st.session_state["page"] == "add_song":
    show_add_song_page()

# 2. 메인 페이지인 경우 (명시적 else 처리)
else:
    col_t, col_a = st.columns([5,1])
    col_t.title("🎵 Praise Maker")

    if col_a.button("곡 추가", type="primary", use_container_width=True):
        go_to_add()

    query = st.text_input("검색", placeholder="곡 제목으로 검색", label_visibility="collapsed").strip().lower()

    # Firestore에서 최신순으로 가져오기
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()

    for doc in docs:
        s = doc.to_dict()
        s["id"] = doc.id
        
        # 검색 필터링
        if not query or query in s.get("title","").lower():
            with st.container(border=True):
                h1, h2 = st.columns([8, 2])
                h1.markdown(f"### {s['title']}")
                h2.write(f"Key: {s.get('start_key','')}")

                if st.button("리스트에 담기", key=f"add_{s['id']}", use_container_width=True):
                    if not any(i["id"] == s["id"] for i in st.session_state["cart"]):
                        st.session_state["cart"].append(s)
                        st.rerun()
