import streamlit as st
import datetime
import os
import io
import tempfile
import shutil
import subprocess
import platform
import requests
import difflib
import random
from copy import deepcopy
from streamlit_cookies_manager import EncryptedCookieManager

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
# 전역 스타일 (폰트 + 상태 배지)
# -------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;800&display=swap');

/* 상속으로만 적용 (아이콘 폰트처럼 자체 font-family를 지정한 요소는 그대로 유지됨) */
.stApp {
    font-family: 'Noto Sans KR', sans-serif;
}

.pm-badge {
    display: inline-block;
    font-size: 12px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 999px;
    margin: 2px 4px 2px 0;
}
.pm-badge-practice { background: #F3E8FF; color: #7E22CE; }
.pm-badge-warning { background: #FEF3E2; color: #B45309; }

/* 곡 카드 / 콘티 카트 카드 / Key 블록: st.container(key=...)가 붙여주는 공식 st-key- 클래스를 활용 */
div[class*="st-key-song-card-"],
div[class*="st-key-cart-item-"],
div[class*="st-key-key-block-"] {
    background: #FFFFFF;
}
</style>
""", unsafe_allow_html=True)

# -------------------------------
# 새로고침에도 로그인이 풀리지 않도록, 암호화된 쿠키에 로그인 정보를 보관
# (COOKIES_PASSWORD 미설정 시 기본값으로 동작은 하지만 배포 환경에서는 반드시 별도 값으로 설정할 것)
# -------------------------------
cookies = EncryptedCookieManager(
    prefix="praisemaker/",
    password=os.environ.get("COOKIES_PASSWORD", "praise-maker-dev-only-change-me")
)
if not cookies.ready():
    st.stop()

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
    "current_page": 1,
    "song_cache": []
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
    st.caption("주의: 이 곡에 등록된 모든 코드(Key)의 악보와 공통 PPT 정보가 DB에서 완전히 삭제됩니다.")
    c1, c2 = st.columns(2)
    if c1.button("예, 전체 삭제합니다", icon=":material/delete_forever:", type="primary", use_container_width=True):
        db.collection("songs").document(song_id).delete()
        st.rerun()
    if c2.button("취소", use_container_width=True): st.rerun()

@st.dialog("기록 삭제 확인")
def delete_history_dialog(doc_id, title):
    st.write(f"'{title}' 콘티 기록을 삭제하시겠습니까?")
    st.caption("※ 드라이브 파일은 유지되며 목록에서만 사라집니다.")
    c1, c2 = st.columns(2)
    if c1.button("기록 삭제", icon=":material/delete:", type="primary", use_container_width=True):
        db.collection("playlists").document(doc_id).delete()
        st.rerun()
    if c2.button("취소", use_container_width=True): st.rerun()

def normalize_title(title):
    return "".join(title.lower().split())

def find_similar_songs(title, exclude_id=None, threshold=0.6):
    # 추가 Firestore 조회 없이, 메인 목록 로드 시 캐싱해둔 song_cache만으로 유사 제목을 찾습니다.
    norm_title = normalize_title(title)
    if not norm_title: return []

    similar = []
    for s in st.session_state.get("song_cache", []):
        if exclude_id and s.get("id") == exclude_id: continue
        norm_other = normalize_title(s.get("title", ""))
        if not norm_other: continue
        is_substring = norm_title in norm_other or norm_other in norm_title
        ratio = difflib.SequenceMatcher(None, norm_title, norm_other).ratio()
        if is_substring or ratio >= threshold:
            similar.append(s)
    return similar

@st.dialog("비슷한 곡 발견")
def duplicate_confirm_dialog(similar_songs, title, youtube_url, tags_input, common_ppt_up, mode, song, is_practice):
    st.write(f"'{title}'와(과) 제목이 비슷한 곡이 이미 등록되어 있습니다.")
    for s in similar_songs:
        st.caption(f"- {s.get('title')} (`{s.get('start_key', 'C')}`)")
    st.write("그래도 등록하시겠습니까?")
    c1, c2 = st.columns(2)
    if c1.button("예, 등록합니다", type="primary", use_container_width=True):
        do_save_song(title, youtube_url, tags_input, common_ppt_up, mode, song, is_practice)
    if c2.button("취소", use_container_width=True): st.rerun()

# -------------------------------
# 랜덤 콘티 생성 (키 기준 매칭)
# -------------------------------
NATURAL_KEY_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SIMILAR_KEY_DISTANCE_THRESHOLD = 1  # 같은 키 또는 완전5도 이내(예: C-G, C-F)까지 "비슷한 키"로 취급

def key_to_pitch_class(key):
    if not key: return None
    base = key[0].upper()
    if base not in NATURAL_KEY_PITCH: return None
    pitch = NATURAL_KEY_PITCH[base]
    if len(key) > 1:
        if key[1] == "#": pitch += 1
        elif key[1] == "b": pitch -= 1
    return pitch % 12

def key_distance(key1, key2):
    # 5도권(circle of fifths) 기준 거리: 완전5도만큼 떨어진 키일수록 전조가 자연스러워 "비슷한 키"로 취급합니다.
    p1, p2 = key_to_pitch_class(key1), key_to_pitch_class(key2)
    if p1 is None or p2 is None: return 99
    pos1, pos2 = (7 * p1) % 12, (7 * p2) % 12
    diff = abs(pos1 - pos2)
    return min(diff, 12 - diff)

def build_cart_item(song, key):
    item = deepcopy(song)
    specific_image = song.get("keys", {}).get(key, {}).get("image_url")
    if specific_image:
        item["image_url"] = specific_image
    item["selected_key"] = key
    item["is_full_page"] = False
    return item

def generate_random_setlist():
    # 1,2 / 3,4 / 5 곡 구성: 각 쌍은 가능하면 키가 비슷한 곡으로, 마지막 한 곡은 완전 랜덤으로 뽑습니다.
    # 연습곡은 아직 정식 레퍼토리가 아니므로 후보에서 제외합니다.
    pool = [s for s in st.session_state.get("song_cache", []) if s.get("start_key") and s.get("status") != "practice"]
    if len(pool) < 5: return None

    remaining = pool[:]

    def take_random():
        return remaining.pop(random.randrange(len(remaining)))

    def take_similar(ref_key):
        candidates = [s for s in remaining if key_distance(ref_key, s.get("start_key")) <= SIMILAR_KEY_DISTANCE_THRESHOLD]
        if not candidates: return take_random()
        chosen = random.choice(candidates)
        remaining.remove(chosen)
        return chosen

    song1 = take_random()
    song2 = take_similar(song1["start_key"])
    song3 = take_random()
    song4 = take_similar(song3["start_key"])
    song5 = take_random()

    return [build_cart_item(s, s["start_key"]) for s in (song1, song2, song3, song4, song5)]

# -------------------------------
# 자주 사용된 곡 (최근 사용 기록 집계)
# -------------------------------
RECENT_USE_SHOW_DAYS = 14   # 최근 이 기간 안에 쓴 곡만 "최근 사용" 표시를 함
FREQUENT_USE_WINDOW_DAYS = 30  # 이 기간 내 사용 횟수를 "최근 N회"로 집계
FREQUENT_USE_THRESHOLD = 2  # 집계 기간 내 이 횟수 이상이면 "자주 사용됨" 경고로 격상

def compute_song_usage_stats():
    # 악보/가사 PPT 생성 시 이미 playlists에 사용 기록이 남고 있어서, 새 쓰기 로직 없이 이걸 집계만 합니다.
    # 같은 날 악보+가사를 둘 다 생성하면 콘티 기록이 2건 남는데, 실제로는 1번 쓴 거라 (곡ID, 날짜) 기준으로 중복 제거합니다.
    song_dates = {}
    for doc in db.collection("playlists").stream():
        d = doc.to_dict()
        created_at = d.get("created_at")
        if not hasattr(created_at, "date"): continue
        date_obj = created_at.date()
        for item in d.get("items", []):
            song_id = item.get("id")
            if not song_id: continue
            song_dates.setdefault(song_id, set()).add(date_obj)

    today = datetime.date.today()
    stats = {}
    for song_id, dates in song_dates.items():
        last_used = max(dates)
        recent_count = sum(1 for d in dates if (today - d).days <= FREQUENT_USE_WINDOW_DAYS)
        stats[song_id] = {"last_used": last_used, "recent_count": recent_count}
    return stats

def save_credentials_to_cookie(creds_dict):
    try:
        cookies["refresh_token"] = creds_dict.get("refresh_token") or ""
        cookies["token_uri"] = creds_dict.get("token_uri") or ""
        cookies["client_id"] = creds_dict.get("client_id") or ""
        cookies["client_secret"] = creds_dict.get("client_secret") or ""
        cookies["scopes"] = ",".join(creds_dict.get("scopes") or [])
        cookies.save()
        print(f"[cookie-auth] saved. has_refresh_token={bool(creds_dict.get('refresh_token'))}", flush=True)
    except Exception as e:
        print(f"[cookie-auth] save FAILED: {e!r}", flush=True)

def clear_credentials_cookie():
    # 라이브러리의 del(쿠키 prefix 처리 버그)이 아닌, 빈 값으로 덮어써서 안전하게 제거합니다.
    try:
        for k in ["refresh_token", "token_uri", "client_id", "client_secret", "scopes"]:
            cookies[k] = ""
        cookies.save()
        print("[cookie-auth] cleared.", flush=True)
    except Exception as e:
        print(f"[cookie-auth] clear FAILED: {e!r}", flush=True)

def restore_credentials_from_cookie():
    refresh_token = cookies.get("refresh_token")
    print(f"[cookie-auth] restore attempt. cookie has refresh_token={bool(refresh_token)}", flush=True)
    if not refresh_token: return False
    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=cookies.get("token_uri"),
            client_id=cookies.get("client_id"),
            client_secret=cookies.get("client_secret"),
            scopes=cookies.get("scopes").split(",") if cookies.get("scopes") else None
        )
        creds.refresh(Request())
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
        print("[cookie-auth] restore SUCCESS", flush=True)
        return True
    except Exception as e:
        print(f"[cookie-auth] restore FAILED: {e!r}", flush=True)
        clear_credentials_cookie()
        return False

def logout():
    st.session_state.update({
        "credentials": None,
        "user_email": None,
        "user_name": None,
        "last_activity": None,
        "slide_url": None,
        "ppt_slide_url": None
    })
    clear_credentials_cookie()

def validate_and_refresh_credentials():
    creds_data = st.session_state.get("credentials")
    if not creds_data: return False
    try:
        creds = Credentials(**creds_data)
        if creds.expired or (creds.valid == False):
            if creds.refresh_token:
                creds.refresh(Request())
                st.session_state["credentials"] = {
                    "token": creds.token, "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri, "client_id": creds.client_id,
                    "client_secret": creds.client_secret, "scopes": creds.scopes
                }
                save_credentials_to_cookie(st.session_state["credentials"])
            else:
                return False
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

# 새로고침 등으로 session_state가 비어도, 쿠키에 저장된 refresh_token으로 로그인을 복원합니다.
if st.session_state["credentials"] is None and not st.query_params.get("code"):
    restore_credentials_from_cookie()

# --- OAuth 콜백 처리 ---
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
        save_credentials_to_cookie(creds_dict)
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
    drive_service = build('drive', 'v3', credentials=creds)
    safe_name = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uploaded_file.name}"
    ext = os.path.splitext(uploaded_file.name)[1]

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(uploaded_file.getbuffer())
        temp_path = tmp.name

    mime_type = 'application/vnd.ms-powerpoint' if ext.lower() == '.ppt' else 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    media = MediaFileUpload(temp_path, mimetype=mime_type, resumable=True)

    uploaded = drive_service.files().create(
        body={'name': safe_name, 'parents': [folder_id], 'mimeType': 'application/vnd.google-apps.presentation'},
        media_body=media, fields='id, webViewLink'
    ).execute()

    file_info = drive_service.files().get(fileId=uploaded['id'], fields='mimeType').execute()
    if file_info["mimeType"] != "application/vnd.google-apps.presentation":
        os.remove(temp_path)
        raise Exception("Google Slides 변환 실패")

    drive_service.permissions().create(fileId=uploaded['id'], body={'type': 'anyone', 'role': 'reader'}).execute()
    os.remove(temp_path)
    return {"file_id": uploaded["id"], "view_link": uploaded["webViewLink"]}

# -------------------------------
# 생성 및 저장 로직
# -------------------------------
def save_playlist_to_firebase(filename, cart_items, file_url):
    try:
        db.collection("playlists").add({
            "title": filename, 
            "items": [{"id": item["id"], "title": item["title"], "selected_key": item.get("selected_key", "C")} for item in cart_items],
            "file_url": file_url, 
            "user_email": st.session_state.get("user_email"), 
            "created_at": datetime.datetime.now()
        })
    except Exception as e: st.error(f"콘티 저장 실패: {e}")

def merge_and_upload_ppt(cart_items, filename):
    try:
        with st.spinner("가사 PPT 생성 중..."):
            creds = Credentials(**st.session_state["credentials"])
            drive_service = build('drive', 'v3', credentials=creds)
            slide_ids = []

            for item in cart_items:
                slide_file_id = item.get("ppt_drive_file_id", "")
                if not slide_file_id: continue
                slide_ids.append(slide_file_id)

            if not slide_ids:
                st.error("슬라이드 파일이 없습니다.")
                return

            response = requests.post(
                os.environ["APPS_SCRIPT_URL"],
                json={"presentation_ids": slide_ids, "output_name": filename},
                timeout=300
            )

            result = response.json()
            if not result.get("success"):
                st.error(result.get("error"))
                return

            original_presentation_id = result["presentation_id"]
            copied_file = drive_service.files().copy(
                fileId=original_presentation_id,
                body={"name": filename, "parents": [LYRICS_FOLDER_ID]},
                supportsAllDrives=True
            ).execute()

            presentation_id = copied_file["id"]
            try:
                drive_service.files().delete(fileId=original_presentation_id, supportsAllDrives=True).execute()
            except Exception: pass

            final_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
            st.session_state["ppt_slide_url"] = final_url
            save_playlist_to_firebase(filename, cart_items, final_url)
            st.success("가사 PPT 생성 완료")
    except Exception as e: st.error(f"오류: {e}")

def do_save_song(title, youtube_url, tags_input, common_ppt_up, mode, song, is_practice):
    with st.spinner("저장 중..."):
        try:
            final_keys = deepcopy(st.session_state["temp_keys"])
            creds = Credentials(**st.session_state["credentials"]) if st.session_state["credentials"] else None

            common_ppt_id = song.get("ppt_drive_file_id", "")
            common_ppt_url = song.get("ppt_drive_url", "")
            if common_ppt_up:
                uploaded = upload_file_to_drive(common_ppt_up, PPT_FOLDER_ID, creds)
                common_ppt_id = uploaded["file_id"]
                common_ppt_url = uploaded["view_link"]

            for k_code, k_data in final_keys.items():
                if "temp_img_file" in k_data and k_data["temp_img_file"]:
                    img_f = k_data["temp_img_file"]

                    # [구글 슬라이드 크기 제한 & 포맷 에러 완벽 방어]
                    from PIL import Image
                    import io

                    # 1. 파일 바이트를 안전하게 복사하여 이미지 객체로 로드
                    img_bytes = img_f.getbuffer()
                    image = Image.open(io.BytesIO(img_bytes))

                    # 2. 악보 이미지의 특성(단색, 인덱스 모드 등)으로 인한 포맷 충돌을 막기 위해 확실하게 풀 RGB로 컨버트
                    if image.mode != "RGB":
                        image = image.convert("RGB")

                    # 3. 해상도가 구글 슬라이드 규격을 넘지 않도록 최대 2500px 규격으로 축소
                    image.thumbnail((2500, 2500), Image.Resampling.LANCZOS)

                    # 4. 메모리 스트림에 순정 표준 JPEG 바이너리로 저장
                    compressed_img_io = io.BytesIO()
                    image.save(compressed_img_io, format="JPEG", quality=85)
                    compressed_img_io.seek(0)

                    # 5. 파이어베이스 스토리지에 업로드 (MIME 타입을 image/jpeg로 완전 고정)
                    blob = bucket.blob(f"songs/images/{datetime.datetime.now().strftime('%H%M%S')}_{img_f.name}")
                    blob.upload_from_file(compressed_img_io, content_type="image/jpeg")
                    blob.make_public()

                    k_data["image_url"] = blob.public_url
                    del k_data["temp_img_file"]

                # 만약 과거 구형 데이터라 개별 코드 블록 내부의 image_url이 없는데 최상위 공통 주소가 있다면 물려줍니다.
                elif not k_data.get("image_url") and song.get("image_url"):
                    k_data["image_url"] = song.get("image_url")

            first_key = list(final_keys.keys())[0]
            data = {
                "title": title,
                "youtube_url": youtube_url,
                "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                "updated_at": datetime.datetime.now(),
                "keys": final_keys,
                "start_key": first_key,
                "image_url": final_keys[first_key]["image_url"],
                "ppt_drive_file_id": common_ppt_id,
                "ppt_drive_url": common_ppt_url
            }

            if mode == "add":
                data["created_at"] = datetime.datetime.now()
                data["uploaded_by"] = st.session_state.get("user_name") or st.session_state.get("user_email") or "알 수 없음"
                data["status"] = "practice" if is_practice else "official"
                db.collection("songs").add(data)
            else:
                db.collection("songs").document(song["id"]).update(data)

            st.session_state.update({"page": "main", "editing_song": None, "temp_keys": None})
            st.success("저장 완료"); st.rerun()
        except Exception as e: st.error(f"저장 실패: {e}")

def show_add_edit_page(mode="add"):
    st.title("찬양곡 추가" if mode == "add" else "찬양곡 수정")
    song = st.session_state.get("editing_song", {}) if mode == "edit" else {}
    if st.button("돌아가기", icon=":material/arrow_back:"): st.session_state.update({"page": "main", "editing_song": None, "temp_keys": None}); st.rerun()

    if "temp_keys" not in st.session_state or st.session_state["temp_keys"] is None:
        existing_keys = song.get("keys", {})
        if mode == "edit" and not existing_keys and song.get("start_key"):
            existing_keys = {song["start_key"]: {"image_url": ""}}
        if not existing_keys:
            existing_keys = {}
        st.session_state["temp_keys"] = deepcopy(existing_keys)

    # [수정] clear_on_submit=True 가 있으면 제출 시 데이터가 초기화되어 데이터 비교문 및 if title 구문을 건너뛰고 튕기므로 False로 교정
    with st.form("song_form", clear_on_submit=False):
        title = st.text_input("곡 이름 *", value=song.get("title", ""))
        youtube_url = st.text_input("YouTube 링크", value=song.get("youtube_url", ""))
        tags_input = st.text_input("태그 (쉼표 구분)", value=", ".join(song.get("tags", [])) if song.get("tags") else "")

        is_practice = False
        if mode == "add":
            is_practice = st.checkbox("연습곡으로 등록 (정식 곡 목록에는 안 보이고, 나중에 따로 정식 등록할 수 있어요)")

        common_ppt_up = st.file_uploader("공통 가사 PPT (모든 Key 공통 사용)", type=["ppt","pptx"])
        if song.get("ppt_drive_url"):
            st.caption("이미 등록된 공통 가사 PPT가 존재합니다.")

        st.divider()
        st.subheader("등록된 Key별 악보 목록")
        st.caption("각 Key 블록에는 악보 이미지만 추가하세요.")

        keys_to_delete = []
        for k_code, k_data in list(st.session_state["temp_keys"].items()):
            with st.container(border=True, key=f"key-block-{k_code}", gap=None):
                ck1, ck2, ck3 = st.columns([1, 2, 1])
                ck1.markdown(f"### Key: `{k_code}`")

                if ck3.form_submit_button(f"{k_code}코드 삭제", icon=":material/delete:", use_container_width=True):
                    keys_to_delete.append(k_code)

                img_up = ck2.file_uploader(f"[{k_code}] 악보 이미지", type=["jpg","png","jpeg"], key=f"img_up_{k_code}")
                if img_up: k_data["temp_img_file"] = img_up
                if k_data.get("image_url"): ck2.caption("기존 악보 존재함")

        for tk in keys_to_delete:
            if len(st.session_state["temp_keys"]) > 1:
                del st.session_state["temp_keys"][tk]
                st.rerun()
            else:
                st.error("최소 한 개 이상의 Key 블록이 존재해야 합니다.")

        st.divider()
        st.write("새로운 Key(코드) 추가하기")
        ak1, ak2, ak3, ak4 = st.columns([2, 1, 1, 2])
        base_key = ak1.selectbox("추가할 Key", ["C","D","E","F","G","A","B"], label_visibility="collapsed", key="add_base_key")
        is_sharp, is_flat = ak2.checkbox("#", key="add_sharp"), ak3.checkbox("b", key="add_flat")
        new_k_code = base_key + ("#" if is_sharp else "b" if is_flat else "")
        
        if ak4.form_submit_button("코드 블록 추가", icon=":material/add:", type="secondary", use_container_width=True):
            if new_k_code not in st.session_state["temp_keys"]:
                st.session_state["temp_keys"][new_k_code] = {"image_url": ""}
                st.rerun()
            else: st.warning("이미 추가된 코드입니다.")

        st.divider()
        if st.form_submit_button("저장하기", icon=":material/save:", type="primary", use_container_width=True):
            if title:
                if mode == "edit":
                    current_tags = [t.strip() for t in tags_input.split(",")] if tags_input else []
                    origin_tags = song.get("tags", [])
                    
                    is_same_text = (
                        title == song.get("title", "") and
                        youtube_url == song.get("youtube_url", "") and
                        current_tags == origin_tags
                    )
                    
                    is_same_keys = True
                    origin_keys = song.get("keys", {})
                    if set(st.session_state["temp_keys"].keys()) != set(origin_keys.keys()):
                        is_same_keys = False
                    else:
                        for k, v in st.session_state["temp_keys"].items():
                            if v.get("temp_img_file") is not None:
                                is_same_keys = False
                                break
                    
                    if is_same_text and is_same_keys and (common_ppt_up is None):
                        st.info("변경 사항이 없습니다.")
                        st.session_state.update({"page": "main", "editing_song": None, "temp_keys": None})
                        import time
                        time.sleep(1)
                        st.rerun()

                valid_check = True
                if mode == "add" and not common_ppt_up:
                    st.error("신곡 등록 시 가사 PPT 파일은 필수 항목입니다. PPT를 추가해 주세요.")
                    valid_check = False

                if not st.session_state["temp_keys"]:
                    st.error("최소 한 개 이상의 Key 코드를 추가하고 악보 이미지를 업로드해 주세요.")
                    valid_check = False
                    
                for k_code, k_data in st.session_state["temp_keys"].items():
                    # [⚠️ 과거 데이터 호환 버그 교정 핵심] 
                    # 개별 Key 블록에 image_url이 없더라도 최상위 song 문서에 공통 image_url이 남아있다면 악보가 유효한 것으로 처리합니다.
                    has_any_sheet = bool(k_data.get("image_url")) or bool(k_data.get("temp_img_file")) or bool(song.get("image_url"))
                    if not has_any_sheet:
                        st.error(f"[{k_code} 코드] 블록에 악보 이미지가 누락되었습니다. 악보를 추가해 주세요.")
                        valid_check = False
                
                if valid_check:
                    similar_songs = find_similar_songs(title, exclude_id=song.get("id") if mode == "edit" else None)
                    if similar_songs:
                        duplicate_confirm_dialog(similar_songs, title, youtube_url, tags_input, common_ppt_up, mode, song, is_practice)
                    else:
                        do_save_song(title, youtube_url, tags_input, common_ppt_up, mode, song, is_practice)

if st.session_state["page"] == "add_song": show_add_edit_page("add")
elif st.session_state["page"] == "edit_song": show_add_edit_page("edit")
else:
    if st.session_state["credentials"]:
        validate_and_refresh_credentials()

    # 사이드바(랜덤 콘티 생성 포함)에서도 바로 쓸 수 있도록, 검색창보다 먼저 곡 목록을 캐싱해둡니다.
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").stream()
    all_songs = [doc.to_dict() | {"id": doc.id} for doc in docs]
    st.session_state["song_cache"] = all_songs
    usage_stats = compute_song_usage_stats()

    with st.sidebar:
        st.header("계정")
        if st.session_state["credentials"] is None:
            flow = create_flow()
            auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            st.markdown(f'<a href="{auth_url}" target="_self" style="text-decoration:none;"><div style="background-color:white; color:#757575; border-radius:4px; border:1px solid #dadce0; padding:10px; text-align:center; font-weight:500; cursor:pointer;">Google 로그인</div></a>', unsafe_allow_html=True)
        else:
            st.success(f"{st.session_state.get('user_name') or st.session_state.get('user_email')}님")
            if st.button("로그아웃", icon=":material/logout:"): logout(); st.rerun()
            st.divider(); st.header("폴더 바로 가기")
            st.link_button("악보 콘티 폴더", SHEET_FOLDER_URL, icon=":material/folder_open:", use_container_width=True)
            st.link_button("가사 콘티 폴더", LYRICS_FOLDER_URL, icon=":material/folder_open:", use_container_width=True)

        st.divider(); st.header("콘티 리스트")
        if st.button("랜덤 콘티 생성 (5곡)", icon=":material/shuffle:", use_container_width=True):
            new_cart = generate_random_setlist()
            if new_cart is None:
                st.error("랜덤 콘티를 만들려면 최소 5곡 이상 등록되어 있어야 합니다.")
            else:
                st.session_state.update({"cart": new_cart, "slide_url": None, "ppt_slide_url": None})
                st.rerun()

        if st.session_state["cart"]:
            fname = st.text_input("파일명", value=f"콘티_{datetime.datetime.now().strftime('%y%m%d')}")
            for idx, item in enumerate(st.session_state["cart"]):
                with st.container(border=True, key=f"cart-item-{idx}", gap=None):
                    c1, c2, c3, c4 = st.columns([4,1,1,1])
                    sel_key = item.get("selected_key", "C")
                    c1.write(f"**{idx+1}. {item['title']} ({sel_key})**")
                    
                    # 위로 이동 버튼 로직
                    if c2.button("", key=f"up_{idx}", icon=":material/arrow_upward:", help="위로 이동") and idx > 0:
                        st.session_state["cart"][idx], st.session_state["cart"][idx-1] = st.session_state["cart"][idx-1], st.session_state["cart"][idx]
                        st.rerun()

                    # 아래로 이동 버튼 로직 (안정적인 구조로 교정)
                    if c3.button("", key=f"dn_{idx}", icon=":material/arrow_downward:", help="아래로 이동") and idx < len(st.session_state["cart"]) - 1:
                        st.session_state["cart"][idx], st.session_state["cart"][idx+1] = st.session_state["cart"][idx+1], st.session_state["cart"][idx]
                        st.rerun()

                    # 제거 버튼 로직
                    if c4.button("", key=f"rm_{idx}", icon=":material/close:", help="목록에서 제거"):
                        st.session_state["cart"].pop(idx)
                        st.rerun()

                    if "is_full_page" not in item: item["is_full_page"] = False
                    item["is_full_page"] = st.checkbox("전체 페이지로 보기", value=item["is_full_page"], key=f"full_chk_{idx}")

            st.divider(); st.subheader("슬라이드 제작")
            if st.button("악보 PPT 생성", icon=":material/music_note:", type="primary", use_container_width=True):
                if validate_and_refresh_credentials():
                    url = create_praise_slides(st.session_state["cart"], fname, Credentials(**st.session_state["credentials"]), SHEET_FOLDER_ID)
                    if url:
                        st.session_state["slide_url"] = url
                        save_playlist_to_firebase(fname, st.session_state["cart"], url)
                        st.rerun()

            if st.session_state.get("slide_url"):
                with st.container(border=True):
                    st.link_button("악보 열기", st.session_state["slide_url"], use_container_width=True)
                    st.link_button("악보 폴더 열기", SHEET_FOLDER_URL, icon=":material/folder_open:", use_container_width=True)
            st.divider()
            if st.button("가사 PPT 생성", icon=":material/subtitles:", use_container_width=True):
                if validate_and_refresh_credentials(): merge_and_upload_ppt(st.session_state["cart"], fname)

            if st.session_state.get("ppt_slide_url"):
                with st.container(border=True):
                    st.link_button("가사 열기", st.session_state["ppt_slide_url"], use_container_width=True)
                    st.link_button("가사 폴더 열기", LYRICS_FOLDER_URL, icon=":material/folder_open:", use_container_width=True)

            if st.button("전체 초기화", icon=":material/restart_alt:", use_container_width=True):
                st.session_state.update({"cart": [], "slide_url": None, "ppt_slide_url": None}); st.rerun()
        else: st.caption("곡을 담아주세요")

        st.divider(); st.header("최근 생성 콘티")
        history = db.collection("playlists").order_by("created_at", direction="DESCENDING").limit(5).stream()
        for h_doc in history:
            h, h_id = h_doc.to_dict(), h_doc.id
            with st.expander(f"{h['title']} ({h['created_at'].strftime('%m/%d %H:%M')})"):
                if st.button("콘티 리스트에 담기", key=f"hist_load_{h_id}", icon=":material/playlist_add:", use_container_width=True, type="primary"):
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
                        else: missing_songs.append(s_item["title"])
                    st.session_state["cart"].extend(new_items)
                    if missing_songs: st.warning(f"DB에서 삭제된 곡 제외: {', '.join(missing_songs)}")
                    st.rerun()
                if st.button("기록 삭제", key=f"hist_del_{h_id}", icon=":material/delete:", use_container_width=True): delete_history_dialog(h_id, h['title'])
                for s in h.get("items", []): st.write(f"- {s['title']} ({s.get('selected_key', 'C')})")

    t1, t2 = st.columns([5,1])
    t1.title("Praise Maker")
    if t2.button("찬양곡 추가", icon=":material/add:", type="primary", use_container_width=True):
        st.session_state["page"] = "add_song"; st.rerun()
        
    practice_count = sum(1 for s in all_songs if s.get("status") == "practice")
    view_mode = st.radio(
        "보기", ["정식곡", f"연습곡 ({practice_count})"], horizontal=True, label_visibility="collapsed"
    )
    is_practice_view = view_mode.startswith("연습곡")

    q = st.text_input("검색", placeholder="제목, 태그, Key 검색", label_visibility="collapsed").strip().lower()

    status_songs = [s for s in all_songs if (s.get("status") == "practice") == is_practice_view]
    filtered_songs = [
        s for s in status_songs
        if not q or q in s['title'].lower() or any(q in t.lower() for t in s.get('tags', [])) or q in s.get('start_key', '').lower()
    ]

    ITEMS_PER_PAGE = 10
    total_items = len(filtered_songs)
    
    if total_items == 0:
        st.info("검색 결과가 없거나 등록된 찬양곡이 없습니다.")
    else:
        total_pages = max(1, (total_items - 1) // ITEMS_PER_PAGE + 1)
        if "current_page" not in st.session_state: st.session_state["current_page"] = 1
        if st.session_state["current_page"] > total_pages: st.session_state["current_page"] = total_pages

        start_idx = (st.session_state["current_page"] - 1) * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        page_items = filtered_songs[start_idx:end_idx]
        
        def render_song_card(s):
            with st.container(border=True, key=f"song-card-{s['id']}", gap=None):
                # 유튜브 유무와 상관없이 항상 같은 칸 구성을 써서, 카드마다 수정/삭제 위치가
                # 흔들리지 않도록 합니다. 유튜브 아이콘은 제목 바로 오른쪽(수정/삭제보다 왼쪽)에 고정.
                h1, h_yt, h2, h3 = st.columns([7, 1, 1, 1])
                h1.markdown(f"### {s['title']}")
                if s.get("youtube_url"):
                    h_yt.markdown(
                        f'<a href="{s["youtube_url"]}" target="_blank" title="YouTube" '
                        f'style="display:flex;align-items:center;justify-content:center;height:38px;'
                        f'border:1px solid #E6EEF6;border-radius:8px;background:#F7FBFF;">'
                        f'<img src="https://upload.wikimedia.org/wikipedia/commons/e/ef/Youtube_logo.png" width="20"></a>',
                        unsafe_allow_html=True
                    )
                if h2.button("", key=f"edit_{s['id']}", icon=":material/edit:", help="수정"):
                    st.session_state.update({"editing_song": s, "page": "edit_song"}); st.rerun()
                if h3.button("", key=f"del_{s['id']}", icon=":material/delete:", help="곡 전체 삭제"): delete_confirm_dialog(s['id'], s['title'])
                if s.get("status") == "practice":
                    st.markdown('<span class="pm-badge pm-badge-practice">연습곡</span>', unsafe_allow_html=True)
                usage = usage_stats.get(s["id"])
                if usage:
                    days_ago = (datetime.date.today() - usage["last_used"]).days
                    if days_ago <= RECENT_USE_SHOW_DAYS:
                        when_text = "오늘" if days_ago == 0 else f"{days_ago}일 전"
                        if usage["recent_count"] >= FREQUENT_USE_THRESHOLD:
                            st.markdown(
                                f'<span class="pm-badge pm-badge-warning">최근 자주 사용됨 · 최근 {FREQUENT_USE_WINDOW_DAYS}일간 {usage["recent_count"]}회 · {when_text}</span>',
                                unsafe_allow_html=True
                            )
                        else:
                            st.caption(f"최근 사용: {when_text}")
                if s.get("uploaded_by"): st.caption(f"업로드: {s['uploaded_by']}")
                if s.get("tags"): st.markdown(" ".join([f"`#{tag}`" for tag in s["tags"]]))

                available_keys = list(s.get("keys", {}).keys())
                if not available_keys and s.get("start_key"): available_keys = [s["start_key"]]
                if not available_keys: available_keys = ["C"]

                st.markdown("**보유 중인 Key:** " + " ".join([f"`{k}`" for k in available_keys]))

                chosen_key = available_keys[0]
                if s.get("status") == "practice":
                    if st.button("정식 곡으로 등록", key=f"promote_{s['id']}", icon=":material/school:", use_container_width=True, type="primary"):
                        db.collection("songs").document(s["id"]).update({"status": "official"})
                        st.rerun()
                else:
                    k_select_col, btn_add_col = st.columns([2, 8])
                    chosen_key = k_select_col.selectbox("코드 선택", available_keys, key=f"sel_k_{s['id']}", label_visibility="collapsed")

                    if btn_add_col.button("콘티에 담기", key=f"add_{s['id']}", icon=":material/playlist_add:", use_container_width=True, type="primary"):
                        if not any(item['id'] == s['id'] and item.get('selected_key') == chosen_key for item in st.session_state["cart"]):
                            song_to_cart = deepcopy(s)

                            # --- [🔥 핵심 수정: 선택된 코드에 맞는 이미지로 강제 오버라이드] ---
                            # 사용자가 선택한 key(chosen_key) 안에 개별 image_url이 존재한다면
                            # 카트에 들어갈 복사본 데이터의 image_url을 해당 이미지로 정확하게 갱신해 줍니다.
                            specific_image = s.get("keys", {}).get(chosen_key, {}).get("image_url")
                            if specific_image:
                                song_to_cart["image_url"] = specific_image
                            # ------------------------------------------------------------------

                            song_to_cart["selected_key"] = chosen_key
                            st.session_state["cart"].append(song_to_cart)
                            st.rerun()

                l1, l2 = st.columns(2)
                tgt_img = s.get("image_url", "")
                if "keys" in s and chosen_key in s["keys"]:
                    tgt_img = s["keys"][chosen_key].get("image_url", tgt_img)
                tgt_ppt = s.get("ppt_drive_url", "")

                if tgt_img: l1.markdown(f'<a href="{tgt_img}" target="_blank" style="display:flex;align-items:center;justify-content:center;background-color:#F7FBFF;color:#1E293B;padding:5px 10px;border-radius:8px;text-decoration:none;font-size:13px;border:1px solid #E6EEF6;">악보 이미지 ({chosen_key})</a>', unsafe_allow_html=True)
                if tgt_ppt: l2.markdown(f'<a href="{tgt_ppt}" target="_blank" style="display:flex;align-items:center;justify-content:center;background-color:#F7FBFF;color:#1E293B;padding:5px 10px;border-radius:8px;text-decoration:none;font-size:13px;border:1px solid #E6EEF6;">가사 PPT (공통)</a>', unsafe_allow_html=True)

        for row_start in range(0, len(page_items), 2):
            row_items = page_items[row_start:row_start + 2]
            card_cols = st.columns(2)
            for card_col, s in zip(card_cols, row_items):
                with card_col:
                    render_song_card(s)

        # ---------------------------------------------------------
        # 하단 페이징 네비게이션 컨트롤러 (처음 / 끝 포함 5열 구조)
        # ---------------------------------------------------------
        st.divider()
        p_col_first, p_col_prev, p_col_text, p_col_next, p_col_last = st.columns([0.7, 0.7, 3, 0.7, 0.7])
        
        with p_col_first:
            if st.button("처음", icon=":material/first_page:", disabled=(st.session_state["current_page"] == 1), use_container_width=True):
                st.session_state["current_page"] = 1
                st.rerun()

        with p_col_prev:
            if st.button("이전", icon=":material/chevron_left:", disabled=(st.session_state["current_page"] == 1), use_container_width=True):
                st.session_state["current_page"] -= 1
                st.rerun()

        with p_col_text:
            st.markdown(f"<p style='text-align: center; margin-top: 5px;'><b>{st.session_state['current_page']}</b> / {total_pages} 페이지 (총 {total_items}곡)</p>", unsafe_allow_html=True)

        with p_col_next:
            if st.button("다음", icon=":material/chevron_right:", disabled=(st.session_state["current_page"] == total_pages), use_container_width=True):
                st.session_state["current_page"] += 1
                st.rerun()

        with p_col_last:
            if st.button("끝", icon=":material/last_page:", disabled=(st.session_state["current_page"] == total_pages), use_container_width=True):
                st.session_state["current_page"] = total_pages
                st.rerun()

        # 페이지 번호로 바로 이동 (현재 페이지 중심으로 일부만 표시)
        PAGE_NUM_WINDOW = 7
        half_window = PAGE_NUM_WINDOW // 2
        window_start = max(1, st.session_state["current_page"] - half_window)
        window_end = min(total_pages, window_start + PAGE_NUM_WINDOW - 1)
        window_start = max(1, window_end - PAGE_NUM_WINDOW + 1)
        page_numbers = list(range(window_start, window_end + 1))

        if len(page_numbers) > 1:
            num_cols = st.columns(len(page_numbers))
            for num_col, p in zip(num_cols, page_numbers):
                is_current_page = (p == st.session_state["current_page"])
                if num_col.button(
                    str(p), key=f"page_num_{p}",
                    type="primary" if is_current_page else "secondary",
                    disabled=is_current_page, use_container_width=True
                ):
                    st.session_state["current_page"] = p
                    st.rerun()
