import streamlit as st
import datetime
import os
import io
import tempfile
import shutil
import subprocess
import platform

from copy import deepcopy

from pptx import Presentation
from pptx.util import Inches

from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import (
    MediaFileUpload,
    MediaIoBaseDownload
)
from googleapiclient.errors import HttpError

from streamlit_cookies_manager import EncryptedCookieManager

# 로컬 테스트 시 보안 연결 허용
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

st.set_page_config(
    page_title="Praise Maker",
    layout="wide"
)

# -------------------------------
# [시스템 설정] 폴더 ID 및 URL
# -------------------------------
SHEET_FOLDER_ID = os.environ.get("SHEET_FOLDER_ID", "FOLDER_ID")
LYRICS_FOLDER_ID = os.environ.get("LYRICS_FOLDER_ID", "FOLDER_ID")
PPT_FOLDER_ID = os.environ.get("PPT_FOLDER_ID", "FOLDER_ID")

SHEET_FOLDER_URL = f"https://drive.google.com/drive/folders/{SHEET_FOLDER_ID}"
LYRICS_FOLDER_URL = f"https://drive.google.com/drive/folders/{LYRICS_FOLDER_ID}"

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
    "cart": [], "editing_song": None, "slide_url": None, "ppt_slide_url": None
}

for key, default in session_keys.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -------------------------------
# 다이얼로그 및 인증 관련 함수
# -------------------------------
@st.dialog("곡 삭제 확인")
def delete_confirm_dialog(song_id, title):
    st.write(f"'{title}' 곡을 삭제하시겠습니까?")
    c1, c2 = st.columns(2)
    if c1.button("삭제", type="primary", use_container_width=True):
        db.collection("songs").document(song_id).delete()
        st.rerun()
    if c2.button("취소", use_container_width=True): st.rerun()

@st.dialog("기록 삭제 확인")
def delete_history_dialog(doc_id, title):
    st.write(f"'{title}' 콘티 기록을 삭제하시겠습니까?")
    st.caption("※ 드라이브 파일은 유지되며 목록에서만 사라집니다.")
    c1, c2 = st.columns(2)
    if c1.button("기록 삭제", type="primary", use_container_width=True):
        db.collection("playlists").document(doc_id).delete()
        st.rerun()
    if c2.button("취소", use_container_width=True): st.rerun()

def logout():
    st.session_state.update({"credentials": None, "user_email": None, "slide_url": None, "ppt_slide_url": None})
    cookies["token"], cookies["refresh_token"] = "", ""
    cookies.save()

