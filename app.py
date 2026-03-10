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
    "credentials": None, "page": "main", "cart": [], 
    "slide_url": None, "merged_ppt_folder_url": None
}.items():
    if key not in st.session_state: st.session_state[key] = default

# -------------------------------
# COOKIE -> SESSION 복원 & OAUTH CALLBACK
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
    except Exception as e: st.error(f"로그인 처리 오류: {e}")

# -------------------------------
# HELPER FUNCTIONS
# -------------------------------
def get_google_credentials():
    if not st.session_state.get("credentials"): return None
    return Credentials(**st.session_state["credentials"])

def go_to_main(): st.session_state.update({"page": "main", "editing_song": None})
def go_to_add(): st.session_state['page'] = 'add_song'
def go_to_edit(song_data):
    st.session_state.update({"editing_song": song_data, "page": "edit_song"})
    st.rerun()

# -------------------------------
# PPT & DRIVE LOGIC
# -------------------------------
def convert_ppt_to_pptx(input_path):
    output_dir = os.path.dirname(input_path)
    soffice = r'C:\Program Files\LibreOffice\program\soffice.exe' if platform.system() == "Windows" else 'soffice'
    if platform.system() == "Windows" and not os.path.exists(soffice):
        st.error("LibreOffice 설치 경로를 확인해주세요."); return None
    try:
        subprocess.run([soffice, '--headless', '--convert-to', 'pptx', '--outdir', output_dir, input_path], check=True, capture_output=True)
        return os.path.join(output_dir, os.path.splitext(os.path.basename(input_path))[0] + ".pptx")
    except Exception as e: st.error(f"변환 실패: {e}"); return None

