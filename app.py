import streamlit as st
import datetime
import os
import json

from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides

from google.oauth2.credentials import Credentials
from streamlit_cookies_manager import EncryptedCookieManager


os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

st.set_page_config(page_title="Praise Maker", layout="wide")


# -------------------------------
# COOKIE MANAGER
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

if 'page' not in st.session_state:
    st.session_state['page'] = 'main'

if 'cart' not in st.session_state:
    st.session_state['cart'] = []

if 'slide_url' not in st.session_state:
    st.session_state['slide_url'] = None


# -------------------------------
# COOKIE -> SESSION 복원
# -------------------------------

if st.session_state["credentials"] is None:

    token = cookies.get("token")
    refresh = cookies.get("refresh_token")

    if token and refresh:

        creds = Credentials(
            token=token,
            refresh_token=refresh,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            scopes=[
                "https://www.googleapis.com/auth/presentations",
                "https://www.googleapis.com/auth/drive"
            ]
        )

        st.session_state["credentials"] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes
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

        st.error(f"로그인 처리 오류: {e}")


# -------------------------------
# CREDENTIAL 객체 생성
# -------------------------------

def get_google_credentials():

    if st.session_state.get("credentials") is None:
        return None

    return Credentials(
        token=st.session_state["credentials"]["token"],
        refresh_token=st.session_state["credentials"]["refresh_token"],
        token_uri=st.session_state["credentials"]["token_uri"],
        client_id=st.session_state["credentials"]["client_id"],
        client_secret=st.session_state["credentials"]["client_secret"],
        scopes=st.session_state["credentials"]["scopes"]
    )


# -------------------------------
# NAV FUNCTIONS
# -------------------------------

def go_to_main():
    st.session_state.update({"page": "main", "editing_song": None})


def go_to_add():
    st.session_state['page'] = 'add_song'


def go_to_edit(song_data):
    st.session_state.update({"editing_song": song_data, "page": "edit_song"})
    st.rerun()


# -------------------------------
# DELETE MODAL
# -------------------------------

@st.dialog("곡 삭제 확인")
def delete_confirm_dialog(song_id, title):

    st.write(f"'{title}' 곡을 삭제하시겠습니까?")

    c1, c2 = st.columns(2)

    if c1.button("삭제", type="primary", use_container_width=True):
        db.collection("songs").document(song_id).delete()
        st.rerun()

    if c2.button("취소", use_container_width=True):
        st.rerun()


# -------------------------------
# SIDEBAR
# -------------------------------

