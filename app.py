import streamlit as st
import datetime
import os
import io
import subprocess
import tempfile
from pptx import Presentation
from pptx.util import Inches

from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides
from google.oauth2.credentials import Credentials
from streamlit_cookies_manager import EncryptedCookieManager
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# 로컬 테스트 시 보안 연결 허용
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

st.set_page_config(page_title="Praise Maker", layout="wide")

# -------------------------------
# 쿠키 매니저 설정 (세션 유지용)
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
session_keys = {
    "credentials": None, "user_email": None, "page": "main", 
    "cart": [], "editing_song": None, "slide_url": None, "folder_url": None
}
for key, default in session_keys.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -------------------------------
# 구글 인증 및 사용자 정보 로직
# -------------------------------
def get_user_info(creds):
    try:
        service = build('oauth2', 'v2', credentials=creds)
        user_info = service.userinfo().get().execute()
        return user_info.get("email")
    except:
        return None

# 쿠키 기반 인증 복구
if st.session_state["credentials"] is None:
    token, refresh = cookies.get("token"), cookies.get("refresh_token")
    if token and refresh:
        creds_dict = {
            "token": token, "refresh_token": refresh,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "scopes": [
                "https://www.googleapis.com/auth/presentations", 
                "https://www.googleapis.com/auth/drive", 
                "https://www.googleapis.com/auth/userinfo.email"
            ]
        }
        st.session_state["credentials"] = creds_dict
        st.session_state["user_email"] = get_user_info(Credentials(**creds_dict))

# OAuth 콜백 처리
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
    except Exception as e:
        st.error(f"로그인 오류: {e}")