def validate_and_refresh_credentials():
    creds_data = st.session_state.get("credentials")
    if not creds_data: return False
    try:
        creds = Credentials(**creds_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            st.session_state["credentials"] = {
                "token": creds.token, "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri, "client_id": creds.client_id,
                "client_secret": creds.client_secret, "scopes": creds.scopes
            }
            cookies["token"], cookies["refresh_token"] = creds.token, creds.refresh_token
            cookies.save()
        return True
    except Exception as e:
        st.error(str(e))
        logout()
        return False

def get_user_info(creds):
    try:
        service = build('oauth2', 'v2', credentials=creds)
        user_info = service.userinfo().get().execute()
        return user_info.get("email")
    except: return None

# --- OAuth 콜백 처리  ---
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
        st.error(f"로그인 처리 중 오류 발생: {e}")

# 쿠키 기반 자동 로그인 복구
if st.session_state["credentials"] is None:
    token, refresh = cookies.get("token"), cookies.get("refresh_token")
    if token and refresh and token != "":
        st.session_state["credentials"] = {
            "token": token, "refresh_token": refresh,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "scopes": ["https://www.googleapis.com/auth/presentations", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/userinfo.email"]
        }
        if validate_and_refresh_credentials():
            st.session_state["user_email"] = get_user_info(Credentials(**st.session_state["credentials"]))
        else: st.rerun()


def upload_file_to_drive(uploaded_file, folder_id, creds):
    drive_service = build('drive', 'v3', credentials=creds)

    safe_name = (
        f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_"
        f"{uploaded_file.name}"
    )

    ext = os.path.splitext(uploaded_file.name)[1]

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(uploaded_file.getbuffer())
        temp_path = tmp.name

    file_metadata = {
        'name': safe_name,
        'parents': [folder_id]
    }
    mime_type = (
        'application/vnd.ms-powerpoint'
        if ext.lower() == '.ppt'
        else 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    )
    media = MediaFileUpload(
        temp_path,
        mimetype=mime_type,
        resumable=True
    )

    uploaded = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink'
    ).execute()

    drive_service.permissions().create(
        fileId=uploaded['id'],
        body={
            'type': 'anyone',
            'role': 'reader'
        }
    ).execute()

    os.remove(temp_path)
    return {
        "file_id": uploaded["id"],
        "view_link": uploaded["webViewLink"]
    }

def download_drive_file(file_id, output_path, creds):

    drive_service = build('drive', 'v3', credentials=creds)

    request = drive_service.files().get_media(
        fileId=file_id
    )

    with io.FileIO(output_path, 'wb') as file:
        downloader = MediaIoBaseDownload(
            file,
            request
        )

        done = False

        while not done:
            _, done = downloader.next_chunk()

# -------------------------------
# 생성 및 저장 로직 (이하 기존 코드 유지)
# -------------------------------
def save_playlist_to_firebase(filename, cart_items, file_url):
    try:
        db.collection("playlists").add({
            "title": filename, "items": [{"id": item["id"], "title": item["title"]} for item in cart_items],
            "file_url": file_url, "user_email": st.session_state.get("user_email"), "created_at": datetime.datetime.now()
        })
    except Exception as e: st.error(f"콘티 저장 실패: {e}")

def merge_and_upload_ppt(cart_items, filename):

    creds = Credentials(**st.session_state["credentials"])

    drive_service = build(
        'drive',
        'v3',
        credentials=creds
    )

    slides_service = build(
        'slides',
        'v1',
        credentials=creds
    )

    try:

        with st.spinner("가사 PPT 생성 중..."):

            # 최종 병합 프레젠테이션 생성
            merged_presentation = slides_service.presentations().create(
                body={
                    "title": filename
                }
            ).execute()

            merged_presentation_id = (
                merged_presentation["presentationId"]
            )

            # 생성 직후 기본 빈 슬라이드 ID 저장
            merged_data = slides_service.presentations().get(
                presentationId=merged_presentation_id
            ).execute()

            default_slide_id = (
                merged_data["slides"][0]["objectId"]
            )

            converted_file_ids = []

            for item in cart_items:

                ppt_file_id = item.get(
                    "ppt_drive_file_id"
                )

                if not ppt_file_id:
                    continue

                # PPT/PPTX -> Google Slides 변환
                converted_file = drive_service.files().copy(
                    fileId=ppt_file_id,
                    body={
                        "name": f"{item['title']}_converted",
                        "mimeType": "application/vnd.google-apps.presentation"
                    },
                    fields="id"
                ).execute()

                source_presentation_id = (
                    converted_file["id"]
                )

                converted_file_ids.append(
                    source_presentation_id
                )

                # source presentation 조회
                source_presentation = (
                    slides_service.presentations().get(
                        presentationId=source_presentation_id
                    ).execute()
                )

                source_slides = source_presentation.get(
                    "slides",
                    []
                )

                # 슬라이드 복사
                for slide in source_slides:

                    slide_id = slide["objectId"]

                    slides_service.presentations().pages().copyTo(
                        presentationId=source_presentation_id,
                        pageObjectId=slide_id,
                        body={
                            "presentationId": merged_presentation_id
                        }
                    ).execute()

            # 기본 생성된 빈 슬라이드 제거
            slides_service.presentations().batchUpdate(
                presentationId=merged_presentation_id,
                body={
                    "requests": [
                        {
                            "deleteObject": {
                                "objectId": default_slide_id
                            }
                        }
                    ]
                }
            ).execute()

            # 공개 권한 부여
            drive_service.permissions().create(
                fileId=merged_presentation_id,
                body={
                    "type": "anyone",
                    "role": "reader"
                }
            ).execute()

            # 임시 변환 Google Slides 삭제
            for converted_id in converted_file_ids:

                try:
                    drive_service.files().delete(
                        fileId=converted_id
                    ).execute()

                except Exception:
                    pass

            final_url = (
                f"https://docs.google.com/presentation/d/"
                f"{merged_presentation_id}/edit"
            )

            st.session_state["ppt_slide_url"] = (
                final_url
            )

            save_playlist_to_firebase(
                filename,
                cart_items,
                final_url
            )

            st.success("가사 PPT 생성 완료")

            st.rerun()

    except Exception as e:

        st.error(f"오류: {e}")

    finally:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

def show_add_edit_page(mode="add"):
    st.title("찬양곡 추가" if mode == "add" else "찬양곡 수정")
    song = st.session_state.get("editing_song", {}) if mode == "edit" else {}
    if st.button("돌아가기"): st.session_state.update({"page": "main", "editing_song": None}); st.rerun()

    with st.form("song_form", clear_on_submit=True):
        col1, col2 = st.columns([2, 1])
        title = col1.text_input("곡 이름 *", value=song.get("title", ""))
        with col2:
            st.write("Key")
            k_col1, k_col2, k_col3 = st.columns([2, 1, 1])
            s_key = song.get("start_key", "C")
            base_key = k_col1.selectbox("Key", ["C","D","E","F","G","A","B"], index=["C","D","E","F","G","A","B"].index(s_key[0] if s_key else "C"), label_visibility="collapsed")
            is_sharp, is_flat = k_col2.checkbox("#", value="#" in s_key), k_col3.checkbox("b", value="b" in s_key)
        
        start_key = base_key + ("#" if is_sharp else "b" if is_flat else "")
        youtube_url = st.text_input("YouTube 링크", value=song.get("youtube_url", ""))
        tags_input = st.text_input("태그 (쉼표 구분)", value=", ".join(song.get("tags", [])) if song.get("tags") else "")
        
        c3, c4 = st.columns(2)
        image_file = c3.file_uploader("악보 이미지", type=["jpg","png","jpeg"], key="img_up")
        ppt_file = c4.file_uploader("가사 PPT", type=["ppt","pptx"], key="ppt_up")

        if st.form_submit_button("저장하기", type="primary", use_container_width=True):
            if title:
                with st.spinner("저장 중..."):
                    try:
                        data = {
                            "title": title,
                            "start_key": start_key,
                            "youtube_url": youtube_url,
                            "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                            "updated_at": datetime.datetime.now(),
                            "image_url": song.get("image_url", ""),
                            "ppt_drive_file_id": song.get("ppt_drive_file_id", ""),
                            "ppt_drive_url": song.get("ppt_drive_url", ""),
                             "ppt_ext": song.get("ppt_ext", ".pptx")
                        }
                        if image_file:
                            blob = bucket.blob(f"songs/images/{datetime.datetime.now().strftime('%H%M%S')}_{image_file.name}")
                            blob.upload_from_file(image_file, content_type=image_file.type); blob.make_public(); data["image_url"] = blob.public_url
                        if ppt_file:

                            creds = Credentials(
                                **st.session_state["credentials"]
                            )

                            uploaded = upload_file_to_drive(
                                ppt_file,
                                PPT_FOLDER_ID,
                                creds
                            )

                            data["ppt_drive_file_id"] = uploaded["file_id"]
                            data["ppt_drive_url"] = uploaded["view_link"]
                            data["ppt_ext"] = os.path.splitext(ppt_file.name)[1].lower()
                        
                        if mode == "add":
                            data["created_at"] = datetime.datetime.now()
                            db.collection("songs").add(data)

                        else:
                            db.collection("songs").document(song["id"]).update(data)
                        st.success("저장 완료"); st.session_state.update({"page": "main", "editing_song": None}); st.rerun()
                    except Exception as e: st.error(f"저장 실패: {e}")

if st.session_state["page"] == "add_song": show_add_edit_page("add")
elif st.session_state["page"] == "edit_song": show_add_edit_page("edit")
else:
    if st.session_state["credentials"]:
        if not validate_and_refresh_credentials():
            st.rerun()
            
    with st.sidebar:
        st.header("계정")
        if st.session_state["credentials"] is None:
            flow = create_flow()
            auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration:none;"><div style="background-color:white; color:#757575; border-radius:4px; border:1px solid #dadce0; padding:10px; text-align:center; font-weight:500; cursor:pointer;">Google 로그인</div></a>', unsafe_allow_html=True)
        else:
            st.success(f"{st.session_state.get('user_email')}님")
            if st.button("로그아웃"): logout(); st.rerun()

        st.divider(); st.header("콘티 리스트")
        if st.session_state["cart"]:
            fname = st.text_input("파일명", value=f"콘티_{datetime.datetime.now().strftime('%y%m%d')}")
            for idx, item in enumerate(st.session_state["cart"]):
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([5,1,1,1])
                    c1.write(f"**{idx+1}. {item['title']}**")
                    if c2.button("▲", key=f"up_{idx}") and idx > 0:
                        st.session_state["cart"][idx], st.session_state["cart"][idx-1] = st.session_state["cart"][idx-1], st.session_state["cart"][idx]; st.rerun()
                    if c3.button("▼", key=f"dn_{idx}") and idx < len(st.session_state["cart"])-1:
                        st.session_state["cart"][idx], st.session_state["cart"][idx+1] = st.session_state["cart"][idx+1], st.session_state["cart"][idx]; st.rerun()
                    if c4.button("X", key=f"rm_{idx}"): st.session_state["cart"].pop(idx); st.rerun()
            
            st.divider(); st.subheader("슬라이드 제작")
            if st.button("악보 PPT 생성", type="primary", use_container_width=True):
                if validate_and_refresh_credentials():
                    url = create_praise_slides(st.session_state["cart"], fname, Credentials(**st.session_state["credentials"]), SHEET_FOLDER_ID)
                    if url: 
                        st.session_state["slide_url"] = url
                        save_playlist_to_firebase(fname, st.session_state["cart"], url)
                        st.rerun()
            
            if st.session_state.get("slide_url"):
                st.link_button("악보 열기", st.session_state["slide_url"], use_container_width=True)
                st.link_button("악보 폴더", SHEET_FOLDER_URL, use_container_width=True)
            st.divider();
            if st.button("가사 PPT 생성", use_container_width=True):
                if validate_and_refresh_credentials(): merge_and_upload_ppt(st.session_state["cart"], fname)
            
            if st.session_state.get("ppt_slide_url"):
                st.link_button("가사 열기", st.session_state["ppt_slide_url"], use_container_width=True)
                st.link_button("가사 폴더", LYRICS_FOLDER_URL, use_container_width=True)

            if st.button("전체 초기화", use_container_width=True):
                st.session_state.update({"cart": [], "slide_url": None, "ppt_slide_url": None}); st.rerun()
        else: st.caption("곡을 담아주세요")

        st.divider(); st.header("최근 생성 콘티")
        history = db.collection("playlists").order_by("created_at", direction="DESCENDING").limit(5).stream()
        for h_doc in history:
            h, h_id = h_doc.to_dict(), h_doc.id
            with st.expander(f"{h['title']} ({h['created_at'].strftime('%m/%d %H:%M')})"):
                if st.button("콘티 리스트에 담기", key=f"hist_load_{h_id}", use_container_width=True, type="primary"):
                    missing_songs = []
                    new_items = []
                    for s_item in h.get("items", []):
                        song_ref = db.collection("songs").document(s_item["id"]).get()
                        if song_ref.exists:
                            song_data = song_ref.to_dict()
                            song_data["id"] = s_item["id"]
                            if not any(i["id"] == song_data["id"] for i in st.session_state["cart"]):
                                new_items.append(song_data)
                        else:
                            missing_songs.append(s_item["title"])
                    st.session_state["cart"].extend(new_items)
                    if missing_songs: st.warning(f"DB에서 삭제된 곡 제외: {', '.join(missing_songs)}")
                    st.rerun()

                if st.button("기록 삭제", key=f"hist_del_{h_id}", use_container_width=True):
                    delete_history_dialog(h_id, h['title'])
                for s in h.get("items", []): st.write(f"- {s['title']}")

    t1, t2 = st.columns([5,1])
    t1.title("Praise Maker")
    if t2.button("찬양곡 추가", type="primary", use_container_width=True):
        st.session_state["page"] = "add_song"; st.rerun()
        
    q = st.text_input("검색", placeholder="제목, 태그, Key 검색", label_visibility="collapsed").strip().lower()
    
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()
    for doc in docs:
        s = doc.to_dict() | {"id": doc.id}
        if not q or q in s['title'].lower() or any(q in t.lower() for t in s.get('tags', [])) or q in s.get('start_key', '').lower():
            with st.container(border=True):
                h1, h2, h3 = st.columns([8,1,1])
                h1.markdown(f"### {s['title']} ({s.get('start_key','C')})")
                if h2.button("수정", key=f"edit_{s['id']}"):
                    st.session_state.update({"editing_song": s, "page": "edit_song"}); st.rerun()
                if h3.button("삭제", key=f"del_{s['id']}"): delete_confirm_dialog(s['id'], s['title'])
                if s.get("tags"): st.markdown(" ".join([f"`#{tag}`" for tag in s["tags"]]))
                if st.button("콘티 리스트에 담기", key=f"add_{s['id']}", use_container_width=True, type="primary"):
                    if s['id'] not in [item['id'] for item in st.session_state["cart"]]:
                        st.session_state["cart"].append(s); st.rerun()
                l1, l2, l3 = st.columns(3)
                if s.get("youtube_url"):
                    l1.markdown(f'<a href="{s["youtube_url"]}" target="_blank" style="display:flex;align-items:center;justify-content:center;background-color:#F0F2F6;color:#262730;padding:5px 10px;border-radius:5px;text-decoration:none;font-size:13px;border:1px solid #E6E9EF;gap:5px;"><img src="https://upload.wikimedia.org/wikipedia/commons/e/ef/Youtube_logo.png" width="18">YouTube</a>', unsafe_allow_html=True)
                if s.get("image_url"): l2.markdown(f'<a href="{s["image_url"]}" target="_blank" style="display:flex;align-items:center;justify-content:center;background-color:#F0F2F6;color:#262730;padding:5px 10px;border-radius:5px;text-decoration:none;font-size:13px;border:1px solid #E6E9EF;">악보 이미지</a>', unsafe_allow_html=True)
                if s.get("ppt_drive_url"):
                    l3.markdown(
                        f'<a href="{s["ppt_drive_url"]}" target="_blank" style="display:flex;align-items:center;justify-content:center;background-color:#F0F2F6;color:#262730;padding:5px 10px;border-radius:5px;text-decoration:none;font-size:13px;border:1px solid #E6E9EF;">가사 PPT</a>',
                        unsafe_allow_html=True
                    )