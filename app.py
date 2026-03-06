import streamlit as st
import datetime
from services.firebase_service import db

# 앱 설정 (모바일 최적화)
st.set_page_config(page_title="Praise Maker", layout="centered")

# --- 1. 사이드바: 새 곡 추가 (실제 필드 반영) ---
with st.sidebar:
    st.header("➕ 새 곡 추가")
    with st.expander("상세 정보 입력", expanded=False):
        new_title = st.text_input("곡 제목")
        new_key = st.selectbox("시작 키", ["C", "D", "E", "F", "G", "A", "B"])
        new_tags = st.text_input("태그 (쉼표로 구분)", placeholder="느린곡, 경배")
        new_youtube = st.text_input("YouTube URL")
        
        if st.button("Firestore에 저장"):
            if new_title:
                tag_list = [t.strip() for t in new_tags.split(",")] if new_tags else []
                db.collection("songs").add({
                    "title": new_title,
                    "start_key": new_key,
                    "tags": tag_list,
                    "youtube_url": new_youtube,
                    "created_at": datetime.datetime.now(),
                    "ppt_url": "",
                    "image_url": "" # 실제 구현 시 파일 업로드 로직 연결 필요
                })
                st.success(f"'{new_title}' 추가 완료!")
                st.rerun()

st.title("🎵 Praise Maker")

# --- 2. 검색 및 곡 목록 (카드 UI 보강) ---
st.subheader("🔍 찬양 검색")
search_query = st.text_input("제목이나 태그를 입력하세요", placeholder="예: 나의 안에 거하라")

songs_ref = db.collection("songs")

# 검색 로직 (Firestore 쿼리)
if search_query:
    # 제목 기준 검색
    song_list = songs_ref.where("title", ">=", search_query).where("title", "<=", search_query + "\uf8ff").stream()
else:
    # 전체 목록 (최신순)
    song_list = songs_ref.order_by("created_at", direction="DESCENDING").limit(20).stream()

for doc in song_list:
    song = doc.to_dict()
    with st.container(border=True):
        col_info, col_btn = st.columns([3, 1])
        with col_info:
            st.markdown(f"### {song.get('title', '제목 없음')}")
            # 태그 및 정보 표시
            tags = song.get('tags', [])
            tag_str = " ".join([f"`#{t}`" for t in tags])
            st.markdown(f"🔑 **Key**: {song.get('start_key', 'N/A')} | {tag_str}")
            
            # 유튜브/PPT 링크 버튼 (데이터 있을 때만 노출)
            l_col1, l_col2 = st.columns(2)
            if song.get("youtube_url"):
                l_col1.link_button("📺 YouTube", song["youtube_url"])
            if song.get("ppt_url"):
                l_col2.link_button("📊 PPT", song["ppt_url"])

        with col_btn:
            if st.button("담기", key=f"add_{doc.id}", use_container_width=True):
                if 'cart' not in st.session_state: st.session_state['cart'] = []
                if not any(item['title'] == song['title'] for item in st.session_state['cart']):
                    st.session_state['cart'].append(song)
                    st.toast(f"'{song['title']}' 추가됨")

st.divider()

# --- 3. 장바구니 및 생성 기능 ---
if 'cart' in st.session_state and st.session_state['cart']:
    st.subheader(f"🛒 선택된 곡 ({len(st.session_state['cart'])})")
    
    # 선택된 곡 리스트 UI
    for idx, item in enumerate(st.session_state['cart']):
        c1, c2 = st.columns([5, 1])
        c1.write(f"{idx+1}. {item['title']} ({item.get('start_key')})")
        if c2.button("❌", key=f"del_{idx}"):
            st.session_state['cart'].pop(idx)
            st.rerun()
    
    st.write("")
    if st.button("✨ 2TB 드라이브에 콘티 생성", type="primary", use_container_width=True):
        st.info("구글 슬라이드 생성을 시도합니다...")
else:
    st.caption("선택된 곡이 없습니다.")