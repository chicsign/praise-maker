import streamlit as st
import datetime
import os
from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides

# 1. 앱 기본 설정 (모든 st 함수 중 최상단에 위치해야 함)
st.set_page_config(page_title="Praise Maker", layout="wide")

# --- OAuth 인증 처리 (URL 파라미터 체크) ---
query_params = st.query_params
if "code" in query_params:
    if "credentials" not in st.session_state:
        try:
            # 1. Flow 객체 재생성
            flow = create_flow()
            
            # 2. [수정] fetch_token 시 authorization_response에 현재 페이지 URL 전체를 전달
            # 로컬 테스트 시 http 임을 명시하기 위한 환경변수 설정 (보통 상단에 배치)
            os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
            
            # Streamlit의 현재 URL 구성을 이용해 전체 응답 URL 재구성
            # (PKCE 미싱 에러 방지를 위한 가장 확실한 방법)
            flow.fetch_token(code=query_params["code"])
            
            creds = flow.credentials
            st.session_state["credentials"] = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes
            }
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"로그인 처리 중 오류 발생: {e}")

# --- 세션 상태 초기화 ---
if 'page' not in st.session_state: st.session_state['page'] = 'main'
if 'editing_song' not in st.session_state: st.session_state['editing_song'] = None
if 'cart' not in st.session_state: st.session_state['cart'] = []

# --- 네비게이션 함수 ---
def go_to_main(): st.session_state.update({"page": "main", "editing_song": None})
def go_to_add(): st.session_state['page'] = 'add_song'
def go_to_edit(song_data):
    st.session_state.update({"editing_song": song_data, "page": "edit_song"})
    st.rerun()

# --- 삭제 확인 모달 ---
@st.dialog("곡 삭제 확인")
def delete_confirm_dialog(song_id, title):
    st.write(f"'{title}' 곡을 삭제하시겠습니까?")
    c1, c2 = st.columns(2)
    if c1.button("삭제", type="primary", use_container_width=True):
        db.collection("songs").document(song_id).delete()
        st.rerun()
    if c2.button("취소", use_container_width=True): st.rerun()

# --- 1. 사이드바 (로그인 & Setlist) ---
with st.sidebar:
    st.header("🔐 인증 관리")
    if "credentials" not in st.session_state:
        st.warning("구글 로그인이 필요합니다.")
        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        st.link_button("🚀 Google 로그인", auth_url, use_container_width=True)
    else:
        st.success("구글 인증 완료")
        if st.button("로그아웃", use_container_width=True):
            del st.session_state["credentials"]
            st.rerun()

    st.divider()
    st.header("🛒 선택된 콘티")
    if st.session_state['cart']:
        for idx, item in enumerate(st.session_state['cart']):
            with st.container(border=True):
                head1, head2 = st.columns([4, 1])
                head1.write(f"**{idx+1}. {item['title']}**")
                if head2.button("X", key=f"cart_del_{idx}"):
                    st.session_state['cart'].pop(idx)
                    st.rerun()
                
                # 순서 변경 버튼
                btn_up, btn_down = st.columns(2)
                if btn_up.button("↑", key=f"up_{idx}", disabled=(idx == 0), use_container_width=True):
                    st.session_state['cart'][idx], st.session_state['cart'][idx-1] = st.session_state['cart'][idx-1], st.session_state['cart'][idx]
                    st.rerun()
                if btn_down.button("↓", key=f"down_{idx}", disabled=(idx == len(st.session_state['cart'])-1), use_container_width=True):
                    st.session_state['cart'][idx], st.session_state['cart'][idx+1] = st.session_state['cart'][idx+1], st.session_state['cart'][idx]
                    st.rerun()
        
        st.write("---")
        # 구글 슬라이드 생성 버튼
        if st.button("✨ 콘티 생성", type="primary", use_container_width=True):
            if "credentials" not in st.session_state:
                st.error("먼저 구글 로그인을 해주세요.")
            else:
                with st.spinner("구글 슬라이드 제작 중..."):
                    try:
                        slide_url = create_praise_slides(st.session_state['cart'])
                        st.success("생성 완료!")
                        st.link_button("📂 슬라이드 열기", slide_url, use_container_width=True)
                    except Exception as e:
                        st.error(f"실패: {e}")
        
        if st.button("리스트 비우기", use_container_width=True):
            st.session_state['cart'] = []
            st.rerun()
    else:
        st.caption("곡을 담아주세요.")