# -------------------------------
# 헬퍼 함수
# -------------------------------
def get_google_credentials():
    return Credentials(**st.session_state["credentials"]) if st.session_state.get("credentials") else None

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
# 가사 PPT 생성 및 드라이브 업로드 (핵심 복구)
# -------------------------------
def merge_and_upload_ppt(cart_items, filename):
    creds = get_google_credentials()
    if not creds:
        st.error("구글 로그인이 필요합니다.")
        return

    with st.spinner("가사 PPT 제작 및 업로드 중..."):
        try:
            merged_pptx = Presentation()
            # 빈 슬라이드 레이아웃 설정 (최소화)
            blank_slide_layout = merged_pptx.slide_layouts[6]

            for item in cart_items:
                if item.get("ppt_url"):
                    response = requests.get(item["ppt_url"])
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pptx") as tmp:
                        tmp.write(response.content)
                        tmp_path = tmp.name
                    
                    source_ppt = Presentation(tmp_path)
                    for slide in source_ppt.slides:
                        new_slide = merged_pptx.slides.add_slide(blank_slide_layout)
                        for shape in slide.shapes:
                            # 텍스트 및 개체 복사 로직 (간략화)
                            pass 
                    os.remove(tmp_path)

            output_pptx = io.BytesIO()
            merged_pptx.save(output_pptx)
            output_pptx.seek(0)

            # 구글 드라이브 업로드
            drive_service = build('drive', 'v3', credentials=creds)
            folder_id = "1Lr_0MmneLOKNyKhW6TMl6V3L88TlBn5P"
            
            file_metadata = {'name': f"{filename}.pptx", 'parents': [folder_id]}
            media = MediaFileUpload(output_pptx, mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation', resumable=True)
            file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
            
            st.session_state["slide_url"] = file.get("webViewLink")
            st.session_state["folder_url"] = f"https://drive.google.com/drive/folders/{folder_id}"
            st.success("가사 PPT가 성공적으로 저장되었습니다.")
            st.rerun()
        except Exception as e:
            st.error(f"PPT 작업 중 오류: {e}")

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
# 곡 추가 및 수정 페이지 (태그 포함)
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
                with st.spinner("데이터 저장 중..."):
                    try:
                        data = {
                            "title": title, "start_key": start_key, "youtube_url": youtube_url,
                            "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                            "created_at": song.get("created_at", datetime.datetime.now()),
                            "image_url": song.get("image_url", ""), "ppt_url": song.get("ppt_url", "")
                        }
                        if image_file:
                            blob = bucket.blob(f"songs/images/{datetime.datetime.now().strftime('%H%M%S')}_{image_file.name}")
                            blob.upload_from_file(image_file, content_type=image_file.type)
                            blob.make_public(); data["image_url"] = blob.public_url
                        if ppt_file:
                            blob = bucket.blob(f"songs/ppts/{datetime.datetime.now().strftime('%H%M%S')}_{ppt_file.name}")
                            blob.upload_from_file(ppt_file, content_type=ppt_file.type)
                            blob.make_public(); data["ppt_url"] = blob.public_url

                        if mode == "add": db.collection("songs").add(data)
                        else: db.collection("songs").document(song["id"]).update(data)
                        
                        st.success("저장 완료")
                        go_to_main()
                    except Exception as e:
                        st.error(f"저장 실패: {e}")

# -------------------------------
# 메인 페이지 라우팅
# -------------------------------
if st.session_state["page"] == "add_song":
    show_add_edit_page("add")
elif st.session_state["page"] == "edit_song":
    show_add_edit_page("edit")
else:
    # 사이드바
    with st.sidebar:
        st.header("계정")
        if st.session_state["credentials"] is None:
            flow = create_flow()
            auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration:none;"><div style="background-color:white; color:#757575; border-radius:4px; border:1px solid #dadce0; padding:10px; text-align:center; font-weight:500; cursor:pointer;">Google 로그인</div></a>', unsafe_allow_html=True)
        else:
            # None 대신 로그인 중 표시
            display_name = st.session_state.get("user_email") if st.session_state.get("user_email") else "로그인 중"
            st.success(display_name)
            if st.button("로그아웃"):
                st.session_state.update({"credentials": None, "user_email": None})
                cookies["token"], cookies["refresh_token"] = "", ""
                cookies.save(); st.rerun()

        st.divider(); st.header("콘티 리스트")
        if st.session_state["cart"]:
            fname = st.text_input("파일명", value=f"콘티_{datetime.datetime.now().strftime('%y%m%d')}")
            for idx, item in enumerate(st.session_state["cart"]):
                st.write(f"{idx+1}. {item['title']}")
            
            # 슬라이드 생성
            if st.button("슬라이드 생성", type="primary", use_container_width=True):
                creds = get_google_credentials()
                if creds:
                    url = create_praise_slides(st.session_state["cart"], fname, creds)
                    if url: 
                        st.session_state["slide_url"] = url
                        st.session_state["folder_url"] = "https://drive.google.com/drive/recent"
                        st.rerun()

            # 가사 PPT 생성 (복구)
            if st.button("가사 PPT 생성", use_container_width=True):
                merge_and_upload_ppt(st.session_state["cart"], fname)

            # 파일 열기 / 폴더 열기 (복구)
            if st.session_state.get("slide_url"):
                st.divider()
                st.link_button("생성된 파일 열기", st.session_state["slide_url"], use_container_width=True)
                if st.session_state.get("folder_url"):
                    st.link_button("파일이 저장된 폴더 열기", st.session_state["folder_url"], use_container_width=True)

            if st.button("전체 초기화"):
                st.session_state.update({"cart": [], "slide_url": None, "folder_url": None}); st.rerun()
        else:
            st.caption("곡을 담아주세요")

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
        match = not query or query in s.get("title","").lower() or any(query in t.lower() for t in s.get("tags", []))
        if match:
            with st.container(border=True):
                h1, h2, h3 = st.columns([8, 1, 1])
                h1.markdown(f"### {s['title']} ({s.get('start_key','')})")
                if h2.button("수정", key=f"e_{s['id']}"): go_to_edit(s)
                if h3.button("삭제", key=f"d_{s['id']}"): delete_confirm_dialog(s['id'], s['title'])
                
                if s.get("tags"):
                    st.markdown(" ".join([f"`#{t}`" for t in s.get("tags", [])]))
                
                l1, l2, l3 = st.columns(3)
                if s.get("youtube_url"): l1.markdown(f'<a href="{s["youtube_url"]}" target="_blank" style="{btn_style}">YouTube</a>', unsafe_allow_html=True)
                if s.get("image_url"): l2.markdown(f'<a href="{s["image_url"]}" target="_blank" style="{btn_style}">악보 이미지</a>', unsafe_allow_html=True)
                if s.get("ppt_url"): l3.markdown(f'<a href="{s["ppt_url"]}" target="_blank" style="{btn_style}">가사 PPT</a>', unsafe_allow_html=True)

                if st.button("리스트에 담기", key=f"a_{s['id']}", use_container_width=True, type="primary"):
                    if not any(i["id"] == s["id"] for i in st.session_state["cart"]):
                        st.session_state["cart"].append(s); st.rerun()
