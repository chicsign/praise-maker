import streamlit as st
import datetime
import os

from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides
from google.oauth2.credentials import Credentials
from streamlit_cookies_manager import EncryptedCookieManager

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

st.set_page_config(page_title="Praise Maker", layout="wide")

# -------------------------------
# COOKIE
# -------------------------------
cookies = EncryptedCookieManager(
    prefix="praise_maker/",
    password=os.environ.get("COOKIE_SECRET","dev-secret")
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
# COOKIE → SESSION
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
            "scopes": [
                "https://www.googleapis.com/auth/presentations",
                "https://www.googleapis.com/auth/drive"
            ]
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
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes
        }

        cookies["token"] = creds.token
        cookies["refresh_token"] = creds.refresh_token
        cookies.save()

        st.query_params.clear()
        st.rerun()

    except Exception as e:
        st.error(f"로그인 오류: {e}")

# -------------------------------
# HELPER
# -------------------------------
def get_google_credentials():
    if st.session_state.get("credentials") is None:
        return None
    return Credentials(**st.session_state["credentials"])

def go_to_main():
    st.session_state["page"] = "main"
    st.rerun()

def go_to_add():
    st.session_state["page"] = "add_song"
    st.rerun()

# -------------------------------
# 곡 추가 페이지
# -------------------------------
def show_add_song_page():
    st.title("➕ 곡 추가")

    if st.button("⬅️ 뒤로가기"):
        go_to_main()

    with st.form("add_song_form"):

        title = st.text_input("곡 이름 *")
        start_key = st.text_input("Key (예: G)")
        youtube_url = st.text_input("YouTube 링크")

        image_file = st.file_uploader("악보 이미지", type=["jpg","jpeg","png"])
        ppt_file = st.file_uploader("악보 PPT", type=["ppt","pptx"])

        submitted = st.form_submit_button("저장하기", type="primary")

        if submitted:
            if not title:
                st.error("곡 이름은 필수입니다")
                return

            try:
                data = {
                    "title": title,
                    "start_key": start_key,
                    "youtube_url": youtube_url,
                    "tags": [],
                    "created_at": datetime.datetime.now()
                }

                # 이미지 업로드
                if image_file:
                    blob = bucket.blob(f"songs/images/{title}_{image_file.name}")
                    blob.upload_from_file(image_file, content_type=image_file.type)
                    blob.make_public()
                    data["image_url"] = blob.public_url

                # PPT 업로드
                if ppt_file:
                    blob = bucket.blob(f"songs/ppts/{title}_{ppt_file.name}")
                    blob.upload_from_file(ppt_file, content_type=ppt_file.type)
                    blob.make_public()
                    data["ppt_url"] = blob.public_url

                db.collection("songs").add(data)

                st.success("곡 추가 완료!")
                go_to_main()

            except Exception as e:
                st.error(f"실패: {e}")

# -------------------------------
# SIDEBAR
# -------------------------------
with st.sidebar:
    st.header("🔐 구글 로그인")

    if st.session_state["credentials"] is None:
        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

        st.markdown(f"""
        <a href="{auth_url}" target="_self">
            <div style="background:#fff;border:1px solid #ddd;padding:10px;text-align:center;">
            Google 로그인
            </div>
        </a>
        """, unsafe_allow_html=True)

    else:
        st.success("로그인됨")
        if st.button("로그아웃"):
            st.session_state["credentials"] = None
            cookies["token"] = ""
            cookies["refresh_token"] = ""
            cookies.save()
            st.rerun()

    st.divider()
    st.header("🛒 콘티")

    for idx, item in enumerate(st.session_state["cart"]):
        st.write(f"{idx+1}. {item['title']}")

# -------------------------------
# ROUTING
# -------------------------------
if st.session_state["page"] == "add_song":
    show_add_song_page()

else:
    # -------------------------------
    # MAIN
    # -------------------------------
    col_t, col_a = st.columns([5,1])
    col_t.title("🎵 Praise Maker")

    if col_a.button("곡 추가", type="primary", use_container_width=True):
        go_to_add()

    query = st.text_input("검색", label_visibility="collapsed")

    docs = db.collection("songs").stream()

    for doc in docs:
        s = doc.to_dict()
        s["id"] = doc.id

        if not query or query in s.get("title",""):

            with st.container(border=True):

                st.markdown(f"### {s['title']}")
                st.write(s.get("start_key",""))

                if st.button("리스트에 담기", key=s["id"]):
                    if not any(i["id"] == s["id"] for i in st.session_state["cart"]):
                        st.session_state["cart"].append(s)
                        st.rerun()
