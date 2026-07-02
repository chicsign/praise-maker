import os
import datetime
import streamlit as st

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile"
]

CLIENT_CONFIG = {
    "web": {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token"
    }
}

def create_flow():
    redirect_uri = os.environ.get("OAUTH_REDIRECT_URI", "http://localhost:8501").strip()
    return Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, redirect_uri=redirect_uri)

def get_credentials():
    if "credentials" not in st.session_state or st.session_state["credentials"] is None:
        return None
    creds_data = st.session_state["credentials"]
    return Credentials(
        token=creds_data["token"],
        refresh_token=creds_data["refresh_token"],
        token_uri=creds_data["token_uri"],
        client_id=creds_data["client_id"],
        client_secret=creds_data["client_secret"],
        scopes=creds_data["scopes"]
    )

# [수정] folder_id 매개변수 추가 (app.py에서 전달받기 위함)
def create_praise_slides(cart_items, file_name, creds, folder_id, show_title_text=True):
    creds = get_credentials()
    if not creds: return None
    
    slides_service = build("slides", "v1", credentials=creds, static_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, static_discovery=False)

    if not file_name:
        file_name = f"콘티_{datetime.datetime.now().strftime('%y%m%d')}"
    
    # [수정] 환경변수 고정이 아닌, 매개변수로 받은 folder_id를 사용하도록 변경
    file_metadata = {
        "name": file_name,
        "mimeType": "application/vnd.google-apps.presentation",
        "parents": [folder_id] if folder_id else []
    }

    file = drive_service.files().create(body=file_metadata, fields="id").execute()
    presentation_id = file["id"]

    # [수정] 초기 생성 시 자동 생성되는 '첫 번째 빈 슬라이드' ID 확보 (나중에 삭제용)
    full_presentation = slides_service.presentations().get(presentationId=presentation_id).execute()
    first_slide_id = full_presentation.get('slides')[0]['objectId']

    requests = []
    image_number = 1
    
    # -------------------------------------------------------------
    # [기능 고도화] 수동 배치(is_full_page) 값에 따른 유연한 슬라이더 생성 루프
    # -------------------------------------------------------------
    processed_items = 0
    total_items = len(cart_items)
    insertion_index = 0

    while processed_items < total_items:
        item = cart_items[processed_items]
        page_id = f"page_{processed_items}_{datetime.datetime.now().microsecond}"
        
        # 새 슬라이드 한 장 추가
        requests.append({
            "createSlide": {
                "objectId": page_id,
                "insertionIndex": str(insertion_index),
                "slideLayoutReference": {"predefinedLayout": "BLANK"}
            }
        })
        insertion_index += 1

       # 16:9 와이드 프레젠테이션 기본 해상도 크기 기준 정렬 (가로 약 720 PT x 세로 405 PT)
        # 만약 사용자가 '전체 페이지 V' 체크박스를 선택했다면 단독으로 1페이지 전체(가로 꽉 차게) 할당
        if item.get("is_full_page", False):
            # 악보 이미지 배치 (왼쪽으로 90도 회전, 세로 405PT 기준 비율 유지, 왼쪽 하단 정렬)
            if item.get("image_url"):
                requests.append({
                    "createImage": {
                        "url": item["image_url"],
                        "elementProperties": {
                            "pageObjectId": page_id,
                            # [💡 핵심 교정] 비율이 깨지지 않도록 하기 위해 구글 슬라이드 크기 자체를 
                            # 회전 후 세로 높이가 될 405 PT를 기준으로 정사각형(405x405)으로 임시 제한합니다.
                            # 이렇게 해야 구글 엔진이 원본 비율을 깨지 않고 순수하게 90도만 돌려줍니다.
                            "size": {
                                "width": {"magnitude": 405, "unit": "PT"}, 
                                "height": {"magnitude": 405, "unit": "PT"}
                            },
                            # 순수 왼쪽 90도 회전 및 왼쪽 하단 원점 정렬 행렬
                            # 돌린 후 슬라이드 왼쪽 하단에 딱 붙도록 translateX를 높이만큼(405) 밀어줍니다.
                            "transform": {
                                "scaleX": 0.0,
                                "scaleY": 0.0,
                                "shearX": 1.0,   # 가로 비율 왜곡 제거 (순수 1:1 회전축 유지)
                                "shearY": -1.0,  # 세로 비율 왜곡 제거 (순수 1:1 회전축 유지)
                                "translateX": 405, # 오른쪽 상단으로 튕기지 않고 왼쪽 하단 기준점에 안착하도록 보정
                                "translateY": 0,
                                "unit": "PT"
                            }
                        }
                    }
                })
                
                # 순서 넘버링 박스 레이아웃 지정 (왼쪽 하단에 안착 및 왼쪽으로 90도 회전)
                label_id = f"label_{image_number}"
                requests.append({
                    "createShape": {
                        "objectId": label_id, 
                        "shapeType": "TEXT_BOX", 
                        "elementProperties": {
                            "pageObjectId": page_id, 
                            "size": {
                                "width": {"magnitude": 40, "unit": "PT"}, 
                                "height": {"magnitude": 40, "unit": "PT"}
                            }, 
                            # 왼쪽 아래 구석(translateX: 15, translateY: 350 지점)에 딱 붙어서 글자가 왼쪽으로 눕도록 배치
                            "transform": {
                                "scaleX": 0.0,
                                "scaleY": 0.0,
                                "shearX": 1.0,
                                "shearY": -1.0,
                                "translateX": 15, 
                                "translateY": 350, 
                                "unit": "PT"
                            }
                        }
                    }
                })
                requests.append({"insertText": {"objectId": label_id, "text": str(image_number)}})
                requests.append({"updateTextStyle": {"objectId": label_id, "style": {"fontSize": {"magnitude": 22, "unit": "PT"}, "bold": True}, "textRange": {"type": "ALL"}, "fields": "fontSize,bold"}})
                image_number += 1
                
            processed_items += 1  # 1개 곡만 처리하고 다음 페이지 분기로 이동
        
        # 체크박스가 선택되지 않았다면 기존처럼 가로폭 절반 크기로 2개씩 분할 정렬
        else:
            # 1. 왼쪽 이미지 배치
            if item.get("image_url"):
                requests.append({
                    "createImage": {
                        "url": item["image_url"],
                        "elementProperties": {
                            "pageObjectId": page_id,
                            "size": {"width": {"magnitude": 360, "unit": "PT"}, "height": {"magnitude": 405, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 0, "translateY": 0, "unit": "PT"}
                        }
                    }
                })
                left_label_id = f"left_label_{image_number}"
                requests.append({"createShape": {"objectId": left_label_id, "shapeType": "TEXT_BOX", "elementProperties": {"pageObjectId": page_id, "size": {"width": {"magnitude": 30, "unit": "PT"}, "height": {"magnitude": 30, "unit": "PT"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 10, "translateY": 25, "unit": "PT"}}}})
                requests.append({"insertText": {"objectId": left_label_id, "text": str(image_number)}})
                requests.append({"updateTextStyle": {"objectId": left_label_id, "style": {"fontSize": {"magnitude": 18, "unit": "PT"}, "bold": True}, "textRange": {"type": "ALL"}, "fields": "fontSize,bold"}})
                image_number += 1

            # 2. 다음 곡 항목을 검사하여 우측 분할 영역에 채워 넣을 수 있는지 체크 (우측 곡도 반 페이지 세팅이어야 함)
            if processed_items + 1 < total_items and not cart_items[processed_items + 1].get("is_full_page", False):
                next_item = cart_items[processed_items + 1]
                if next_item.get("image_url"):
                    requests.append({
                        "createImage": {
                            "url": next_item["image_url"],
                            "elementProperties": {
                                "pageObjectId": page_id,
                                "size": {"width": {"magnitude": 360, "unit": "PT"}, "height": {"magnitude": 405, "unit": "PT"}},
                                "transform": {"scaleX": 1, "scaleY": 1, "translateX": 360, "translateY": 0, "unit": "PT"}
                            }
                        }
                    })
                    right_label_id = f"right_label_{image_number}"
                    requests.append({"createShape": {"objectId": right_label_id, "shapeType": "TEXT_BOX", "elementProperties": {"pageObjectId": page_id, "size": {"width": {"magnitude": 30, "unit": "PT"}, "height": {"magnitude": 30, "unit": "PT"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 370, "translateY": 25, "unit": "PT"}}}})
                    requests.append({"insertText": {"objectId": right_label_id, "text": str(image_number)}})
                    requests.append({"updateTextStyle": {"objectId": right_label_id, "style": {"fontSize": {"magnitude": 18, "unit": "PT"}, "bold": True}, "textRange": {"type": "ALL"}, "fields": "fontSize,bold"}})
                    image_number += 1
                processed_items += 2  # 양쪽 2개 아이템 세트 동시 차감 처리
            else:
                processed_items += 1  # 우측에 놓을 곡이 없거나 다음 곡이 전체 화면 배치형이면 1개만 소모 후 마감

        # [버그 수정 완료] 이미지 개체 생성이 완전히 끝난 후(가장 아래쪽 레이어), 
        # 맨 마지막에 텍스트 상자 생성 명령을 누적하여 Z-Index상 텍스트가 항상 이미지 위(앞)에 오도록 수정
        if processed_items - (2 if not item.get("is_full_page", False) and processed_items % 2 == 0 else 1) == 0 and show_title_text:
            title_box_id = f"title_box_{datetime.datetime.now().microsecond}"
            requests.append({
                "createShape": {
                    "objectId": title_box_id, "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": page_id,
                        "size": {"width": {"magnitude": 400, "unit": "PT"}, "height": {"magnitude": 30, "unit": "PT"}},
                        "transform": {"scaleX": 1, "scaleY": 1, "translateX": 160, "translateY": 5, "unit": "PT"}
                    }
                }
            })
            requests.append({"insertText": {"objectId": title_box_id, "text": file_name}})
            requests.append({"updateTextStyle": {"objectId": title_box_id, "style": {"fontSize": {"magnitude": 10, "unit": "PT"}, "bold": True}, "textRange": {"type": "ALL"}, "fields": "fontSize,bold"}})
            requests.append({"updateParagraphStyle": {"objectId": title_box_id, "style": {"alignment": "CENTER"}, "textRange": {"type": "ALL"}, "fields": "alignment"}})

    # [수정] 모든 페이지 생성이 끝난 후, 맨 처음에 있던 자동 생성 슬라이드 삭제
    requests.append({"deleteObject": {"objectId": first_slide_id}})

    if requests:
        slides_service.presentations().batchUpdate(presentationId=presentation_id, body={"requests": requests}).execute()

    return f"https://docs.google.com/presentation/d/{presentation_id}"
