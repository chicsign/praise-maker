import streamlit as st
import datetime
from services.firebase_service import db, bucket

# 앱 설정 (사이드바 활용을 위해 레이아웃을 wide로 변경 고려)
st.set_page_config(page_title="Praise Maker", layout="wide")

# --- 페이지 상태 관리 (Navigation) ---
if 'page' not in st.session_state:
    st.session_state['page'] = 'main'

def go_to_main():
    st.session_state['page'] = 'main'

def go_to_add():
    st.session_state['page'] = 'add_song'

# --- 1. 공통 사이드바 (장바구니) ---
with st.sidebar:
    st.header("🛒 선택된 콘티")
    if 'cart' in st.session_state and st.session_state['cart']:
        st.caption(f"총 {len(st.session_state['cart'])}곡")
        
        # 선택된 곡 리스트 UI
        for idx, item in enumerate(st.session_state['cart']):
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{idx+1}. {item['title']}**")
                c1.caption(f"Key: {item.get('start_key')}")
                if c2.button("❌", key=f"del_{idx}"):
                    st.session_state['cart'].pop(idx)
                    st.rerun()
        
        st.write("")
        if st.button("✨ 콘티 생성 시작", type="primary", use_container_width=True):
            st.info("구글 슬라이드 생성을 시도합니다...")
            
        if st.button("🗑️ 전체 비우기", use_container_width=True):
            st.session_state['cart'] = []
            st.rerun()
    else:
        st.info("목록에서 곡을 담아주세요.")

# --- CASE 1: 새 곡 추가 페이지 ---
if st.session_state['page'] == 'add_song':
    st.button("돌아가기", on_click=go_to_main)
    st.title("➕ 새 곡 추가")
    
    with st.form("add_song_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            new_title = st.text_input("곡 제목")
            new_key = st.selectbox("시작 코드", ["C", "D", "E", "F", "G", "A", "B"])
        with col2:
            new_tags = st.text_input("태그 (쉼표 구분)", placeholder="느린곡, 경배")
            new_youtube = st.text_input("YouTube URL")
        
        st.divider()
        st.subheader("📁 파일 업로드")
        img_file = st.file_uploader("악보 이미지 (JPG, PNG)", type=['jpg', 'jpeg', 'png'])
        ppt_file = st.file_uploader("PPT 파일 (PPTX, PPT)", type=['pptx', 'ppt'])
        
        submitted = st.form_submit_button("Firestore에 저장", use_container_width=True)
        
        if submitted:
            if new_title:
                with st.spinner("파일 및 데이터 저장 중..."):
                    img_url = ""
                    ppt_url = ""
                    
                    if img_file:
                        blob_img = bucket.blob(f"scores/{new_title}_{img_file.name}")
                        blob_img.upload_from_string(img_file.read(), content_type=img_file.type)
                        blob_img.make_public()
                        img_url = blob_img.public_url
                    
                    if ppt_file:
                        blob_ppt = bucket.blob(f"ppts/{new_title}_{ppt_file.name}")
                        blob_ppt.upload_from_string(ppt_file.read(), content_type=ppt_file.type)
                        blob_ppt.make_public()
                        ppt_url = blob_ppt.public_url

                    tag_list = [t.strip() for t in new_tags.split(",")] if new_tags else []
                    db.collection("songs").add({
                        "title": new_title,
                        "start_key": new_key,
                        "tags": tag_list,
                        "youtube_url": new_youtube,
                        "image_url": img_url,
                        "ppt_url": ppt_url,
                        "created_at": datetime.datetime.now()
                    })
                    
                st.success(f"'{new_title}' 추가 완료!")
                st.balloons()
                st.session_state['page'] = 'main'
                st.rerun()
            else:
                st.error("곡 제목은 필수입니다.")

# --- CASE 2: 메인 검색 페이지 ---
else:
    st.title("🎵 Praise Maker")

    # 검색창 및 추가 버튼 레이아웃
    col_sub, col_add = st.columns([4, 1])
    with col_sub:
        st.subheader("🔍 찬양 검색")
    with col_add:
        st.button("➕ 새 곡 추가", on_click=go_to_add, use_container_width=True)

    # 검색어 입력 (제목, 태그, 키 통합 검색)
    search_query = st.text_input(
        "제목, 태그, 또는 시작 키를 입력하세요", 
        placeholder="예: 나의 안에 거하라 / 느린곡 / G키", 
        label_visibility="collapsed"
    ).strip().lower()

    songs_ref = db.collection("songs")
    
    # 1. 일단 최신순으로 데이터를 가져옵니다 (필터링은 파이썬에서 진행)
    # 데이터가 아주 많아지면 Firestore 쿼리를 세분화해야 하지만, 현재는 이 방식이 가장 유연합니다.
    all_songs = songs_ref.order_by("created_at", direction="DESCENDING").stream()
    
    filtered_songs = []
    for doc in all_songs:
        song_data = doc.to_dict()
        song_data['id'] = doc.id  # ID 보관
        
        if not search_query:
            filtered_songs.append(song_data)
            continue
            
        # 검색 필터링 조건 (제목, 태그 리스트, 시작 키 중 하나라도 포함되면 노출)
        title = song_data.get('title', '').lower()
        start_key = song_data.get('start_key', '').lower()
        tags = [t.lower() for t in song_data.get('tags', [])]
        
        if (search_query in title) or \
           (search_query in start_key) or \
           (any(search_query in t for t in tags)):
            filtered_songs.append(song_data)

    # 2. 필터링된 결과 출력
    if not filtered_songs:
        st.info("검색 결과가 없습니다.")
    else:
        for song in filtered_songs:
            with st.container(border=True):
                col_info, col_btn = st.columns([4, 1])
                with col_info:
                    st.markdown(f"### {song.get('title', '제목 없음')}")
                    tags = song.get('tags', [])
                    tag_str = " ".join([f"`#{t}`" for t in tags])
                    st.markdown(f"🔑 **Key**: {song.get('start_key', 'N/A')} | {tag_str}")
                    
                    l_col1, l_col2, l_col3 = st.columns(3)
                    if song.get("youtube_url"):
                        l_col1.link_button("📺 YouTube", song["youtube_url"], use_container_width=True)
                    if song.get("image_url"):
                        l_col2.link_button("🖼️ 악보", song["image_url"], use_container_width=True)
                    if song.get("ppt_url"):
                        l_col3.link_button("📊 PPT", song["ppt_url"], use_container_width=True)

                with col_btn:
                    st.write("") 
                    if st.button("담기", key=f"add_{song['id']}", use_container_width=True, type="secondary"):
                        if 'cart' not in st.session_state: 
                            st.session_state['cart'] = []
                        
                        if not any(item['title'] == song['title'] for item in st.session_state['cart']):
                            st.session_state['cart'].append(song)
                            st.toast(f"'{song['title']}' 담기 완료!")
                            st.rerun() # 사이드바 즉시 갱신
                        else:
                            st.warning("이미 담긴 곡입니다.")