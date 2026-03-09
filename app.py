import streamlit as st
import datetime
import os
import json
from services.firebase_service import db, bucket
from services.google_slides_service import create_flow, create_praise_slides
from google.oauth2.credentials import Credentials

# [보안] 로컬(HTTP) 환경 테스트 허용
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# 1. 앱 기본 설정 (최상단 고정)
st.set_page_config(page_title="Praise Maker", layout="wide")

# --- [중요] 세션 상태 초기화 및 로그인 유지 로직 ---
if "credentials" not in st.session_state:
    st.session_state["credentials"] = None
if 'page' not in st.session_state: st.session_state['page'] = 'main'
if 'cart' not in st.session_state: st.session_state['cart'] = []
if 'slide_url' not in st.session_state: st.session_state['slide_url'] = None

if st.query_params.get("code") and st.session_state["credentials"] is None:
    try:
        flow = create_flow()
        flow.fetch_token(code=st.query_params["code"])
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


def get_google_credentials():
    if st.session_state.get("credentials") is None:
        return None

    return Credentials(
        token=st.session_state["credentials"]["token"],
        refresh_token=st.session_state["credentials"]["refresh_token"],
        token_uri=st.session_state["credentials"]["token_uri"],
        client_id=st.session_state["credentials"]["client_id"],
        client_secret=st.session_state["credentials"]["client_secret"],
        scopes=st.session_state["credentials"]["scopes"]
    )

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

# --- 1. 사이드바 (인증 관리 & 장바구니) ---
with st.sidebar:
    st.header("🔐 인증 관리")
    if st.session_state["credentials"] is None:
        st.warning("구글 로그인이 필요합니다.")
        flow = create_flow()
        auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        
        # [디자인 개선] 정석 구글 스타일 로그인 버튼

        st.markdown(f"""
        <a href="{auth_url}" target="_self" style="text-decoration: none;">
            <div style="
                display: flex;
                align-items: center;
                justify-content: center;
                background-color: #ffffff;
                color: #757575;
                border-radius: 4px;
                border: 1px solid #dadce0;
                padding: 10px 15px;
                font-family: 'Roboto', arial, sans-serif;
                font-weight: 500;
                cursor: pointer;
                transition: background-color .2s, box-shadow .2s;
                box-shadow: 0 1px 1px 0 rgba(66,133,244,.15);
                margin-bottom: 10px;
            " onmouseover="this.style.backgroundColor='#f8f9fa'; this.style.boxShadow='0 1px 3px 1px rgba(66,133,244,.3)';" 
               onmouseout="this.style.backgroundColor='#ffffff'; this.style.boxShadow='0 1px 1px 0 rgba(66,133,244,.15)';"
            >
                <img src="https://fonts.gstatic.com/s/i/productlogos/googleg/v6/24px.svg" width="20px" height="20px" style="margin-right: 12px;">
                Google 계정으로 로그인
            </div>
        </a>
        """, unsafe_allow_html=True)
    else:
        st.success("✅ 구글 인증 완료")
        if st.button("로그아웃", use_container_width=True):
            st.session_state["credentials"] = None
            st.session_state['slide_url'] = None 
            st.rerun()

    st.divider()
    st.header("🛒 선택된 콘티")
    
    # 생성 성공
    if st.session_state['slide_url']:
        st.success("🎉 콘티 생성 성공!")
        st.link_button("📂 슬라이드 열기", st.session_state['slide_url'], use_container_width=True)
        if st.button("비우기", use_container_width=True):
            st.session_state['slide_url'] = None
            st.rerun()
        st.divider()

    if st.session_state['cart']:
        default_filename = f"찬양콘티_{datetime.datetime.now().strftime('%y%m%d')}"
        custom_filename = st.text_input("📄 생성될 파일명", value=default_filename)
        
        for idx, item in enumerate(st.session_state['cart']):
            with st.container(border=True):
                head1, head2 = st.columns([4, 1])
                head1.write(f"**{idx+1}. {item['title']}**")
                if head2.button("X", key=f"cart_del_{idx}"):
                    st.session_state['cart'].pop(idx)
                    st.rerun()
                
                btn_up, btn_down = st.columns(2)
                if btn_up.button("↑", key=f"up_{idx}", disabled=(idx == 0), use_container_width=True):
                    st.session_state['cart'][idx], st.session_state['cart'][idx-1] = st.session_state['cart'][idx-1], st.session_state['cart'][idx]
                    st.rerun()
                if btn_down.button("↓", key=f"down_{idx}", disabled=(idx == len(st.session_state['cart'])-1), use_container_width=True):
                    st.session_state['cart'][idx], st.session_state['cart'][idx+1] = st.session_state['cart'][idx+1], st.session_state['cart'][idx]
                    st.rerun()
        
        st.write("---")
        
        if st.button("✨ 콘티 생성", type="primary", use_container_width=True):
            if st.session_state["credentials"] is None:
                st.error("먼저 구글 로그인을 해주세요.")
            else:
                with st.spinner("구글 슬라이드 제작 중..."):
                    try:
                        # 1. 슬라이드 생성
                        creds = get_google_credentials()

                        slide_url = create_praise_slides(
                            st.session_state['cart'],
                            custom_filename,
                            creds
                        )
                        
                        # 2. 성공 시 즉시 장바구니 비우기
                        st.session_state['cart'] = []
                        
                        # 3. 결과 URL 저장 (UI에서 성공 메시지를 띄우기 위함)
                        st.session_state['slide_url'] = slide_url
                        
                        # 4. 화면 갱신
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"실패: {e}")
        
        if st.button("비우기", use_container_width=True):
            st.session_state['cart'] = []
            st.rerun()
    elif not st.session_state['slide_url']:
        st.caption("곡을 담아주세요.")

# --- 2. 메인 화면 로직 (추가/수정/검색) ---
if st.session_state['page'] in ['add_song', 'edit_song']:
    is_edit = st.session_state['page'] == 'edit_song'
    song = st.session_state.get('editing_song', {})
    
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
                        b = bucket.blob(f"songs/{title_input}_{img_f.name}")
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
                button_style = """
                display:flex;
                align-items:center;
                justify-content:center;
                background-color:#F0F2F6;
                color:#262730;
                padding:8px;
                border-radius:6px;
                text-decoration:none;
                font-weight:600;
                border:1px solid #E6E9EF;
                height:38px;
                """

                if s.get("youtube_url"):
                    l1.markdown(f"""
                    <a href="{s["youtube_url"]}" target="_blank" style="{button_style}">
                    <img src="https://upload.wikimedia.org/wikipedia/commons/7/75/YouTube_social_white_squircle_%282017%29.svg"
                    width="18" style="margin-right:8px;">
                    YouTube
                    </a>
                    """, unsafe_allow_html=True)

                if s.get("image_url"):
                    l2.markdown(f"""
                    <a href="{s["image_url"]}" target="_blank" style="{button_style}">
                    악보
                    </a>
                    """, unsafe_allow_html=True)

                if s.get("ppt_url"):
                    l3.markdown(f"""
                    <a href="{s["ppt_url"]}" target="_blank" style="{button_style}">
                    PPTX
                    </a>
                    """, unsafe_allow_html=True)
                
                if st.button("리스트에 담기", key=f"c_{s['id']}", use_container_width=True, type="primary"):
                    if not any(item['id'] == s['id'] for item in st.session_state['cart']):
                        st.session_state['cart'].append(s)
                        st.rerun()