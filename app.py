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
# COOKIE MANAGER
# -------------------------------
cookies = EncryptedCookieManager(prefix="praise_maker/", password=os.environ.get("COOKIE_SECRET", "dev-secret"))
if not cookies.ready(): st.stop()

# -------------------------------
# SESSION INIT
# -------------------------------
for key, default in {
    "credentials": None, "user_email": None, "page": "main", "cart": [], 
    "slide_url": None, "merged_ppt_url": None, "merged_ppt_folder_url": None
}.items():
    if key not in st.session_state: st.session_state[key] = default

# -------------------------------
# OAUTH & USER INFO
# -------------------------------
def get_user_info(creds):
    try:
        service = build('oauth2', 'v2', credentials=creds)
        user_info = service.userinfo().get().execute()
        return user_info.get("email")
    except: return None

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
    except Exception as e: st.error(f"로그인 처리 오류: {e}")

# -------------------------------
# HELPER FUNCTIONS
# -------------------------------
def get_google_credentials():
    creds_dict = st.session_state.get("credentials")
    if not creds_dict: return None
    return Credentials(**creds_dict)

def go_to_main(): st.session_state.update({"page": "main", "editing_song": None})
def go_to_add(): st.session_state['page'] = 'add_song'
def go_to_edit(song_data):
    st.session_state.update({"editing_song": song_data, "page": "edit_song"})
    st.rerun()

# -------------------------------
# PPT & DRIVE LOGIC (기존 로직 유지)
# -------------------------------
def convert_ppt_to_pptx(input_path):
    output_dir = os.path.dirname(input_path)
    soffice = 'soffice'
    try:
        subprocess.run([soffice, '--headless', '--convert-to', 'pptx', '--outdir', output_dir, input_path], 
                        check=True, capture_output=True)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        return os.path.join(output_dir, base_name + ".pptx")
    except Exception as e: 
        st.error(f"LibreOffice 변환 실패: {e}")
        return None

