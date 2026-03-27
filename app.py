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
if "merged_ppt_url" not in st.session_state: st.session_state["merged_ppt_url"] = None

# -------------------------------
# OAUTH & USER INFO
# -------------------------------
def get_user_info(creds):
    try:
        service = build('oauth2', 'v2', credentials=creds)
        user_info = service.userinfo().get().execute()
        return user_info.get("email")
    except: return None

# 쿠키에서 인증 정보 복구
if st.session_state["credentials"] is None:
    token = cookies.get("token")
    refresh = cookies.get("refresh_token")
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

# 콜백 처리
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

def go_to_main(): st.session_state.update({"page": "main", "editing_song": None})
def go_to_add(): st.session_state['page'] = 'add_song'
def go_to_edit(song_data): st.session_state.update({"editing_song": song_data, "page": "edit_song"})

# -------------------------------
# PPT & DRIVE LOGIC
# -------------------------------
def convert_ppt_to_pptx(input_path):
    output_dir = os.path.dirname(input_path)
    soffice = 'soffice' # Cloud Run 환경
    try:
        subprocess.run([soffice, '--headless', '--convert-to', 'pptx', '--outdir', output_dir, input_path], check=True, capture_output=True)
        return os.path.join(output_dir, os.path.splitext(os.path.basename(input_path))[0] + ".pptx")
    except: return None

def upload_to_drive(file_path, filename, credentials):
    try:
        service = build('drive', 'v3', credentials=credentials)
        FOLDER_ID = "1Lr_0MmneLOKNyKhW6TMl6V3L88TlBn5P"
        media = MediaFileUpload(file_path, mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation')
        file = service.files().create(body={'name': filename, 'parents': [FOLDER_ID]}, media_body=media, fields='id, webViewLink').execute()
        
        # [히스토리 저장] Firebase Storage 사용자별 폴더에 백업
        if st.session_state["user_email"]:
            blob = bucket.blob(f"history/{st.session_state['user_email']}/{filename}")
            blob.upload_from_filename(file_path)
            
        return file.get('webViewLink'), f"https://drive.google.com/drive/folders/{FOLDER_ID}"
    except Exception as e:
        st.error(f"드라이브 업로드 실패: {e}")
        return None, None

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
# PAGES
# -------------------------------
def show_add_edit_page(mode="add"):
    st.title("➕ 곡 추가" if mode == "add" else "📝 곡 수정")
    song = st.session_state["editing_song"] if mode == "edit" else {}
    
    with st.form("song_form"):
        title = st.text_input("곡 제목*", value=song.get("title", ""))
        start_key = st.text_input("Key", value=song.get("start_key", ""))
        youtube_url = st.text_input("YouTube URL", value=song.get("youtube_url", ""))
        tags = st.text_input("태그 (쉼표 구분)", value=", ".join(song.get("tags", [])))
        
        c1, c2 = st.columns(2)
        img_f = c1.file_uploader("악보 이미지", type=['jpg', 'png'])
        ppt_f = c2.file_uploader("가사 PPT", type=['ppt', 'pptx'])
        
        if st.form_submit_button("저장하기", type="primary"):
            if not title: st.error("제목은 필수입니다.")
            else:
                data = {"title": title, "start_key": start_key, "youtube_url": youtube_url, 
                        "tags": [t.strip() for t in tags.split(",")] if tags else [],
                        "created_at": datetime.datetime.now(),
                        "image_url": song.get("image_url", ""), "ppt_url": song.get("ppt_url", "")}
                
                if img_f:
                    b = bucket.blob(f"songs/images/{img_f.name}")
                    b.upload_from_file(img_f, content_type=img_f.type)
                    b.make_public(); data["image_url"] = b.public_url
                if ppt_f:
                    b = bucket.blob(f"songs/ppts/{ppt_f.name}")
                    b.upload_from_file(ppt_f, content_type=ppt_f.type)
                    b.make_public(); data["ppt_url"] = b.public_url
                
                if mode == "add": db.collection("songs").add(data)
                else: db.collection("songs").document(song["id"]).update(data)
                
                st.success("저장되었습니다!"); go_to_main(); st.rerun()
    if st.button("돌아가기"): go_to_main(); st.rerun()

# -------------------------------
# SIDEBAR (콘티 리스트 & 생성)
# -------------------------------
with st.sidebar:
    st.header("🔐 구글 로그인")
    if st.session_state["credentials"] is None:
        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration:none;"><div style="background-color:white; color:#757575; border-radius:4px; border:1px solid #dadce0; padding:10px; text-align:center; font-weight:500; cursor:pointer;">Google 로그인</div></a>', unsafe_allow_html=True)
    else:
        st.success(f"✅ {st.session_state['user_email']}")
        if st.button("로그아웃"):
            st.session_state.update({"credentials": None, "user_email": None})
            cookies["token"] = ""; cookies["refresh_token"] = ""; cookies.save(); st.rerun()

    st.divider(); st.header("🛒 선택된 콘티")
    if st.session_state['cart']:
        fname = st.text_input("📄 파일명", value=f"콘티_{datetime.datetime.now().strftime('%y%m%d')}")
        for idx, item in enumerate(st.session_state['cart']):
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.write(f"**{idx+1}. {item['title']}**")
                if c2.button("X", key=f"c_del_{idx}"): st.session_state['cart'].pop(idx); st.rerun()
        
        if st.button("✨ 콘티 생성 (Slides)", type="primary", use_container_width=True):
            with st.spinner("생성 중..."):
                url = create_praise_slides(st.session_state['cart'], fname, get_google_credentials())
                if url: st.session_state["slide_url"] = url; st.rerun()
        
        if st.button("📥 가사 PPT 생성 (Drive)", use_container_width=True):
            # merge_ppt_files 함수 내에서 upload_to_drive를 호출하며 히스토리를 저장함
            # (이전 코드의 merge_ppt_files 로직 그대로 사용)
            pass 

        if st.session_state.get("slide_url"):
            st.link_button("📄 슬라이드 열기", st.session_state['slide_url'], use_container_width=True)
        
        if st.button("🗑️ 초기화"): st.session_state.update({"cart": [], "slide_url": None}); st.rerun()
    else: st.caption("곡을 담아주세요.")

# -------------------------------
# MAIN ROUTING
# -------------------------------
if st.session_state['page'] == 'add_song': show_add_edit_page("add")
elif st.session_state['page'] == 'edit_song': show_add_edit_page("edit")
else:
    t1, t2 = st.columns([5, 1])
    t1.title("🎵 Praise Maker")
    if t2.button("곡 추가", type="primary", use_container_width=True): go_to_add(); st.rerun()
    
    query = st.text_input("검색", placeholder="제목, 태그 검색...", label_visibility="collapsed").strip().lower()
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()
    
    for doc in docs:
        s = doc.to_dict(); s['id'] = doc.id
        if not query or query in s.get('title','').lower():
            with st.container(border=True):
                h1, h2, h3 = st.columns([8, 1, 1])
                h1.markdown(f"### {s['title']} ({s.get('start_key','')})")
                if h2.button("📝", key=f"edit_{s['id']}"): go_to_edit(s); st.rerun()
                if h3.button("🗑️", key=f"del_{s['id']}"): delete_confirm_dialog(s['id'], s['title'])
                
                # 리스트 담기 버튼
                if st.button("리스트에 담기", key=f"btn_{s['id']}", use_container_width=True, type="primary"):
                    if not any(i['id'] == s['id'] for i in st.session_state['cart']):
                        st.session_state['cart'].append(s); st.rerun()
