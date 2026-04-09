import streamlit as st
import datetime
import os
import io
import requests
import tempfile
import shutil
import subprocess
import platform
from pptx import Presentation

from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides
from google.oauth2.credentials import Credentials
from streamlit_cookies_manager import EncryptedCookieManager
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# 로컬 테스트 시 보안 연결 허용
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

st.set_page_config(page_title="Praise Maker", layout="wide")

# 고정된 구글 드라이브 폴더 ID
TARGET_FOLDER_ID = "1Lr_0MmneLOKNyKhW6TMl6V3L88TlBn5P"
TARGET_FOLDER_URL = f"https://drive.google.com/drive/folders/{TARGET_FOLDER_ID}"

# -------------------------------
# 쿠키 매니저 설정
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
    "cart": [], "editing_song": None, "slide_url": None
}
for key, default in session_keys.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -------------------------------
# 구글 인증 로직
# -------------------------------
def get_user_info(creds):
    try:
        service = build('oauth2', 'v2', credentials=creds)
        user_info = service.userinfo().get().execute()
        return user_info.get("email")
    except: return None

# 쿠키 기반 복구
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

# 콜백 처리
if st.query_params.get("code") and st.session_state["credentials"] is None:
    try:
        flow = create_flow()
        flow.fetch_token(code=st.query_params["code"])
        creds = flow.credentials
        creds_dict = {"token": creds.token, "refresh_token": creds.refresh_token, "token_uri": creds.token_uri, "client_id": creds.client_id, "client_secret": creds.client_secret, "scopes": creds.scopes}
        st.session_state["credentials"] = creds_dict
        st.session_state["user_email"] = get_user_info(creds)
        cookies["token"], cookies["refresh_token"] = creds.token, creds.refresh_token
        cookies.save()
        st.query_params.clear()
        st.rerun()
    except Exception as e: st.error(f"로그인 오류: {e}")

# -------------------------------
# 가사 PPT 생성 및 업로드 (수정됨)
# -------------------------------
def merge_and_upload_ppt(cart_items, filename):
    creds = Credentials(**st.session_state["credentials"])

    with st.spinner("가사 PPT 제작 중..."):
        temp_dir = tempfile.mkdtemp()

        try:
            processed_files = []

            for idx, item in enumerate(cart_items):
                if not item.get("ppt_url"):
                    continue

                response = requests.get(item["ppt_url"])

                ext = ".ppt" if item["ppt_url"].lower().endswith(".ppt") else ".pptx"
                temp_path = os.path.join(temp_dir, f"temp_{idx}{ext}")

                with open(temp_path, "wb") as f:
                    f.write(response.content)

                if ext == ".ppt":
                    output_dir = os.path.dirname(temp_path)
                    soffice = r'C:\Program Files\LibreOffice\program\soffice.exe' if platform.system() == "Windows" else 'soffice'

                    if platform.system() == "Windows" and not os.path.exists(soffice):
                        st.error("LibreOffice 설치 경로를 확인해주세요.")
                        return

                    subprocess.run(
                        [soffice, '--headless', '--convert-to', 'pptx', '--outdir', output_dir, temp_path],
                        check=True
                    )

                    temp_path = os.path.join(
                        output_dir,
                        os.path.splitext(os.path.basename(temp_path))[0] + ".pptx"
                    )

                processed_files.append(temp_path)

            if not processed_files:
                st.error("PPT 파일이 없습니다.")
                return

            merged_pptx = Presentation()

            for path in processed_files:
                source_ppt = Presentation(path)

                for slide in source_ppt.slides:
                    new_slide = merged_pptx.slides.add_slide(merged_pptx.slide_layouts[6])

                    for shape in slide.shapes:
                        if shape.shape_type == 13:
                            new_slide.shapes.add_picture(
                                io.BytesIO(shape.image.blob),
                                shape.left,
                                shape.top,
                                shape.width,
                                shape.height
                            )
                        elif shape.has_text_frame:
                            textbox = new_slide.shapes.add_textbox(
                                shape.left,
                                shape.top,
                                shape.width,
                                shape.height
                            )
                            textbox.text_frame.text = shape.text

            output_path = os.path.join(temp_dir, f"{filename}.pptx")
            merged_pptx.save(output_path)

            drive_service = build('drive', 'v3', credentials=creds)

            file_metadata = {'name': f"{filename}.pptx", 'parents': [TARGET_FOLDER_ID]}

            media = MediaFileUpload(
                output_path,
                mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation'
            )

            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()

            st.session_state["slide_url"] = file.get("webViewLink")
            st.success("가사 PPT 업로드 완료")
            st.rerun()

        except Exception as e:
            st.error(f"PPT 작업 중 오류: {e}")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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
    if c2.button("취소", use_container_width=True): st.rerun()

