import firebase_admin
from firebase_admin import credentials, firestore, storage

def init_firebase():
    # 중복 초기화 방지 (안드로이드의 Singleton 패턴과 유사)
    if not firebase_admin._apps:
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred, {
            # 아까 확인한 정확한 주소를 넣으세요
            'storageBucket': 'praisemaker-8cc81.firebasestorage.app' 
        })
    return firestore.client(), storage.bucket()

# 중요: 여기서 함수를 실행해서 변수에 할당해야 다른 파일에서 import가 가능합니다.
db, bucket = init_firebase()