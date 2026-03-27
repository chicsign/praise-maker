import streamlit as st
import datetime
import os
import json
import tempfile
import requests
import subprocess
import platform
import shutil
import io

from pptx import Presentation
from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides
from pptx.util import Inches
from google.oauth2.credentials import Credentials
from streamlit_cookies_manager import EncryptedCookieManager
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# [보안] 로컬 테스트 허용
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# 1. 앱 기본 설정
st.set_page_config(page_title="Praise Maker", layout="wide")

# -------------------------------
# COOKIE MANAGER (로그인 유지용)
# -------------------------------
cookies = EncryptedCookieManager(prefix="praise_maker/", password=os.environ.get("COOKIE_SECRET", "dev-secret"))
if not cookies.ready(): st.stop()

# -------------------------------
# SESSION INIT
# -------------------------------
if "credentials" not in st.session_state: st.session_state["credentials"] = None
if "user_email" not in st.session_state: st.session_state["user_email"] = None
if "page" not in st.session_state: st.session_state["page"] = "main"
if "cart" not in st.session_state: st.session_state["cart"] = []
if "editing_song" not in st.session_state: st.session_state["editing_song"] = None
if "slide_url" not in st.session_state: st.session_state["slide_url"] = None

# -------------------------------
# OAUTH & USER INFO
# -------------------------------
def get_user_info(creds):
    try:
        service = build('oauth2', 'v2', credentials=creds)
        user_info = service.userinfo().get().execute()
        return user_info.get("email")
    except: return None

