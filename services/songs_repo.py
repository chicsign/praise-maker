import streamlit as st

from services.firebase_service import db

CACHE_TTL_SECONDS = 300  # 5분: 캐시된 목록이 최대 이만큼 오래될 수 있음


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_all_songs():
    docs = db.collection("songs").order_by("created_at", direction="DESCENDING").stream()
    return [doc.to_dict() | {"id": doc.id} for doc in docs]


def get_song(song_id):
    doc = db.collection("songs").document(song_id).get()
    if not doc.exists:
        return None
    return doc.to_dict() | {"id": doc.id}


def add_song(data):
    db.collection("songs").add(data)
    fetch_all_songs.clear()


def update_song(song_id, data):
    db.collection("songs").document(song_id).update(data)
    fetch_all_songs.clear()


def delete_song(song_id):
    db.collection("songs").document(song_id).delete()
    fetch_all_songs.clear()


def promote_to_official(song_id):
    update_song(song_id, {"status": "official"})
