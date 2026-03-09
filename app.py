import streamlit as st
import datetime
from services.firebase_service import db, bucket

# 앱 설정 (모바일 최적화)
st.set_page_config(page_title="Praise Maker", layout="centered")

# --- 페이지 상태 관리 (Navigation) ---
if 'page' not in st.session_state:
    st.session_state['page'] = 'main'

def go_to_main():
    st.session_state['page'] = 'main'
 

def go_to_add():
    st.session_state['page'] = 'add_song'


# --- CASE 1: 새 곡 추가 페이지 ---
if st.session_state['page'] == 'add_song':
    st.button("⬅️ 돌아가기", on_click=go_to_main)
    st.title("➕ 새 곡 추가")
    
    with st.form("add_song_form", clear_on_submit=True):
        new_title = st.text_input("곡 제목")
        new_key = st.selectbox("시작 키", ["C", "D", "E", "F", "G", "A", "B"])
        new_tags = st.text_input("태그 (쉼표로 구분)", placeholder="느린곡, 경배")
        new_youtube = st.text_input("YouTube URL")
        
        # --- 파일 업로드 필드 추가 ---
        img_file = st.file_uploader("악보 이미지 업로드 (JPG, PNG)", type=['jpg', 'jpeg', 'png'])
        ppt_file = st.file_uploader("PPT 파일 업로드 (PPTX)", type=['pptx', 'ppt'])
        
        submitted = st.form_submit_button("Firestore에 저장", use_container_width=True)
        
        if submitted:
            if new_title:
                with st.spinner("파일 및 데이터 저장 중..."):
                    img_url = ""
                    ppt_url = ""
                    
                    # 1. 이미지 업로드 처리
                    if img_file:
                        blob_img = bucket.blob(f"scores/{new_title}_{img_file.name}")
                        blob_img.upload_from_string(img_file.read(), content_type=img_file.type)
                        blob_img.make_public()
                        img_url = blob_img.public_url
                    
                    # 2. PPT 업로드 처리
                    if ppt_file:
                        blob_ppt = bucket.blob(f"ppts/{new_title}_{ppt_file.name}")
                        blob_ppt.upload_from_string(ppt_file.read(), content_type=ppt_file.type)
                        blob_ppt.make_public()
                        ppt_url = blob_ppt.public_url

                    # 3. Firestore 데이터 저장
                    tag_list = [t.strip() for t in new_tags.split(",")] if new_tags else []
                    db.collection("songs").add({
                        "title": new_title,
                        "start_key": new_key,
                        "tags": tag_list,
                        "youtube_url": new_youtube,
                        "image_url": img_url,  # 업로드된 URL 저장
                        "ppt_url": ppt_url,    # 업로드된 URL 저장
                        "created_at": datetime.datetime.now()
                    })
                    
                st.success(f"'{new_title}' 추가 완료!")
                st.balloons()
                # 상태 변경 후 메인으로 이동 (rerun은 if문 밖이나 콜백 미사용 시 직접 호출)
                st.session_state['page'] = 'main'
                st.rerun()
            else:
                st.error("곡 제목은 필수입니다.")

# --- CASE 2: 메인 검색 페이지 ---
else:
    st.title("🎵 Praise Maker")

    # --- 검색창 및 추가 버튼 레이아웃 ---
    col_sub, col_add = st.columns([4, 1])
    with col_sub:
        st.subheader("🔍 찬양 검색")
    with col_add:
        # 우측 상단에 추가 버튼 배치
        st.button("➕ 추가", on_click=go_to_add, use_container_width=True)

    search_query = st.text_input("제목이나 태그를 입력하세요", placeholder="예: 나의 안에 거하라", label_visibility="collapsed")

    songs_ref = db.collection("songs")

    # 검색 로직
    if search_query:
        song_list = songs_ref.where("title", ">=", search_query).where("title", "<=", search_query + "\uf8ff").stream()
    else:
        song_list = songs_ref.order_by("created_at", direction="DESCENDING").limit(20).stream()

    # 곡 목록 렌더링
    for doc in song_list:
        song = doc.to_dict()
        with st.container(border=True):
            col_info, col_btn = st.columns([3, 1])
            with col_info:
                st.markdown(f"### {song.get('title', '제목 없음')}")
                tags = song.get('tags', [])
                tag_str = " ".join([f"`#{t}`" for t in tags])
                st.markdown(f"🔑 **Key**: {song.get('start_key', 'N/A')} | {tag_str}")
                
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
        for idx, item in enumerate(st.session_state['cart']):
            c1, c2 = st.columns([5, 1])
            c1.write(f"{idx+1}. {item['title']} ({item.get('start_key')})")
            if c2.button("❌", key=f"del_{idx}"):
                st.session_state['cart'].pop(idx)
        
        st.write("")
        if st.button("✨ 콘티 생성", type="primary", use_container_width=True):
            st.info("구글 슬라이드 생성을 시도합니다...")
    else:
        st.caption("선택된 곡이 없습니다.")