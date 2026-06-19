import streamlit as st
import datetime
import os
import io
import tempfile
import shutil
import subprocess
import platform
import requests
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
# 세션 상태 초기화
# -------------------------------
session_keys = {
    "credentials": None,
    "user_email": None,
    "user_name": None,
    "last_activity": None,
    "page": "main",
    "cart": [],
    "editing_song": None,
    "slide_url": None,
    "ppt_slide_url": None,
    "current_page": 1
}

for key, default in session_keys.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -------------------------------
# 다이얼로그 및 인증 관련 함수
# -------------------------------
@st.dialog("곡 전체 삭제 확인")
def delete_confirm_dialog(song_id, title):
    st.write(f"'{title}' 찬양곡 전체를 삭제하시겠습니까?")
    st.caption("⚠️ 주의: 이 곡에 등록된 모든 코드(Key)의 악보와 PPT 정보가 DB에서 완전히 삭제됩니다.")
    c1, c2 = st.columns(2)
    if c1.button("🚨 예, 전체 삭제합니다", type="primary", use_container_width=True):
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
    st.session_state.update({
        "credentials": None,
        "user_email": None,
        "user_name": None,
        "last_activity": None,
        "slide_url": None,
        "ppt_slide_url": None
    })

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

        return True
    except Exception as e:
        st.error(str(e))
        logout()
        return False

def get_user_info(creds):
    try:
        service = build('oauth2', 'v2', credentials=creds)
        user_info = service.userinfo().get().execute()
        return {
            "email": user_info.get("email"),
            "name": user_info.get("name")
        }
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
        user_info = get_user_info(creds)

        if user_info:
            st.session_state["user_email"] = user_info.get("email")
            st.session_state["user_name"] = user_info.get("name")
        else:
            st.session_state["user_email"] = None
            st.session_state["user_name"] = None
 
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"로그인 처리 중 오류 발생: {e}")


def upload_file_to_drive(uploaded_file, folder_id, creds):

    drive_service = build(
        'drive',
        'v3',
        credentials=creds
    )

    safe_name = (
        f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_"
        f"{uploaded_file.name}"
    )

    ext = os.path.splitext(
        uploaded_file.name
    )[1]

    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=ext
    ) as tmp:

        tmp.write(
            uploaded_file.getbuffer()
        )

        temp_path = tmp.name

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

    # PPT 업로드 + Slides 변환
    uploaded = drive_service.files().create(
        body={
            'name': safe_name,
            'parents': [folder_id],
            'mimeType': 'application/vnd.google-apps.presentation'
        },
        media_body=media,
        fields='id, webViewLink'
    ).execute()

    # 실제 변환 결과 확인
    file_info = drive_service.files().get(
        fileId=uploaded['id'],
        fields='mimeType'
    ).execute()

    if file_info["mimeType"] != "application/vnd.google-apps.presentation":
        os.remove(temp_path)
        raise Exception("Google Slides 변환 실패")

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
            "title": filename, "items": [{"id": item["id"], "title": item["title"], "selected_key": item.get("selected_key", "C")} for item in cart_items],
            "file_url": file_url, "user_email": st.session_state.get("user_email"), "created_at": datetime.datetime.now()
        })
    except Exception as e: st.error(f"콘티 저장 실패: {e}")

