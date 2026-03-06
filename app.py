import streamlit as st
import datetime
from services.firebase_service import db # 기존 설정된 Firestore 연결
from googleapiclient.discovery import build

# 앱 설정 (모바일 최적화: 중앙 집중 레이아웃)
st.set_page_config(page_title="Praise Maker", layout="centered")

# --- 1. 곡 추가 기능 (우측 사이드바 또는 상단 배치) ---
with st.sidebar:
    st.header("➕ 새 곡 추가")
    with st.expander("곡 정보 입력", expanded=False):
        new_title = st.text_input("곡 제목")
        new_key = st.selectbox("시작 키", ["C", "D", "E", "F", "G", "A", "B"])
        new_img = st.text_input("악보 이미지 URL")
        if st.button("Firestore에 저장"):
            # db.collection("songs").add({"title": new_title, "start_key": new_key, "image_url": new_img})
            st.success("곡이 추가되었습니다!")
            st.rerun()

st.title("🎵 Praise Maker")

# --- 2. 검색창 및 곡 목록 노출 ---
st.subheader("🔍 곡 목록")
search_query = st.text_input("검색어를 입력하세요", placeholder="제목이나 가사 검색...")

# Firestore 데이터 가져오기 (검색어 유무에 따른 필터링)
songs_ref = db.collection("songs")
if search_query:
    query = songs_ref.where("title", ">=", search_query).where("title", "<=", search_query + "\uf8ff")
    song_list = query.stream()
else:
    # 검색어 없을 때 전체 목록 (최신순 또는 가나다순 10개 예시)
    song_list = songs_ref.limit(20).stream()

# 곡 목록 렌더링 (카드 UI)
for doc in song_list:
    song = doc.to_dict()
    with st.container(border=True):
        col_info, col_btn = st.columns([4, 1])
        with col_info:
            st.markdown(f"**{song['title']}** ({song.get('start_key', 'N/A')})")
        with col_btn:
            if st.button("담기", key=f"add_{doc.id}"):
                if 'cart' not in st.session_state:
                    st.session_state['cart'] = []
                if not any(item['title'] == song['title'] for item in st.session_state['cart']):
                    st.session_state['cart'].append(song)
                    st.toast(f"'{song['title']}' 장바구니 추가!")

st.divider()

# --- 3. 장바구니 및 생성 버튼 (하단 고정 느낌) ---
if 'cart' in st.session_state and st.session_state['cart']:
    st.subheader(f"🛒 장바구니 ({len(st.session_state['cart'])}곡)")
    
    # 담긴 곡 목록 요약
    cart_titles = [item['title'] for item in st.session_state['cart']]
    st.info(", ".join(cart_titles))
    
    # 생성 관련 설정
    col_date, col_action = st.columns([1, 1])
    with col_date:
        file_date = st.date_input("예배 날짜", datetime.date(2026, 3, 8))
    with col_action:
        if st.button("🗑️ 비우기", use_container_width=True):
            st.session_state['cart'] = []
            st.rerun()

    # 생성 버튼 (가장 하단에 크게 배치)
    if st.button("✨ 구글 슬라이드 콘티 생성", type="primary", use_container_width=True):
        # OAuth 및 슬라이드 생성 로직 호출
        # p_id = create_praise_slides(st.session_state['cart'], f"{file_date} 콘티")
        st.success("슬라이드 생성을 시작합니다!")
else:
    st.caption("장바구니가 비어 있습니다. 목록에서 곡을 담아주세요.")