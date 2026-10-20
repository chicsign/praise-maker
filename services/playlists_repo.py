import datetime

import streamlit as st

from services.firebase_service import db

CACHE_TTL_SECONDS = 300  # 5분: 캐시된 값이 최대 이만큼 오래될 수 있음
FREQUENT_USE_WINDOW_DAYS = 30  # 이 기간 내 사용 횟수를 "최근 N회"로 집계


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_recent_playlists(limit=5):
    docs = db.collection("playlists").order_by("created_at", direction="DESCENDING").limit(limit).stream()
    return [(doc.id, doc.to_dict()) for doc in docs]


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
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


def add_playlist(filename, cart_items, file_url, user_email):
    db.collection("playlists").add({
        "title": filename,
        "items": [{"id": item["id"], "title": item["title"], "selected_key": item.get("selected_key", "C")} for item in cart_items],
        "file_url": file_url,
        "user_email": user_email,
        "created_at": datetime.datetime.now()
    })
    fetch_recent_playlists.clear()
    compute_song_usage_stats.clear()


def delete_playlist(doc_id):
    db.collection("playlists").document(doc_id).delete()
    fetch_recent_playlists.clear()
    compute_song_usage_stats.clear()