def upload_to_drive(file_path, filename, credentials):
    try:
        if not credentials: return None, None
        service = build('drive', 'v3', credentials=credentials)
        FOLDER_ID = "1Lr_0MmneLOKNyKhW6TMl6V3L88TlBn5P" 
        file_metadata = {'name': filename, 'parents': [FOLDER_ID]}
        media = MediaFileUpload(file_path, mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation')
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        
        # [추가 기능] Firebase Storage에도 히스토리 저장
        if st.session_state["user_email"]:
            user_path = f"history/{st.session_state['user_email']}/{filename}"
            hist_blob = bucket.blob(user_path)
            hist_blob.upload_from_filename(file_path)
            
        return file.get('webViewLink'), f"https://drive.google.com/drive/folders/{FOLDER_ID}"
    except Exception as e:
        st.error(f"구글 드라이브 업로드 중 에러: {e}")
        return None, None

def merge_ppt_files(cart_items, custom_filename, credentials):
    if not credentials:
        st.error("로그인이 필요합니다.")
        return None, None
    temp_dir = tempfile.mkdtemp()
    processed_files = []
    try:
        for idx, item in enumerate(cart_items):
            ppt_url = item.get("ppt_url")
            if not ppt_url: continue
            resp = requests.get(ppt_url)
            ext = ".ppt" if ppt_url.lower().endswith(".ppt") else ".pptx"
            path = os.path.join(temp_dir, f"temp_{idx}{ext}")
            with open(path, "wb") as f: f.write(resp.content)
            if ext == ".ppt":
                conv = convert_ppt_to_pptx(path)
                if conv: processed_files.append(conv)
            else: processed_files.append(path)

        if not processed_files: return None, None
        merged_prs = Presentation()
        merged_prs.slide_width, merged_prs.slide_height = Inches(13.333), Inches(7.5)
        for path in processed_files:
            source = Presentation(path)
            for slide in source.slides:
                layout = merged_prs.slide_layouts[6] if len(merged_prs.slide_layouts) > 6 else merged_prs.slide_layouts[-1]
                new_slide = merged_prs.slides.add_slide(layout)
                for shape in slide.shapes:
                    if shape.shape_type == 13: # Picture
                        new_slide.shapes.add_picture(io.BytesIO(shape.image.blob), 0, 0, width=merged_prs.slide_width, height=merged_prs.slide_height)
        
        filename = f"{custom_filename}.pptx"
        local_path = os.path.join(temp_dir, filename)
        merged_prs.save(local_path)
        return upload_to_drive(local_path, filename, credentials)
    except Exception as e:
        st.error(f"병합 과정 오류: {e}")
        return None, None
    finally: shutil.rmtree(temp_dir, ignore_errors=True)

# -------------------------------
# PAGE: ADD SONG
# -------------------------------
def show_add_song_page():
    st.title("➕ 새 곡 추가")
    with st.form("add_song_form"):
        col1, col2 = st.columns(2)
        title = col1.text_input("곡 제목*")
        start_key = col2.text_input("Key (예: G)")
        youtube_url = st.text_input("YouTube 링크")
        tags_input = st.text_input("태그 (쉼표 구분)")
        
        st.divider()
        c3, c4 = st.columns(2)
        image_file = c3.file_uploader("악보 이미지", type=['jpg', 'jpeg', 'png'])
        ppt_file = c4.file_uploader("가사 PPT", type=['ppt', 'pptx'])
        
        if st.form_submit_button("저장하기", type="primary", use_container_width=True):
            if not title: st.error("제목을 입력하세요.")
            else:
                with st.spinner("Firebase 업로드 중..."):
                    data = {"title": title, "start_key": start_key, "youtube_url": youtube_url, 
                            "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                            "created_at": datetime.datetime.now(), "image_url": "", "ppt_url": ""}
                    if image_file:
                        blob = bucket.blob(f"songs/images/{image_file.name}")
                        blob.upload_from_file(image_file, content_type=image_file.type)
                        blob.make_public(); data["image_url"] = blob.public_url
                    if ppt_file:
                        blob = bucket.blob(f"songs/ppts/{ppt_file.name}")
                        blob.upload_from_file(ppt_file, content_type=ppt_file.type)
                        blob.make_public(); data["ppt_url"] = blob.public_url
                    db.collection("songs").add(data)
                    st.success("저장 완료!"); go_to_main(); st.rerun()
    if st.button("돌아가기"): go_to_main(); st.rerun()

# -------------------------------
# SIDEBAR & MAIN
# -------------------------------
with st.sidebar:
    st.header("🔐 구글 로그인")
    if st.session_state["credentials"] is None:
        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration: none;"><div style="background-color: white; color: #757575; border-radius: 4px; border: 1px solid #dadce0; padding: 10px; text-align: center; font-weight: 500;">Google 로그인</div></a>', unsafe_allow_html=True)
    else:
        st.success(f"✅ {st.session_state['user_email']} 님")
        if st.button("로그아웃"):
            st.session_state.update({"credentials": None, "user_email": None})
            cookies["token"] = ""; cookies["refresh_token"] = ""; cookies.save(); st.rerun()

    st.divider(); st.header("🛒 선택된 콘티")
    if st.session_state['cart']:
        custom_filename = st.text_input("📄 파일명", value=f"콘티_{datetime.datetime.now().strftime('%y%m%d')}")
        for idx, item in enumerate(st.session_state['cart']):
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.write(f"{idx+1}. {item['title']}")
                if c2.button("X", key=f"cart_del_{idx}"): st.session_state['cart'].pop(idx); st.rerun()
        
        if st.button("✨ 콘티 생성 (Slides)", type="primary", use_container_width=True):
            url = create_praise_slides(st.session_state['cart'], custom_filename, get_google_credentials())
            if url: st.session_state["slide_url"] = url; st.rerun()
        
        if st.button("📥 가사 PPT 생성 (Drive)", use_container_width=True):
            f_url, fold_url = merge_ppt_files(st.session_state['cart'], custom_filename, get_google_credentials())
            if f_url: st.session_state.update({"merged_ppt_url": f_url, "merged_ppt_folder_url": fold_url}); st.success("생성 완료!")

        if st.session_state.get("merged_ppt_url"):
            st.link_button("📄 파일 열기", st.session_state['merged_ppt_url'], use_container_width=True)
            
        if st.button("🗑️ 초기화"): st.session_state.update({"cart": [], "slide_url": None, "merged_ppt_url": None}); st.rerun()
    else: st.caption("곡을 담아주세요.")

# 페이지 라우팅
if st.session_state['page'] == 'add_song': show_add_song_page()
elif st.session_state['page'] == 'edit_song': st.write("수정 페이지 준비 중"); st.button("홈", on_click=go_to_main)
else:
    col_t, col_a = st.columns([5, 1])
    col_t.title("🎵 Praise Maker")
    if col_a.button("곡 추가", type="primary"): go_to_add(); st.rerun()
    
    query = st.text_input("search", label_visibility="collapsed").strip().lower()
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()
    
    for doc in docs:
        s = doc.to_dict(); s['id'] = doc.id
        if not query or query in s.get('title','').lower():
            with st.container(border=True):
                st.markdown(f"### {s['title']} ({s['start_key']})")
                if st.button("리스트에 담기", key=f"add_{s['id']}", use_container_width=True):
                    if not any(i['id'] == s['id'] for i in st.session_state['cart']):
                        st.session_state['cart'].append(s); st.rerun()