with st.sidebar:

    st.header("🔐 구글 로그인")

    if st.session_state["credentials"] is None:

        st.warning("콘티 생성시 로그인이 필요합니다.")

        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

        st.markdown(f"""
        <a href="{auth_url}" target="_self" style="text-decoration: none;">
            <div style="
                display: flex;
                align-items: center;
                justify-content: center;
                background-color: #ffffff;
                color: #757575;
                border-radius: 4px;
                border: 1px solid #dadce0;
                padding: 10px 15px;
                font-family: 'Roboto', arial, sans-serif;
                font-weight: 500;
                cursor: pointer;
                transition: background-color .2s, box-shadow .2s;
                box-shadow: 0 1px 1px 0 rgba(66,133,244,.15);
                margin-bottom: 10px;
            " onmouseover="this.style.backgroundColor='#f8f9fa'; this.style.boxShadow='0 1px 3px 1px rgba(66,133,244,.3)';" 
               onmouseout="this.style.backgroundColor='#ffffff'; this.style.boxShadow='0 1px 1px 0 rgba(66,133,244,.15)';"
            >
                <img src="https://fonts.gstatic.com/s/i/productlogos/googleg/v6/24px.svg" width="20px" height="20px" style="margin-right: 12px;">
                Google 계정으로 로그인
            </div>
        </a>
        """, unsafe_allow_html=True)

    else:

        st.success("✅ 로그인 유지됨")

        if st.button("로그아웃", use_container_width=True):

            st.session_state["credentials"] = None
            st.session_state["slide_url"] = None

            cookies["token"] = ""
            cookies["refresh_token"] = ""

            cookies.save()

            st.rerun()


    st.divider()

    st.header("🛒 선택된 콘티")


    if st.session_state['slide_url']:

        st.success("🎉 콘티 생성 성공")

        st.link_button(
            "📂 슬라이드 열기",
            st.session_state['slide_url'],
            use_container_width=True
        )

        if st.button("비우기", use_container_width=True):
            st.session_state['slide_url'] = None
            st.rerun()


    if st.session_state['cart']:

        default_filename = f"찬양콘티_{datetime.datetime.now().strftime('%y%m%d')}"

        custom_filename = st.text_input(
            "📄 생성될 파일명",
            value=default_filename
        )

        for idx, item in enumerate(st.session_state['cart']):

            with st.container(border=True):

                head1, head2 = st.columns([4, 1])

                head1.write(f"**{idx+1}. {item['title']}**")

                if head2.button("X", key=f"cart_del_{idx}"):

                    st.session_state['cart'].pop(idx)
                    st.rerun()


        st.write("---")

        if st.button("✨ 콘티 생성", type="primary", use_container_width=True):

            creds = get_google_credentials()

            with st.spinner("슬라이드 생성 중..."):

                try:

                    slide_url = create_praise_slides(
                        st.session_state['cart'],
                        custom_filename,
                        creds
                    )

                    st.session_state['cart'] = []
                    st.session_state['slide_url'] = slide_url

                    st.rerun()

                except Exception as e:

                    st.error(f"실패: {e}")

    else:

        st.caption("곡을 담아주세요.")


# -------------------------------
# MAIN PAGE
# -------------------------------

col_t, col_a = st.columns([5, 1])

col_t.title("🎵 Praise Maker")

if col_a.button("곡 추가", type="primary", use_container_width=True):

    go_to_add()
    st.rerun()


query = st.text_input(
    "search",
    placeholder="제목, 태그, 키 검색",
    label_visibility="collapsed"
).strip().lower()


docs = db.collection("songs") \
    .order_by("created_at", direction="DESCENDING") \
    .limit(50) \
    .stream()


for doc in docs:

    s = doc.to_dict()
    s['id'] = doc.id

    if not query or \
       query in s.get('title','').lower() or \
       query in s.get('start_key','').lower() or \
       any(query in t.lower() for t in s.get('tags', [])):

        with st.container(border=True):

            st.markdown(f"### {s['title']}")

            st.markdown(
                f"**Key:** {s['start_key']} | "
                + " ".join([f"`#{t}`" for t in s.get('tags', [])])
            )

            l1, l2, l3 = st.columns(3)
            button_style = """
            display:flex;
            align-items:center;
            justify-content:center;
            background-color:#F0F2F6;
            color:#262730;
            padding:8px;
            border-radius:6px;
            text-decoration:none;
            font-weight:600;
            border:1px solid #E6E9EF;
            height:38px;
            """

            if s.get("youtube_url"):
                l1.markdown(f"""
                <a href="{s["youtube_url"]}" target="_blank" style="{button_style}">
                <img src="https://upload.wikimedia.org/wikipedia/commons/7/75/YouTube_social_white_squircle_%282017%29.svg"
                width="18" style="margin-right:8px;">
                YouTube
                </a>
                """, unsafe_allow_html=True)

            if s.get("image_url"):
                l2.markdown(f"""
                <a href="{s["image_url"]}" target="_blank" style="{button_style}">
                악보
                </a>
                """, unsafe_allow_html=True)

            if s.get("ppt_url"):
                l3.markdown(f"""
                <a href="{s["ppt_url"]}" target="_blank" style="{button_style}">
                PPTX
                </a>
                """, unsafe_allow_html=True)
            
            if st.button("리스트에 담기", key=f"c_{s['id']}", use_container_width=True, type="primary"):
                if not any(item['id'] == s['id'] for item in st.session_state['cart']):
                    st.session_state['cart'].append(s)
                    st.rerun()