# --- 2. 페이지 본문 분기 ---
if st.session_state['page'] in ['add_song', 'edit_song']:
    # [생략] 작성하신 Case 1 (추가/수정) 로직 그대로 유지
    is_edit = st.session_state['page'] == 'edit_song'
    song = st.session_state.get('editing_song') if is_edit else {}
    if song is None: song = {}
    
    if st.button("Back"): go_to_main(); st.rerun()
    st.header("곡 수정" if is_edit else "새 곡 추가")
    
    with st.form("song_form"):
        c1, c2 = st.columns(2)
        title_input = c1.text_input("제목", value=song.get('title', ''))
        keys = ["C","D","E","F","G","A","B"]
        default_key_idx = keys.index(song.get('start_key', 'C')) if song.get('start_key') in keys else 0
        key_input = c1.selectbox("키", keys, index=default_key_idx)
        tags_input = c2.text_input("태그 (쉼표 구분)", value=", ".join(song.get('tags', [])))
        yt_input = c2.text_input("YouTube", value=song.get('youtube_url', ''))
        
        img_f = st.file_uploader("악보 이미지", type=['jpg','png','jpeg'])
        ppt_f = st.file_uploader("PPT 파일", type=['pptx','ppt'])
        
        if st.form_submit_button("저장하기", use_container_width=True):
            if title_input:
                with st.spinner("처리 중..."):
                    data = {
                        "title": title_input, "start_key": key_input,
                        "tags": [t.strip() for t in tags_input.split(",")] if tags_input else [],
                        "youtube_url": yt_input, "updated_at": datetime.datetime.now()
                    }
                    if img_f:
                        b = bucket.blob(f"scores/{title_input}_{img_f.name}")
                        b.upload_from_string(img_f.read(), content_type=img_f.type); b.make_public()
                        data["image_url"] = b.public_url
                    if ppt_f:
                        b = bucket.blob(f"ppts/{title_input}_{ppt_f.name}")
                        b.upload_from_string(ppt_f.read(), content_type=ppt_f.type); b.make_public()
                        data["ppt_url"] = b.public_url

                    if is_edit: db.collection("songs").document(song['id']).update(data)
                    else: 
                        data["created_at"] = datetime.datetime.now()
                        db.collection("songs").add(data)
                go_to_main(); st.rerun()
            else:
                st.error("제목을 입력해주세요.")

else:
    # [생략] 작성하신 Case 2 (메인 목록) 로직 그대로 유지
    col_t, col_a = st.columns([5, 1])
    col_t.title("🎵 Praise Maker")
    if col_a.button("곡 추가", type="primary", use_container_width=True): 
        go_to_add(); st.rerun()

    query = st.text_input("search", placeholder="제목, 태그, 키 검색", label_visibility="collapsed").strip().lower()
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()
    
    for doc in docs:
        s = doc.to_dict(); s['id'] = doc.id
        if not query or (query in s.get('title','').lower()) or (query in s.get('start_key','').lower()) or any(query in t.lower() for t in s.get('tags', [])):
            with st.container(border=True):
                header_col, edit_col, del_col = st.columns([8, 0.6, 0.6])
                header_col.markdown(f"### {s['title']}")
                if edit_col.button("수정", key=f"e_{s['id']}"): go_to_edit(s)
                if del_col.button("곡 삭제", key=f"d_{s['id']}"): delete_confirm_dialog(s['id'], s['title'])
                
                st.markdown(f"**Key:** {s['start_key']} | {' '.join([f'`#{t}`' for t in s.get('tags', [])])}")
                
                l1, l2, l3 = st.columns(3)
                if s.get("youtube_url"): l1.link_button("Youtube", s["youtube_url"], use_container_width=True)
                if s.get("image_url"): l2.link_button("악보", s["image_url"], use_container_width=True)
                if s.get("ppt_url"): l3.link_button("PPT", s["ppt_url"], use_container_width=True)
                
                if st.button("리스트에 담기", key=f"c_{s['id']}", use_container_width=True, type="primary"):
                    if not any(item['id'] == s['id'] for item in st.session_state['cart']):
                        st.session_state['cart'].append(s)
                        st.rerun()