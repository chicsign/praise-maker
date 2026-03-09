import streamlit as st
import datetime
from services.firebase_service import db, bucket

# 1. 앱 기본 설정
st.set_page_config(page_title="Praise Maker", layout="wide")

# --- 페이지 상태 관리 ---
if 'page' not in st.session_state: st.session_state['page'] = 'main'
if 'editing_song' not in st.session_state: st.session_state['editing_song'] = None
if 'cart' not in st.session_state: st.session_state['cart'] = []

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

# --- 1. 사이드바 (Setlist & 순서 변경) ---
with st.sidebar:
    st.header("🛒 선택된 콘티")
    if st.session_state['cart']:
        for idx, item in enumerate(st.session_state['cart']):
            with st.container(border=True):
                # 제목과 삭제 버튼
                head1, head2 = st.columns([4, 1])
                head1.write(f"**{idx+1}. {item['title']}**")
                if head2.button("X", key=f"cart_del_{idx}"):
                    st.session_state['cart'].pop(idx)
                    st.rerun()
                
                # 순서 변경 버튼 (위/아래)
                btn_up, btn_down = st.columns(2)
                if btn_up.button("↑", key=f"up_{idx}", disabled=(idx == 0), use_container_width=True):
                    st.session_state['cart'][idx], st.session_state['cart'][idx-1] = st.session_state['cart'][idx-1], st.session_state['cart'][idx]
                    st.rerun()
                if btn_down.button("↓", key=f"down_{idx}", disabled=(idx == len(st.session_state['cart'])-1), use_container_width=True):
                    st.session_state['cart'][idx], st.session_state['cart'][idx+1] = st.session_state['cart'][idx+1], st.session_state['cart'][idx]
                    st.rerun()
        
        st.write("---")
        if st.button("✨콘티 생성✨", type="primary", use_container_width=True):
            st.info("구글 슬라이드 생성 로직 준비 중...")
        if st.button("비우기", use_container_width=True):
            st.session_state['cart'] = []
            st.rerun()
    else:
        st.caption("곡을 담아주세요.")

# --- CASE 1: 곡 추가/수정 페이지 ---
if st.session_state['page'] in ['add_song', 'edit_song']:
    is_edit = st.session_state['page'] == 'edit_song'
    # 에러 방지용 안전한 변수 할당
    song = st.session_state.get('editing_song') if is_edit else {}
    if song is None: song = {}
    
    if st.button("Back"): 
        go_to_main()
        st.rerun()
        
    st.header("곡 수정" if is_edit else "새 곡 추가")
    
    # --- FORM 시작 ---
    with st.form("song_form"):
        c1, c2 = st.columns(2)
        title_input = c1.text_input("제목", value=song.get('title', ''))
        
        # 키 선택 (기존 값 인덱스 찾기)
        keys = ["C","D","E","F","G","A","B"]
        default_key_idx = keys.index(song.get('start_key', 'C')) if song.get('start_key') in keys else 0
        key_input = c1.selectbox("키", keys, index=default_key_idx)
        tags_input = c2.text_input("태그 (쉼표 구분)", placeholder="느린곡, 경배", value=", ".join(song.get('tags', [])))
        yt_input = c2.text_input("YouTube", value=song.get('youtube_url', ''))
        
        img_f = st.file_uploader("악보 이미지", type=['jpg','png','jpeg'])
        ppt_f = st.file_uploader("PPT 파일", type=['pptx','ppt'])
        
        # --- Missing Submit Button 해결 ---
        submitted = st.form_submit_button("저장하기", use_container_width=True)
        
        if submitted:
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
                    
                go_to_main()
                st.rerun()
            else:
                st.error("제목을 입력해주세요.")

# --- CASE 2: 메인 검색 페이지 ---
else:
    col_t, col_a = st.columns([5, 1])
    col_t.title("🎵Praise Maker")
    if col_a.button("곡 추가", type="primary", use_container_width=True): 
        go_to_add()
        st.rerun()

    query = st.text_input("search", placeholder="제목, 태그, 키 검색", label_visibility="collapsed").strip().lower()
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").limit(50).stream()
    
    for doc in docs:
        s = doc.to_dict(); s['id'] = doc.id
        if not query or (query in s.get('title','').lower()) or (query in s.get('start_key','').lower()) or any(query in t.lower() for t in s.get('tags', [])):
            
            with st.container(border=True):
                # 제목 + 수정/삭제 (이모지)
                header_col, edit_col, del_col = st.columns([8, 0.6, 0.6])
                header_col.markdown(f"### {s['title']}")
                if edit_col.button("수정", key=f"e_{s['id']}"): go_to_edit(s)
                if del_col.button("곡 삭제", key=f"d_{s['id']}"): delete_confirm_dialog(s['id'], s['title'])
                
                # 정보
                st.markdown(f"**Key:** {s['start_key']} | {' '.join([f'`#{t}`' for t in s.get('tags', [])])}")
                
                # 하단 버튼 (Youtube, 악보, PPT)
                l1, l2, l3 = st.columns(3)
                if s.get("youtube_url"): l1.link_button("Youtube", s["youtube_url"], use_container_width=True)
                if s.get("image_url"): l2.link_button("악보", s["image_url"], use_container_width=True)
                if s.get("ppt_url"): l3.link_button("PPT", s["ppt_url"], use_container_width=True)
                
                # 담기 버튼
                if st.button("리스트에 담기", key=f"c_{s['id']}", use_container_width=True, type="primary"):
                    if not any(item['id'] == s['id'] for item in st.session_state['cart']):
                        st.session_state['cart'].append(s)
                        st.rerun()