def merge_and_upload_ppt(cart_items, filename):

    try:

        with st.spinner("가사 PPT 생성 중..."):

            creds = Credentials(
                **st.session_state["credentials"]
            )

            drive_service = build(
                'drive',
                'v3',
                credentials=creds
            )

            slide_ids = []

            for item in cart_items:

                sel_key = item.get("selected_key", "C")
                slide_file_id = ""
                if "keys" in item and sel_key in item["keys"]:
                    slide_file_id = item["keys"][sel_key].get("ppt_drive_file_id", "")
                else:
                    slide_file_id = item.get("ppt_drive_file_id")

                if not slide_file_id:
                    continue

                slide_ids.append(
                    slide_file_id
                )

            if not slide_ids:

                st.error("슬라이드 파일이 없습니다.")
                return

            # Apps Script 호출
            response = requests.post(
                os.environ["APPS_SCRIPT_URL"],
                json={
                    "presentation_ids": slide_ids,
                    "output_name": filename
                },
                timeout=300
            )
            # st.write(response.status_code)
            # st.write(response.text)
            # st.json(response.json())
            # st.write(os.environ["APPS_SCRIPT_URL"])


            result = response.json()

            if not result.get("success"):
                st.error(result.get("error"))
                return

            original_presentation_id = result["presentation_id"]

            # [수정] 이동(update) 대신 복사(copy)를 사용하여 소유권/권한 문제 우회
            copied_file = drive_service.files().copy(
                fileId=original_presentation_id,
                body={
                    "name": filename,
                    "parents": [LYRICS_FOLDER_ID]
                },
                supportsAllDrives=True
            ).execute()

            # 복사된 새 파일의 ID를 사용
            presentation_id = copied_file["id"]

            # Apps Script가 만든 원본 파일 삭제 시도 (타 계정이라 권한이 없으면 조용히 무시)
            try:
                drive_service.files().delete(
                    fileId=original_presentation_id,
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass

            final_url = (
                f"https://docs.google.com/presentation/d/"
                f"{presentation_id}/edit"
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

    except Exception as e:

        st.error(f"오류: {e}")

def show_add_edit_page(mode="add"):
    st.title("찬양곡 추가" if mode == "add" else "찬양곡 수정")
    song = st.session_state.get("editing_song", {}) if mode == "edit" else {}
    if st.button("돌아가기"): st.session_state.update({"page": "main", "editing_song": None, "temp_keys": None}); st.rerun()

    # 다중 키 세션을 컴포넌트 내부에서 임시 관리
    if "temp_keys" not in st.session_state or st.session_state["temp_keys"] is None:
        existing_keys = song.get("keys", {})
        if mode == "edit" and not existing_keys and song.get("start_key"):
            existing_keys = {
                song["start_key"]: {
                    "image_url": song.get("image_url", ""),
                    "ppt_drive_file_id": song.get("ppt_drive_file_id", ""),
                    "ppt_drive_url": song.get("ppt_drive_url", ""),
                    "ppt_ext": song.get("ppt_ext", ".pptx")
                }
            }
        if not existing_keys:
            existing_keys = {"C": {"image_url": "", "ppt_drive_file_id": "", "ppt_drive_url": "", "ppt_ext": ".pptx"}}
        st.session_state["temp_keys"] = deepcopy(existing_keys)

    with st.form("song_form", clear_on_submit=True):
        title = st.text_input("곡 이름 *", value=song.get("title", ""))
        youtube_url = st.text_input("YouTube 링크", value=song.get("youtube_url", ""))
        tags_input = st.text_input("태그 (쉼표 구분)", value=", ".join(song.get("tags", [])) if song.get("tags") else "")
        
        st.divider()
        st.subheader("등록된 Key별 파일 목록")
        st.caption("💡 찬양곡에 필요한 여러 Key들의 악보와 가사를 한 번에 관리하세요.")

        # 등록된 키 파일 렌더링 및 수정 폼 내 개별 키 세트 삭제 UI 구현
        keys_to_delete = []
        for k_code, k_data in list(st.session_state["temp_keys"].items()):
            with st.container(border=True):
                ck1, ck2, ck3 = st.columns([1, 2, 1])
                ck1.markdown(f"### Key: `{k_code}`")
                
                # 특정 키 삭제 기능 (수정 화면 등에서 해당 코드만 부분 삭제)
                if ck3.form_submit_button(f"🗑️ {k_code}코드 삭제", use_container_width=True):
                    keys_to_delete.append(k_code)

                img_up = ck2.file_uploader(f"[{k_code}] 악보 이미지", type=["jpg","png","jpeg"], key=f"img_up_{k_code}")
                ppt_up = ck2.file_uploader(f"[{k_code}] 가사 PPT", type=["ppt","pptx"], key=f"ppt_up_{k_code}")
                
                if img_up: k_data["temp_img_file"] = img_up
                if ppt_up: k_data["temp_ppt_file"] = ppt_up

                if k_data.get("image_url"): ck2.caption(f"✅ 기존 악보 존재함")
                if k_data.get("ppt_drive_url"): ck2.caption(f"✅ 기존 가사 PPT 존재함")

        for tk in keys_to_delete:
            if len(st.session_state["temp_keys"]) > 1:
                del st.session_state["temp_keys"][tk]
                st.rerun()
            else:
                st.error("최소 한 개 이상의 Key 블록이 존재해야 합니다.")

        st.divider()
        st.write("➕ 새로운 Key(코드) 추가하기")
        ak1, ak2, ak3, ak4 = st.columns([2, 1, 1, 2])
        base_key = ak1.selectbox("추가할 Key", ["C","D","E","F","G","A","B"], label_visibility="collapsed", key="add_base_key")
        is_sharp, is_flat = ak2.checkbox("#", key="add_sharp"), ak3.checkbox("b", key="add_flat")
        new_k_code = base_key + ("#" if is_sharp else "b" if is_flat else "")
        
        if ak4.form_submit_button("코드 블록 추가", type="secondary", use_container_width=True):
            if new_k_code not in st.session_state["temp_keys"]:
                st.session_state["temp_keys"][new_k_code] = {"image_url": "", "ppt_drive_file_id": "", "ppt_drive_url": "", "ppt_ext": ".pptx"}
                st.rerun()
            else:
                st.warning("이미 추가된 코드입니다.")

        st.divider()
        if st.form_submit_button("저장하기", type="primary", use_container_width=True):
            if title:
                with st.spinner("저장 중..."):
                    try:
                        final_keys = deepcopy(st.session_state["temp_keys"])
                        creds = Credentials(**st.session_state["credentials"]) if st.session_state["credentials"] else None

                        for k_code, k_data in final_keys.items():
                            if "temp_img_file" in k_data and k_data["temp_img_file"]:
                                img_f = k_data["temp_img_file"]
                                blob = bucket.blob(f"songs/images/{datetime.datetime.now().strftime('%H%M%S')}_{img_f.name}")
                                blob.upload_from_file(img_f, content_type=img_f.type); blob.make_public()
                                k_data["image_url"] = blob.public_url
                                del k_data["temp_img_file"]

                            if "temp_ppt_file" in k_data and k_data["temp_ppt_file"]:
                                ppt_f = k_data["temp_ppt_file"]
                                uploaded = upload_file_to_drive(ppt_f, PPT_FOLDER_ID, creds)
                                k_data["ppt_drive_file_id"] = uploaded["file_id"]
                                k_data["ppt_drive_url"] = uploaded["view_link"]
                                k_data["ppt_ext"] = os.path.splitext(ppt_f.name)[1].lower()
                                del k_data["temp_ppt_file"]

                        first_key = list(final_keys.keys())[0]
                        data = {
                            "title": title,
                            "youtube_url": youtube_url,
                            "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                            "updated_at": datetime.datetime.now(),
                            "keys": final_keys,
                            "start_key": first_key,
                            "image_url": final_keys[first_key]["image_url"],
                            "ppt_drive_file_id": final_keys[first_key]["ppt_drive_file_id"],
                            "ppt_drive_url": final_keys[first_key]["ppt_drive_url"]
                        }

                        if mode == "add":
                            data["created_at"] = datetime.datetime.now()
                            db.collection("songs").add(data)
                        else:
                            db.collection("songs").document(song["id"]).update(data)

                        st.session_state.update({"page": "main", "editing_song": None, "temp_keys": None})
                        st.success("저장 완료"); st.rerun()
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
            st.success(
                f"{st.session_state.get('user_name') or st.session_state.get('user_email')}님"
            )
            if st.button("로그아웃"): logout(); st.rerun()
            st.divider(); st.header("폴더 바로 가기")
            st.link_button(
                "악보 콘티 폴더",
                SHEET_FOLDER_URL,
                use_container_width=True
            )

            st.link_button(
                "가사 콘티 폴더",
                LYRICS_FOLDER_URL,
                use_container_width=True
            )


        
        st.divider(); st.header("콘티 리스트")
        if st.session_state["cart"]:
            fname = st.text_input("파일명", value=f"콘티_{datetime.datetime.now().strftime('%y%m%d')}")
            for idx, item in enumerate(st.session_state["cart"]):
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([4,1,1,1])
                    sel_key = item.get("selected_key", "C")
                    c1.write(f"**{idx+1}. {item['title']} ({sel_key})**")
                    if c2.button("▲", key=f"up_{idx}") and idx > 0:
                        st.session_state["cart"][idx], st.session_state["cart"][idx-1] = st.session_state["cart"][idx-1], st.session_state["cart"][idx]; st.rerun()
                    if c3.button("▼", key=f"dn_{idx}") and idx < len(st.session_state["cart"])-1:
                        st.session_state["cart"][idx], st.session_state["cart"][idx+1] = st.session_state["cart"][idx+1], st.session_state["cart"][idx]; st.rerun()
                    if c4.button("X", key=f"rm_{idx}"): st.session_state["cart"].pop(idx); st.rerun()
                    
                    if "is_full_page" not in item:
                        item["is_full_page"] = False
                    item["is_full_page"] = st.checkbox("전체 페이지 V", value=item["is_full_page"], key=f"full_chk_{idx}")
            
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
                            song_data["selected_key"] = s_item.get("selected_key", "C")
                            if not any(i["id"] == song_data["id"] and i.get("selected_key") == song_data["selected_key"] for i in st.session_state["cart"]):
                                new_items.append(song_data)
                        else:
                            missing_songs.append(s_item["title"])
                    st.session_state["cart"].extend(new_items)
                    if missing_songs: st.warning(f"DB에서 삭제된 곡 제외: {', '.join(missing_songs)}")
                    st.rerun()
                if st.button("기록 삭제", key=f"hist_del_{h_id}", use_container_width=True):
                    delete_history_dialog(h_id, h['title'])
                for s in h.get("items", []): st.write(f"- {s['title']} ({s.get('selected_key', 'C')})")

    t1, t2 = st.columns([5,1])
    t1.title("Praise Maker")
    if t2.button("찬양곡 추가", type="primary", use_container_width=True):
        st.session_state["page"] = "add_song"; st.rerun()
        
    q = st.text_input("검색", placeholder="제목, 태그, Key 검색", label_visibility="collapsed").strip().lower()
    
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").stream()
    
    # 먼저 검색 조건에 맞는 데이터를 리스트로 채집합니다.
    filtered_songs = []
    for doc in docs:
        s = doc.to_dict() | {"id": doc.id}
        if not q or q in s['title'].lower() or any(q in t.lower() for t in s.get('tags', [])) or q in s.get('start_key', '').lower():
            filtered_songs.append(s)
            
    # 페이징 설정
    ITEMS_PER_PAGE = 10  # 한 페이지에 보여줄 찬양곡 개수
    total_items = len(filtered_songs)
    
    if total_items == 0:
        st.info("검색 결과가 없거나 등록된 찬양곡이 없습니다.")
    else:
        # 총 페이지 수 계산
        total_pages = max(1, (total_items - 1) // ITEMS_PER_PAGE + 1)
        
        # 세션 상태로 현재 페이지 번호 관리
        if "current_page" not in st.session_state:
            st.session_state["current_page"] = 1
            
        # 페이지 범위 이탈 방지 예외 처리
        if st.session_state["current_page"] > total_pages:
            st.session_state["current_page"] = total_pages

        # 현재 페이지에 해당하는 데이터만 슬라이싱
        start_idx = (st.session_state["current_page"] - 1) * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        page_items = filtered_songs[start_idx:end_idx]
        
        # 곡 목록 렌더링
        for s in page_items:
            with st.container(border=True):
                h1, h2, h3 = st.columns([8,1,1])
                h1.markdown(f"### {s['title']}")
                if h2.button("수정", key=f"edit_{s['id']}"):
                    st.session_state.update({"editing_song": s, "page": "edit_song"}); st.rerun()
                if h3.button("🗑️ 곡 전체 삭제", key=f"del_{s['id']}"): delete_confirm_dialog(s['id'], s['title'])
                if s.get("tags"): st.markdown(" ".join([f"`#{tag}`" for tag in s["tags"]]))
                
                available_keys = list(s.get("keys", {}).keys())
                if not available_keys and s.get("start_key"):
                    available_keys = [s["start_key"]]
                if not available_keys:
                    available_keys = ["C"]

                # 사용 가능한 키를 한눈에 보여주는 시각적 뱃지 노출
                st.markdown("**보유 중인 Key:** " + " ".join([f"`{k}`" for k in available_keys]))

                k_select_col, btn_add_col = st.columns([2, 8])
                chosen_key = k_select_col.selectbox("코드 선택", available_keys, key=f"sel_k_{s['id']}", label_visibility="collapsed")

                if btn_add_col.button(f"[{chosen_key} 코드] 콘티 리스트에 담기", key=f"add_{s['id']}", use_container_width=True, type="primary"):
                    if not any(item['id'] == s['id'] and item.get('selected_key') == chosen_key for item in st.session_state["cart"]):
                        song_to_cart = deepcopy(s)
                        song_to_cart["selected_key"] = chosen_key
                        st.session_state["cart"].append(song_to_cart)
                        st.rerun()
                l1, l2, l3 = st.columns(3)
                if s.get("youtube_url"):
                    l1.markdown(f'<a href="{s["youtube_url"]}" target="_blank" style="display:flex;align-items:center;justify-content:center;background-color:#F0F2F6;color:#262730;padding:5px 10px;border-radius:5px;text-decoration:none;font-size:13px;border:1px solid #E6E9EF;gap:5px;"><img src="https://upload.wikimedia.org/wikipedia/commons/e/ef/Youtube_logo.png" width="18">YouTube</a>', unsafe_allow_html=True)
                
                tgt_img, tgt_ppt = s.get("image_url", ""), s.get("ppt_drive_url", "")
                if "keys" in s and chosen_key in s["keys"]:
                    tgt_img = s["keys"][chosen_key].get("image_url", tgt_img)
                    tgt_ppt = s["keys"][chosen_key].get("ppt_drive_url", tgt_ppt)

                if tgt_img: l2.markdown(f'<a href="{tgt_img}" target="_blank" style="display:flex;align-items:center;justify-content:center;background-color:#F0F2F6;color:#262730;padding:5px 10px;border-radius:5px;text-decoration:none;font-size:13px;border:1px solid #E6E9EF;">악보 이미지 ({chosen_key})</a>', unsafe_allow_html=True)
                if tgt_ppt: l3.markdown(f'<a href="{tgt_ppt}" target="_blank" style="display:flex;align-items:center;justify-content:center;background-color:#F0F2F6;color:#262730;padding:5px 10px;border-radius:5px;text-decoration:none;font-size:13px;border:1px solid #E6E9EF;">가사 PPT ({chosen_key})</a>', unsafe_allow_html=True)
        
        # ---------------------------------------------------------
        # [수정] 하단 페이징 네비게이션 컨트롤러 추가 (처음, 끝 이동 포함 5열 구조)
        # ---------------------------------------------------------
        st.divider()
        p_col_first, p_col_prev, p_col_text, p_col_next, p_col_last = st.columns([0.7, 0.7, 3, 0.7, 0.7])
        
        with p_col_first:
            if st.button("◀◀ 처음", disabled=(st.session_state["current_page"] == 1), use_container_width=True):
                st.session_state["current_page"] = 1
                st.rerun()

        with p_col_prev:
            if st.button("◀ 이전", disabled=(st.session_state["current_page"] == 1), use_container_width=True):
                st.session_state["current_page"] -= 1
                st.rerun()
                
        with p_col_text:
            st.markdown(f"<p style='text-align: center; margin-top: 5px;'><b>{st.session_state['current_page']}</b> / {total_pages} 페이지 (총 {total_items}곡)</p>", unsafe_allow_html=True)
            
        with p_col_next:
            if st.button("다음 ▶", disabled=(st.session_state["current_page"] == total_pages), use_container_width=True):
                st.session_state["current_page"] += 1
                st.rerun()

        with p_col_last:
            if st.button("끝 ▶▶", disabled=(st.session_state["current_page"] == total_pages), use_container_width=True):
                st.session_state["current_page"] = total_pages
                st.rerun()