def upload_to_drive(file_path, filename, credentials):
    service = build('drive', 'v3', credentials=credentials)
    FOLDER_ID = "1Lr_0MmneLOKNyKhW6TMl6V3L88TlBn5P" 
    file_metadata = {'name': filename, 'parents': [FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation')
    
    # 파일 업로드 후 id와 webViewLink를 받아옵니다.
    file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
    
    # (파일 개별 링크, 폴더 링크)를 함께 반환
    file_url = file.get('webViewLink')
    folder_url = f"https://drive.google.com/drive/folders/{FOLDER_ID}"
    return file_url, folder_url

def merge_ppt_files(cart_items, custom_filename, credentials):
    temp_dir = tempfile.mkdtemp()
    processed_files = []
    try:
        for idx, item in enumerate(cart_items):
            if not item.get("ppt_url"): continue
            resp = requests.get(item["ppt_url"])
            ext = ".ppt" if item["ppt_url"].lower().endswith(".ppt") else ".pptx"
            path = os.path.join(temp_dir, f"temp_{idx}{ext}")
            with open(path, "wb") as f: f.write(resp.content)
            if ext == ".ppt":
                conv = convert_ppt_to_pptx(path)
                if conv: processed_files.append(conv)
            else: processed_files.append(path)

        # [중요] 처리할 파일이 하나도 없다면?
        if not processed_files:
            st.error("선택한 곡 중에 유효한 PPT 파일이 없습니다.")
            return None, None # None 하나가 아니라 두 개를 넘겨야 에러가 안 남
    
        merged_prs = Presentation()
        merged_prs.slide_width = Inches(13.333) # 16:9 표준 가로
        merged_prs.slide_height = Inches(7.5)   # 16:9 표준 세로

        for path in processed_files:
            source = Presentation(path)
            for slide in source.slides:
                try: layout = merged_prs.slide_layouts[6]
                except IndexError: layout = merged_prs.slide_layouts[-1]
                new_slide = merged_prs.slides.add_slide(layout)
                for shape in slide.shapes:
                    if shape.shape_type == 13: # Picture
                        # [수정] 원본 이미지를 16:9 슬라이드 크기에 꽉 채우기
                        new_slide.shapes.add_picture(
                            io.BytesIO(shape.image.blob), 
                            0, 0, # 시작 위치 (좌상단 0,0)
                            width=merged_prs.slide_width, 
                            height=merged_prs.slide_height
                        )
        
        # 파일 저장 및 업로드
        filename = f"{custom_filename}.pptx"
        local_path = os.path.join(temp_dir, filename)
        merged_prs.save(local_path)
        # upload_to_drive 함수가 (file_url, folder_url) 두 개를 반환하는지 확인!
        return upload_to_drive(local_path, filename, credentials)

    except Exception as e:
        st.error(f"병합 중 오류 발생: {e}")
        return None, None # 예외 발생 시에도 두 개의 값을 반환
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# -------------------------------
# DIALOGS & SIDEBAR
# -------------------------------
@st.dialog("곡 삭제 확인")
def delete_confirm_dialog(song_id, title):
    st.write(f"'{title}' 곡을 삭제하시겠습니까?")
    c1, c2 = st.columns(2)
    if c1.button("삭제", type="primary", use_container_width=True):
        db.collection("songs").document(song_id).delete(); st.rerun()
    if c2.button("취소", use_container_width=True): st.rerun()

with st.sidebar:
    st.header("🔐 구글 로그인")
    if st.session_state["credentials"] is None:
        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration: none;"><div style="display: flex; align-items: center; justify-content: center; background-color: #ffffff; color: #757575; border-radius: 4px; border: 1px solid #dadce0; padding: 10px 15px; font-family: \'Roboto\', arial, sans-serif; font-weight: 500; cursor: pointer; box-shadow: 0 1px 1px 0 rgba(66,133,244,.15);"> <img src="https://fonts.gstatic.com/s/i/productlogos/googleg/v6/24px.svg" width="20px" height="20px" style="margin-right: 12px;"> Google 로그인 </div></a>', unsafe_allow_html=True)
    else:
        st.success("✅ 로그인 유지됨")
        if st.button("로그아웃", use_container_width=True):
            st.session_state["credentials"] = None
            cookies["token"] = ""; cookies["refresh_token"] = ""; cookies.save(); st.rerun()

    st.divider(); st.header("🛒 선택된 콘티")
    
    # [변경] 성공 링크 표시 (장바구니 위에 고정하여 가독성 확보)
    if st.session_state["slide_url"]:
        st.success("🎉 슬라이드 생성 완료")


    if st.session_state["merged_ppt_folder_url"]:
        st.info("✅ 가사 PPT 저장 완료")
    

    if st.session_state['cart']:
        custom_filename = st.text_input("📄 파일명", value=f"찬양콘티_{datetime.datetime.now().strftime('%y%m%d')}")
        for idx, item in enumerate(st.session_state['cart']):
            with st.container(border=True):
                h1, h2 = st.columns([4, 1])
                h1.write(f"**{idx+1}. {item['title']}**")
                if h2.button("X", key=f"del_{idx}"): st.session_state['cart'].pop(idx); st.rerun()
        
        # --- 1. 콘티 생성 (Google Slides) ---
        if st.button("✨ 콘티 생성 (Slides)", type="primary", use_container_width=True):
            with st.spinner("슬라이드 생성 중..."):
                url = create_praise_slides(st.session_state['cart'], custom_filename, get_google_credentials())
                st.session_state["slide_url"] = url
                st.rerun()
        
        if st.session_state["slide_url"]:
            col1, col2 = st.columns(2)
            col1.link_button("📄 파일 열기", st.session_state['slide_url'], use_container_width=True)
            col2.link_button("📂 폴더 열기", f"https://drive.google.com/drive/folders/1Lr_0MmneLOKNyKhW6TMl6V3L88TlBn5P", use_container_width=True)

        st.write("")

        # --- 2. 가사 PPT 생성 (Drive Save) ---
        if st.button("📥 가사 PPT 생성 (Drive)", use_container_width=True):
            with st.spinner("PPT 병합 및 업로드 중..."):
                # 파일 URL과 폴더 URL을 세션에 저장
                f_url, fold_url = merge_ppt_files(st.session_state['cart'], custom_filename, get_google_credentials())
                st.session_state["merged_ppt_url"] = f_url
                st.session_state["merged_ppt_folder_url"] = fold_url
                st.rerun()

        if st.session_state.get("merged_ppt_url"):
            col3, col4 = st.columns(2)
            col3.link_button("📄 파일 열기", st.session_state['merged_ppt_url'], use_container_width=True)
            col4.link_button("📂 폴더 열기", st.session_state['merged_ppt_folder_url'], use_container_width=True)

        st.divider()
        if st.button("🗑️ 전체 초기화", use_container_width=True):
            st.session_state.update({"cart": [], "slide_url": None, "merged_ppt_url": None, "merged_ppt_folder_url": None})
            st.rerun()
    else: st.caption("곡을 담아주세요.")

# -------------------------------
# MAIN PAGE (기존 UI 유지)
# -------------------------------
if st.session_state['page'] in ['add_song', 'edit_song']:
    # (추가/수정 페이지 로직 생략 - 기존과 동일)
    pass
else:
    col_t, col_a = st.columns([5, 1]); col_t.title("🎵 Praise Maker")
    if col_a.button("곡 추가", type="primary", use_container_width=True): go_to_add(); st.rerun()
    query = st.text_input("search", placeholder="제목, 태그, 키 검색", label_visibility="collapsed").strip().lower()
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()
    
    button_style = "display:flex; align-items:center; justify-content:center; background-color:#F0F2F6; color:#262730; padding:8px; border-radius:6px; text-decoration:none; font-weight:600; border:1px solid #E6E9EF; height:38px;"
    
    for doc in docs:
        s = doc.to_dict(); s['id'] = doc.id
        if not query or query in s.get('title','').lower() or query in s.get('start_key','').lower() or any(query in t.lower() for t in s.get('tags', [])):
            with st.container(border=True):
                h_col, e_col, d_col = st.columns([8, 0.6, 0.6])
                h_col.markdown(f"### {s['title']}")
                if e_col.button("📝", key=f"e_{s['id']}"): go_to_edit(s)
                if d_col.button("🗑️", key=f"d_{s['id']}"): delete_confirm_dialog(s['id'], s['title'])
                st.markdown(f"**Key:** {s['start_key']} | {' '.join([f'`#{t}`' for t in s.get('tags', [])])}")
                
                l1, l2, l3 = st.columns(3)
                if s.get("youtube_url"):
                    l1.markdown(f'<a href="{s["youtube_url"]}" target="_blank" style="{button_style}"><img src="https://upload.wikimedia.org/wikipedia/commons/7/75/YouTube_social_white_squircle_%282017%29.svg" width="18" style="margin-right:8px;">YouTube</a>', unsafe_allow_html=True)
                if s.get("image_url"):
                    l2.markdown(f'<a href="{s["image_url"]}" target="_blank" style="{button_style}">악보</a>', unsafe_allow_html=True)
                if s.get("ppt_url"):
                    l3.markdown(f'<a href="{s["ppt_url"]}" target="_blank" style="{button_style}">PPTX</a>', unsafe_allow_html=True)
                
                if st.button("리스트에 담기", key=f"c_{s['id']}", use_container_width=True, type="primary"):
                    if not any(item['id'] == s['id'] for item in st.session_state['cart']):
                        st.session_state['cart'].append(s); st.rerun()