# -------------------------------
# 곡 추가 및 수정 페이지
# -------------------------------
def show_add_edit_page(mode="add"):
    st.title("찬양곡 추가" if mode == "add" else "찬양곡 수정")
    song = st.session_state.get("editing_song", {}) if mode == "edit" else {}
    
    if st.button("돌아가기"):
        st.session_state.update({"page": "main", "editing_song": None})
        st.rerun()

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
                        st.session_state.update({"page": "main", "editing_song": None})
                        st.rerun()
                    except Exception as e:
                        st.error(f"저장 실패: {e}")

# -------------------------------
# 메인 화면
# -------------------------------
if st.session_state["page"] == "add_song":
    show_add_edit_page("add")
elif st.session_state["page"] == "edit_song":
    show_add_edit_page("edit")
else:
    with st.sidebar:
        st.header("계정")
        if st.session_state["credentials"] is None:
            flow = create_flow()
            auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration:none;"><div style="background-color:white; color:#757575; border-radius:4px; border:1px solid #dadce0; padding:10px; text-align:center; font-weight:500; cursor:pointer;">Google 로그인</div></a>', unsafe_allow_html=True)
        else:
            user_info = st.session_state.get("user_email")
            status_text = f"{user_info}님 환영합니다" if user_info else "인증이 완료되었습니다"
            st.success(status_text)
            if st.button("로그아웃"):
                st.session_state.update({"credentials": None, "user_email": None})
                cookies["token"], cookies["refresh_token"] = "", ""
                cookies.save(); st.rerun()

        st.divider(); st.header("콘티 리스트")
        if st.session_state["cart"]:
            fname = st.text_input("파일명", value=f"콘티_{datetime.datetime.now().strftime('%y%m%d')}")
            for idx, item in enumerate(st.session_state["cart"]):
                st.write(f"{idx+1}. {item['title']}")
            
            if st.button("슬라이드 생성", type="primary", use_container_width=True):
                creds = Credentials(**st.session_state["credentials"])
                url = create_praise_slides(st.session_state["cart"], fname, creds)
                if url: 
                    st.session_state["slide_url"] = url
                    st.rerun()

            if st.button("가사 PPT 생성", use_container_width=True):
                if st.session_state["credentials"]:
                    merge_and_upload_ppt(st.session_state["cart"], fname)
                else:
                    st.error("구글 로그인이 필요합니다.")

            if st.session_state.get("slide_url"):
                st.divider()
                st.link_button("생성된 파일 열기", st.session_state["slide_url"], use_container_width=True)
                st.link_button("파일이 저장된 폴더 열기", TARGET_FOLDER_URL, use_container_width=True)

            if st.button("전체 초기화"):
                st.session_state.update({"cart": [], "slide_url": None}); st.rerun()
        else: st.caption("곡을 담아주세요")

    t1, t2 = st.columns([5,1])
    t1.title("Praise Maker")
    if t2.button("찬양곡 추가", type="primary", use_container_width=True):
        st.session_state["page"] = "add_song"; st.rerun()

    query = st.text_input("검색", placeholder="제목 또는 태그 검색", label_visibility="collapsed").strip().lower()
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()

    btn_style = "display:flex; align-items:center; justify-content:center; background-color:#F0F2F6; color:#262730; padding:5px 10px; border-radius:5px; text-decoration:none; font-size:13px; border:1px solid #E6E9EF; gap:5px;"

    for doc in docs:
        s = doc.to_dict(); s["id"] = doc.id
        match = not query or query in s.get("title","").lower() or any(query in t.lower() for t in s.get("tags", []))
        if match:
            with st.container(border=True):
                h1, h2, h3 = st.columns([8, 1, 1])
                h1.markdown(f"### {s['title']} ({s.get('start_key','')})")
                if h2.button("수정", key=f"edit_{s['id']}", use_container_width=True):
                    st.session_state.update({"editing_song": s, "page": "edit_song"}); st.rerun()
                if h3.button("삭제", key=f"del_{s['id']}", use_container_width=True):
                    delete_confirm_dialog(s['id'], s['title'])
                
                if s.get("tags"):
                    st.markdown(" ".join([f"`#{t}`" for t in s.get("tags", [])]))
                
                l1, l2, l3 = st.columns(3)
                if s.get("youtube_url"):
                    youtube_icon_url = "https://upload.wikimedia.org/wikipedia/commons/e/ef/Youtube_logo.png"
                    l1.markdown(f'<a href="{s["youtube_url"]}" target="_blank" style="{btn_style}"><img src="{youtube_icon_url}" width="18" height="13">YouTube</a>', unsafe_allow_html=True)
                if s.get("image_url"):
                    l2.markdown(f'<a href="{s["image_url"]}" target="_blank" style="{btn_style}">악보 이미지</a>', unsafe_allow_html=True)
                if s.get("ppt_url"):
                    l3.markdown(f'<a href="{s["ppt_url"]}" target="_blank" style="{btn_style}">가사 PPT</a>', unsafe_allow_html=True)

                if st.button("리스트에 담기", key=f"add_{s['id']}", use_container_width=True, type="primary"):
                    if not any(i["id"] == s["id"] for i in st.session_state["cart"]):
                        st.session_state["cart"].append(s); st.rerun()
