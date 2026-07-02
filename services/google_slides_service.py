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

def create_praise_slides(cart_items, file_name, creds, folder_id, show_title_text=True):
    creds = get_credentials()
    if not creds: return None
    
    slides_service = build("slides", "v1", credentials=creds, static_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, static_discovery=False)

    if not file_name:
        file_name = f"콘티_{datetime.datetime.now().strftime('%y%m%d')}"
    
    file_metadata = {
        "name": file_name,
        "mimeType": "application/vnd.google-apps.presentation",
        "parents": [folder_id] if folder_id else []
    }

    file = drive_service.files().create(body=file_metadata, fields="id").execute()
    presentation_id = file["id"]

    full_presentation = slides_service.presentations().get(presentationId=presentation_id).execute()
    first_slide_id = full_presentation.get('slides')[0]['objectId']

    requests = []
    image_number = 1
    
    processed_items = 0
    total_items = len(cart_items)
    insertion_index = 0

    while processed_items < total_items:
        item = cart_items[processed_items]
        page_id = f"page_{processed_items}_{datetime.datetime.now().microsecond}"
        
        requests.append({
            "createSlide": {
                "objectId": page_id,
                "insertionIndex": str(insertion_index),
                "slideLayoutReference": {"predefinedLayout": "BLANK"}
            }
        })
        insertion_index += 1

        # 16:9 와이드 프레젠테이션 기본 해상도 크기 기준 정렬 (가로 약 720 PT x 세로 405 PT)
        if item.get("is_full_page", False):
            # 악보 이미지 배치 (왼쪽으로 90도 회전, 세로 405PT 기준 정비율 정밀 축소/확대, 왼쪽 하단 밀착 배치)
            if item.get("image_url"):
                requests.append({
                    "createImage": {
                        "url": item["image_url"],
                        "elementProperties": {
                            "pageObjectId": page_id,
                            # [🔥 핵심 피드백 반영] 비율이 깨지지 않도록 원래 세로형 악보 규격(1:1.4 비율)을 정밀 대입합니다.
                            "size": {
                                "width": {"magnitude": 405, "unit": "PT"},  # 회전 후 세로 높이가 될 축 (405 PT)
                                "height": {"magnitude": 570, "unit": "PT"}  # 회전 후 가로 너비가 될 축 (A4 비율 보존 570 PT)
                            },
                            # 수학적으로 정밀 유도한 아핀 회전 행렬 대입 (translateX=0, translateY=405)
                            # 이를 통해 원본 비율 왜곡 없이 정확하게 좌하단 (0, 405) 영역에 안착합니다.
                            "transform": {
                                "scaleX": 0.0,
                                "scaleY": 0.0,
                                "shearX": 1.0,
                                "shearY": -1.0,
                                "translateX": 0,
                                "translateY": 405,
                                "unit": "PT"
                            }
                        }
                    }
                })
                
                # 순서 넘버링 박스 레이아웃 지정 (왼쪽 하단에 정확히 안착 및 왼쪽으로 90도 회전)
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
                
            processed_items += 1
        
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

            # 2. 우측 곡 처리
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
                processed_items += 2
            else:
                processed_items += 1

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

    requests.append({"deleteObject": {"objectId": first_slide_id}})

    if requests:
        slides_service.presentations().batchUpdate(presentationId=presentation_id, body={"requests": requests}).execute()

    return f"https://docs.google.com/presentation/d/{presentation_id}"
