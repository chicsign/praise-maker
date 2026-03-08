import os
import json
import firebase_admin
from firebase_admin import credentials, firestore, storage

def init_firebase():
    # 1. 환경 변수에서 JSON 문자열 가져오기
    service_account_info = os.environ.get("SERVICE_ACCOUNT_JSON")
    
    if not service_account_info:
        # 로컬 개발 환경용 (파일이 있을 때만 실행)
        if os.path.exists("serviceAccountKey.json"):
            cred = credentials.Certificate("serviceAccountKey.json")
        else:
            raise ValueError("Firebase 인증 정보가 없습니다. 환경 변수나 JSON 파일을 확인하세요.")
    else:
        # 2. 환경 변수에 저장된 한 줄 JSON 문자열을 딕셔너리로 변환
        info_dict = json.loads(service_account_info)
        cred = credentials.Certificate(info_dict)

    # Firebase 앱 초기화 (중복 방지)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {
            'storageBucket': 'praisemaker-8cc81.firebasestorage.app'
        })
    
    return firestore.client(), storage.bucket()

db, bucket = init_firebase()