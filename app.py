import streamlit as st
import datetime
import os

from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides
from google.oauth2.credentials import Credentials
from streamlit_cookies_manager import EncryptedCookieManager

# 로컬 테스트 시 OAuth 보안 연결 허용 설정
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

st.set_page_config(page_title="Praise Maker", layout="wide")

# -------------------------------
# 쿠키 매니저 설정 (로그인 세션 유지)
# -------------------------------
cookies = EncryptedCookieManager(
    prefix="praise_maker/",
    password=os.environ.get("COOKIE_SECRET", "dev-secret")
)
if not cookies.ready():
    st.stop()

# -------------------------------
# 세션 상태 초기화
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
# 쿠키 데이터를 세션으로 복원
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
# 구글 로그인 인증 콜백 처리
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
# 페이지 이동 헬퍼 함수
# -------------------------------
def go_to_main():
    st.session_state["page"] = "main"
    st.rerun()

def go_to_add():
    st.session_state["page"] = "add_song"
    st.rerun()

# -------------------------------
# 찬양곡 추가 페이지 (태그 및 파일 업로드 포함)
# -------------------------------
def show_add_song_page():
    st.title("찬양곡 추가")
    
    if st.button("돌아가기"):
        go_to_main()

    with st.form("add_song_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        title = col1.text_input("곡 이름 *", placeholder="곡 제목을 입력하세요")
        start_key = col2.text_input("Key", placeholder="예: G, Ab")
        
        youtube_url = st.text_input("YouTube 링크", placeholder="주소를 입력하세요")
        tags_input = st.text_input("태그 (쉼표로 구분)", placeholder="예: 경배, 감사, 빠른곡")
        
        st.write("---")
        st.subheader("파일 업로드")
        c3, c4 = st.columns(2)
        image_file = c3.file_uploader("악보 이미지 (JPG, PNG)", type=["jpg","jpeg","png"])
        ppt_file = c4.file_uploader("가사 PPT (PPTX)", type=["ppt","pptx"])

        submitted = st.form_submit_button("저장하기", type="primary", use_container_width=True)

        if submitted:
            if not title:
                st.error("곡 이름은 필수입니다")
            else:
                with st.spinner("데이터 저장 중..."):
                    try:
                        # Firestore 저장용 데이터 구조 생성
                        data = {
                            "title": title,
                            "start_key": start_key,
                            "youtube_url": youtube_url,
                            "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                            "created_at": datetime.datetime.now(),
                            "image_url": "",
                            "ppt_url": ""
                        }

                        # Firebase Storage에 이미지 파일 업로드 및 공개 URL 생성
                        if image_file:
                            blob = bucket.blob(f"songs/images/{datetime.datetime.now().strftime('%H%M%S')}_{image_file.name}")
                            blob.upload_from_file(image_file, content_type=image_file.type)
                            blob.make_public()
                            data["image_url"] = blob.public_url

                        # Firebase Storage에 PPT 파일 업로드 및 공개 URL 생성
                        if ppt_file:
                            blob = bucket.blob(f"songs/ppts/{datetime.datetime.now().strftime('%H%M%S')}_{ppt_file.name}")
                            blob.upload_from_file(ppt_file, content_type=ppt_file.type)
                            blob.make_public()
                            data["ppt_url"] = blob.public_url

                        # Firestore 데이터베이스에 최종 데이터 추가
                        db.collection("songs").add(data)
                        st.success("곡이 추가되었습니다")
                        st.session_state["page"] = "main"
                        st.rerun()
                    except Exception as e:
                        st.error(f"저장 실패: {e}")

# -------------------------------
# 메인 페이지 및 찬양곡 목록 출력
# -------------------------------
if st.session_state["page"] == "add_song":
    show_add_song_page()
else:
    # 사이드바 설정 (로그인 및 장바구니)
    with st.sidebar:
        st.header("계정")
        if st.session_state["credentials"] is None:
            flow = create_flow()
            auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration:none;"><div style="background-color:white; color:#757575; border-radius:4px; border:1px solid #dadce0; padding:10px; text-align:center; font-weight:500;">Google 로그인</div></a>', unsafe_allow_html=True)
        else:
            st.success("로그인 유지 중")
            if st.button("로그아웃"):
                st.session_state["credentials"] = None
                cookies["token"] = ""; cookies["refresh_token"] = ""; cookies.save(); st.rerun()

        st.divider(); st.header("콘티 리스트")
        if not st.session_state["cart"]:
            st.caption("곡을 담아주세요")
        else:
            for idx, item in enumerate(st.session_state["cart"]):
                st.write(f"{idx+1}. {item['title']}")
            if st.button("전체 초기화"):
                st.session_state["cart"] = []; st.rerun()

    # 메인 페이지 헤더 및 검색창
    col_t, col_a = st.columns([5,1])
    col_t.title("Praise Maker")
    if col_a.button("찬양곡 추가", type="primary", use_container_width=True):
        go_to_add()

    query = st.text_input("검색", placeholder="곡 제목 또는 태그로 검색", label_visibility="collapsed").strip().lower()

    # Firestore에서 모든 곡 데이터를 최신순으로 가져옴
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()

    # 외부 링크 버튼 스타일링
    btn_style = "display:flex; align-items:center; justify-content:center; background-color:#F0F2F6; color:#262730; padding:5px; border-radius:5px; text-decoration:none; font-size:14px; border:1px solid #E6E9EF; margin-bottom:5px;"

    for doc in docs:
        s = doc.to_dict()
        s["id"] = doc.id
        
        # 검색 조건 (제목 또는 태그 포함 여부 확인)
        match_title = query in s.get("title","").lower()
        match_tags = any(query in t.lower() for t in s.get("tags", []))
        
        if not query or match_title or match_tags:
            with st.container(border=True):
                h1, h2 = st.columns([8, 2])
                h1.markdown(f"### {s['title']}")
                h2.write(f"Key: {s.get('start_key','')}")
                
                # 등록된 태그 표시
                if s.get("tags"):
                    st.markdown(" ".join([f"`#{t}`" for t in s.get("tags", [])]))
                
                # 파일 및 유튜브 링크 버튼 출력
                l_col1, l_col2, l_col3 = st.columns(3)
                if s.get("youtube_url"):
                    l_col1.markdown(f'<a href="{s["youtube_url"]}" target="_blank" style="{btn_style}">YouTube</a>', unsafe_allow_html=True)
                if s.get("image_url"):
                    l_col2.markdown(f'<a href="{s["image_url"]}" target="_blank" style="{btn_style}">악보 이미지</a>', unsafe_allow_html=True)
                if s.get("ppt_url"):
                    l_col3.markdown(f'<a href="{s["ppt_url"]}" target="_blank" style="{btn_style}">가사 PPT</a>', unsafe_allow_html=True)

                # 선택한 곡을 장바구니(콘티 리스트)에 담는 버튼
                if st.button("리스트에 담기", key=f"main_add_{s['id']}", use_container_width=True, type="secondary"):
                    if not any(i["id"] == s["id"] for i in st.session_state["cart"]):
                        st.session_state["cart"].append(s)
                        st.rerun()
