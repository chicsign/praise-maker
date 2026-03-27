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
for key, default in {
    "credentials": None, "page": "main", "cart": [], 
    "editing_song": None, "slide_url": None
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -------------------------------
# 쿠키 데이터를 세션으로 복원
# -------------------------------
if st.session_state["credentials"] is None:
    token, refresh = cookies.get("token"), cookies.get("refresh_token")
    if token and refresh:
        st.session_state["credentials"] = {
            "token": token, "refresh_token": refresh,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "scopes": ["https://www.googleapis.com/auth/presentations", "https://www.googleapis.com/auth/drive"]
        }

# -------------------------------
# 페이지 이동 헬퍼 함수
# -------------------------------
def go_to_main():
    st.session_state.update({"page": "main", "editing_song": None})
    st.rerun()

def go_to_add():
    st.session_state["page"] = "add_song"
    st.rerun()

def go_to_edit(song_data):
    st.session_state.update({"editing_song": song_data, "page": "edit_song"})
    st.rerun()

# -------------------------------
# 삭제 확인 다이얼로그
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
# 곡 추가 및 수정 페이지 (통합 관리)
# -------------------------------
def show_add_edit_page(mode="add"):
    st.title("찬양곡 추가" if mode == "add" else "찬양곡 수정")
    song = st.session_state.get("editing_song", {}) if mode == "edit" else {}
    
    if st.button("돌아가기"):
        go_to_main()

    with st.form("song_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        title = col1.text_input("곡 이름 *", value=song.get("title", ""))
        start_key = col2.text_input("Key", value=song.get("start_key", ""))
        
        youtube_url = st.text_input("YouTube 링크", value=song.get("youtube_url", ""))
        tags_input = st.text_input("태그 (쉼표 구분)", value=", ".join(song.get("tags", [])) if song.get("tags") else "")
        
        st.write("---")
        st.subheader("파일 업로드")
        c3, c4 = st.columns(2)
        image_file = c3.file_uploader("악보 이미지", type=["jpg","png"])
        ppt_file = c4.file_uploader("가사 PPT", type=["ppt","pptx"])

        if st.form_submit_button("저장하기", type="primary", use_container_width=True):
            if not title:
                st.error("곡 이름은 필수입니다")
            else:
                with st.spinner("저장 중..."):
                    data = {
                        "title": title, "start_key": start_key, "youtube_url": youtube_url,
                        "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                        "created_at": song.get("created_at", datetime.datetime.now()),
                        "image_url": song.get("image_url", ""), "ppt_url": song.get("ppt_url", "")
                    }
                    if image_file:
                        blob = bucket.blob(f"songs/images/{image_file.name}")
                        blob.upload_from_file(image_file, content_type=image_file.type)
                        blob.make_public(); data["image_url"] = blob.public_url
                    if ppt_file:
                        blob = bucket.blob(f"songs/ppts/{ppt_file.name}")
                        blob.upload_from_file(ppt_file, content_type=ppt_file.type)
                        blob.make_public(); data["ppt_url"] = blob.public_url

                    if mode == "add": db.collection("songs").add(data)
                    else: db.collection("songs").document(song["id"]).update(data)
                    
                    st.success("저장되었습니다")
                    go_to_main()

# -------------------------------
# 메인 페이지 및 목록 출력
# -------------------------------
if st.session_state["page"] == "add_song":
    show_add_edit_page("add")
elif st.session_state["page"] == "edit_song":
    show_add_edit_page("edit")
else:
    # 사이드바 (장바구니)
    with st.sidebar:
        st.header("계정")
        if st.session_state["credentials"] is not None:
            st.success("로그인됨")
            if st.button("로그아웃"):
                st.session_state.update({"credentials": None})
                cookies.save(); st.rerun()

        st.divider(); st.header("콘티 리스트")
        if st.session_state["cart"]:
            for idx, item in enumerate(st.session_state["cart"]):
                st.write(f"{idx+1}. {item['title']}")
            if st.button("전체 초기화"):
                st.session_state["cart"] = []; st.rerun()
        else: st.caption("곡을 담아주세요")

    # 메인 목록
    t1, t2 = st.columns([5,1])
    t1.title("Praise Maker")
    if t2.button("찬양곡 추가", type="primary", use_container_width=True):
        go_to_add()

    query = st.text_input("검색", placeholder="제목 또는 태그 검색", label_visibility="collapsed").strip().lower()
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()

    btn_style = "display:flex; align-items:center; justify-content:center; background-color:#F0F2F6; color:#262730; padding:5px; border-radius:5px; text-decoration:none; font-size:13px; border:1px solid #E6E9EF;"

    for doc in docs:
        s = doc.to_dict(); s["id"] = doc.id
        if not query or query in s.get("title","").lower() or any(query in t.lower() for t in s.get("tags", [])):
            with st.container(border=True):
                # 제목, 수정, 삭제 버튼 배치
                h1, h2, h3 = st.columns([8, 1, 1])
                h1.markdown(f"### {s['title']} ({s.get('start_key','')})")
                if h2.button("수정", key=f"edit_{s['id']}", use_container_width=True):
                    go_to_edit(s)
                if h3.button("삭제", key=f"del_{s['id']}", use_container_width=True):
                    delete_confirm_dialog(s['id'], s['title'])
                
                if s.get("tags"):
                    st.markdown(" ".join([f"`#{t}`" for t in s.get("tags", [])]))
                
                # 링크 버튼
                l1, l2, l3 = st.columns(3)
                if s.get("youtube_url"): l1.markdown(f'<a href="{s["youtube_url"]}" target="_blank" style="{btn_style}">YouTube</a>', unsafe_allow_html=True)
                if s.get("image_url"): l2.markdown(f'<a href="{s["image_url"]}" target="_blank" style="{btn_style}">악보 이미지</a>', unsafe_allow_html=True)
                if s.get("ppt_url"): l3.markdown(f'<a href="{s["ppt_url"]}" target="_blank" style="{btn_style}">가사 PPT</a>', unsafe_allow_html=True)

                if st.button("리스트에 담기", key=f"add_{s['id']}", use_container_width=True, type="primary"):
                    if not any(i["id"] == s["id"] for i in st.session_state["cart"]):
                        st.session_state["cart"].append(s); st.rerun()