# 쿠키 복원 로직
if st.session_state["credentials"] is None:
    token, refresh = cookies.get("token"), cookies.get("refresh_token")
    if token and refresh:
        creds_dict = {
            "token": token, "refresh_token": refresh,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "scopes": ["https://www.googleapis.com/auth/presentations", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/userinfo.email"]
        }
        st.session_state["credentials"] = creds_dict
        st.session_state["user_email"] = get_user_info(Credentials(**creds_dict))

if st.query_params.get("code") and st.session_state["credentials"] is None:
    try:
        flow = create_flow()
        flow.fetch_token(code=st.query_params["code"])
        creds = flow.credentials
        creds_dict = {
            "token": creds.token, "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri, "client_id": creds.client_id,
            "client_secret": creds.client_secret, "scopes": creds.scopes
        }
        st.session_state["credentials"] = creds_dict
        st.session_state["user_email"] = get_user_info(creds)
        cookies["token"], cookies["refresh_token"] = creds.token, creds.refresh_token
        cookies.save()
        st.query_params.clear()
        st.rerun()
    except Exception as e: st.error(f"로그인 오류: {e}")

# -------------------------------
# HELPER FUNCTIONS
# -------------------------------
def get_google_credentials():
    creds_dict = st.session_state.get("credentials")
    return Credentials(**creds_dict) if creds_dict else None

def go_to_main(): 
    st.session_state.update({"page": "main", "editing_song": None})
    st.rerun()

def go_to_add(): 
    st.session_state['page'] = 'add_song'
    st.rerun()

def go_to_edit(song_data): 
    st.session_state.update({"editing_song": song_data, "page": "edit_song"})
    st.rerun()

# -------------------------------
# DIALOGS
# -------------------------------
@st.dialog("곡 삭제 확인")
def delete_confirm_dialog(song_id, title):
    st.write(f"'{title}' 곡을 삭제하시겠습니까?")
    c1, c2 = st.columns(2)
    if c1.button("삭제", type="primary", use_container_width=True):
        db.collection("songs").document(song_id).delete()
        st.rerun()
    if c2.button("취소", use_container_width=True): st.rerun()

# -------------------------------
# PAGE: ADD / EDIT (여기가 핵심!)
# -------------------------------
def show_add_edit_page(mode="add"):
    st.title("➕ 새 곡 추가" if mode == "add" else "📝 곡 정보 수정")
    
    # 수정 모드일 때 기존 데이터 불러오기
    song = st.session_state.get("editing_song", {}) if mode == "edit" else {}
    
    with st.form("song_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        title = col1.text_input("곡 제목*", value=song.get("title", ""), placeholder="곡 제목을 입력하세요")
        start_key = col2.text_input("Key", value=song.get("start_key", ""), placeholder="예: G, Ab")
        
        youtube_url = st.text_input("YouTube 링크", value=song.get("youtube_url", ""), placeholder="https://www.youtube.com/watch?v=...")
        tags_input = st.text_input("태그 (쉼표로 구분)", value=", ".join(song.get("tags", [])) if song.get("tags") else "", placeholder="예: 경배, 감사, 느린곡")
        
        st.divider()
        st.subheader("📁 파일 업로드 (Firebase Storage)")
        c3, c4 = st.columns(2)
        image_file = c3.file_uploader("악보 이미지 (JPG, PNG)", type=['jpg', 'jpeg', 'png'])
        ppt_file = c4.file_uploader("가사 PPT 파일 (PPT, PPTX)", type=['ppt', 'pptx'])
        
        submitted = st.form_submit_button("곡 저장하기", type="primary", use_container_width=True)
        
        if submitted:
            if not title:
                st.error("곡 제목은 필수입니다!")
            else:
                with st.spinner("저장 중..."):
                    try:
                        data = {
                            "title": title,
                            "start_key": start_key,
                            "youtube_url": youtube_url,
                            "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                            "created_at": datetime.datetime.now(),
                            "image_url": song.get("image_url", ""),
                            "ppt_url": song.get("ppt_url", "")
                        }

                        # 이미지 업로드 로직
                        if image_file:
                            img_blob = bucket.blob(f"songs/images/{image_file.name}")
                            img_blob.upload_from_file(image_file, content_type=image_file.type)
                            img_blob.make_public()
                            data["image_url"] = img_blob.public_url

                        # PPT 업로드 로직
                        if ppt_file:
                            ppt_blob = bucket.blob(f"songs/ppts/{ppt_file.name}")
                            ppt_blob.upload_from_file(ppt_file, content_type=ppt_file.type)
                            ppt_blob.make_public()
                            data["ppt_url"] = ppt_blob.public_url

                        if mode == "add":
                            db.collection("songs").add(data)
                        else:
                            db.collection("songs").document(song["id"]).update(data)
                        
                        st.success("저장 완료!")
                        go_to_main()
                    except Exception as e:
                        st.error(f"오류 발생: {e}")

    if st.button("⬅️ 메인으로 돌아가기", use_container_width=True):
        go_to_main()

# -------------------------------
# SIDEBAR & MAIN ROUTING
# -------------------------------
with st.sidebar:
    st.header("🔐 구글 로그인")
    if st.session_state["credentials"] is None:
        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration:none;"><div style="background-color:white; color:#757575; border-radius:4px; border:1px solid #dadce0; padding:10px; text-align:center; font-weight:500; cursor:pointer;">Google 로그인</div></a>', unsafe_allow_html=True)
    else:
        st.success(f"✅ {st.session_state['user_email']}")
        if st.button("로그아웃", use_container_width=True):
            st.session_state.update({"credentials": None, "user_email": None})
            cookies["token"] = ""; cookies["refresh_token"] = ""; cookies.save(); st.rerun()

    st.divider(); st.header("🛒 선택된 콘티")
    # ... (장바구니 로직은 기존과 동일)
    if not st.session_state['cart']: st.caption("곡을 담아주세요.")
    else:
        for idx, item in enumerate(st.session_state['cart']):
            st.write(f"{idx+1}. {item['title']}")
        if st.button("🗑️ 초기화"): st.session_state['cart'] = []; st.rerun()

# --- 페이지 라우팅 ---
if st.session_state['page'] == 'add_song':
    show_add_edit_page(mode="add")
elif st.session_state['page'] == 'edit_song':
    show_add_edit_page(mode="edit")
else:
    # 메인 페이지 (리스트 보기)
    col_t, col_a = st.columns([5, 1])
    col_t.title("🎵 Praise Maker")
    if col_a.button("곡 추가", type="primary", use_container_width=True):
        go_to_add()
    
    # 검색 및 리스트 출력 로직...
    query = st.text_input("검색", label_visibility="collapsed").strip().lower()
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()
    
    for doc in docs:
        s = doc.to_dict(); s['id'] = doc.id
        if not query or query in s.get('title','').lower():
            with st.container(border=True):
                h1, h2, h3 = st.columns([8, 1, 1])
                h1.markdown(f"### {s['title']} ({s.get('start_key','')})")
                if h2.button("📝", key=f"e_{s['id']}"): go_to_edit(s)
                if h3.button("🗑️", key=f"d_{s['id']}"): delete_confirm_dialog(s['id'], s['title'])
                
                if st.button("리스트에 담기", key=f"add_{s['id']}", use_container_width=True, type="primary"):
                    if not any(i['id'] == s['id'] for i in st.session_state['cart']):
                        st.session_state['cart'].append(s); st.rerun()
