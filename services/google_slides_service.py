import os
import datetime
import streamlit as st

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive"
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
    redirect_uri = os.environ.get(
        "OAUTH_REDIRECT_URI",
        "http://localhost:8501"
    ).strip()

    return Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

def get_credentials():
    if (
        "credentials" not in st.session_state or
        st.session_state["credentials"] is None
    ):
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

def create_praise_slides(
    cart_items,
    file_name,
    creds,
    show_title_text=True
):
    creds = get_credentials()

    if not creds:
        return None
    
    slides_service = build(
        "slides",
        "v1",
        credentials=creds,
        static_discovery=False
    )

    drive_service = build(
        "drive",
        "v3",
        credentials=creds,
        static_discovery=False
    )

    if not file_name:
        file_name = f"콘티_{datetime.datetime.now().strftime('%y%m%d')}"
    
    file_metadata = {
        "name": file_name,
        "mimeType": "application/vnd.google-apps.presentation",
        "parents": (
            [os.environ.get("FOLDER_ID")]
            if os.environ.get("FOLDER_ID")
            else []
        )
    }

    file = drive_service.files().create(
        body=file_metadata,
        fields="id"
    ).execute()

    presentation_id = file["id"]

    requests = []

    # 이미지 번호 카운터
    image_number = 1

    # -------------------------------
    # 2분할 배치 로직
    # -------------------------------
    for i in range(0, len(cart_items), 2):

        page_id = f"page_{i}_{datetime.datetime.now().microsecond}"

        requests.append({
            "createSlide": {
                "objectId": page_id,
                "insertionIndex": str(i // 2),
                "slideLayoutReference": {
                    "predefinedLayout": "BLANK"
                }
            }
        })

        # -------------------------------
        # 첫 슬라이드 상단 중앙 제목 표시
        # -------------------------------
        if i == 0 and show_title_text:

            title_box_id = f"title_box_{datetime.datetime.now().microsecond}"

            requests.append({
                "createShape": {
                    "objectId": title_box_id,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": page_id,
                        "size": {
                            "width": {
                                "magnitude": 400,
                                "unit": "PT"
                            },
                            "height": {
                                "magnitude": 30,
                                "unit": "PT"
                            }
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            # 슬라이드 상단 중앙
                            "translateX": 160,
                            "translateY": 5,
                            "unit": "PT"
                        }
                    }
                }
            })

            requests.append({
                "insertText": {
                    "objectId": title_box_id,
                    "text": file_name
                }
            })

            requests.append({
                "updateTextStyle": {
                    "objectId": title_box_id,
                    "style": {
                        "fontSize": {
                            "magnitude": 10,
                            "unit": "PT"
                        },
                        "bold": True
                    },
                    "textRange": {
                        "type": "ALL"
                    },
                    "fields": "fontSize,bold"
                }
            })

            requests.append({
                "updateParagraphStyle": {
                    "objectId": title_box_id,
                    "style": {
                        "alignment": "CENTER"
                    },
                    "textRange": {
                        "type": "ALL"
                    },
                    "fields": "alignment"
                }
            })

        # -------------------------------
        # 왼쪽 이미지
        # -------------------------------
        if cart_items[i].get("image_url"):

            requests.append({
                "createImage": {
                    "url": cart_items[i]["image_url"],
                    "elementProperties": {
                        "pageObjectId": page_id,
                        "size": {
                            "width": {
                                "magnitude": 360,
                                "unit": "PT"
                            },
                            "height": {
                                "magnitude": 405,
                                "unit": "PT"
                            }
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 0,
                            "translateY": 0,
                            "unit": "PT"
                        }
                    }
                }
            })

            # 왼쪽 이미지 번호
            left_label_id = f"left_label_{image_number}"

            requests.append({
                "createShape": {
                    "objectId": left_label_id,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": page_id,
                        "size": {
                            "width": {
                                "magnitude": 30,
                                "unit": "PT"
                            },
                            "height": {
                                "magnitude": 30,
                                "unit": "PT"
                            }
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 10,
                            "translateY": 25,
                            "unit": "PT"
                        }
                    }
                }
            })

            requests.append({
                "insertText": {
                    "objectId": left_label_id,
                    "text": str(image_number)
                }
            })

            requests.append({
                "updateTextStyle": {
                    "objectId": left_label_id,
                    "style": {
                        "fontSize": {
                            "magnitude": 18,
                            "unit": "PT"
                        },
                        "bold": True
                    },
                    "textRange": {
                        "type": "ALL"
                    },
                    "fields": "fontSize,bold"
                }
            })

            image_number += 1

        # -------------------------------
        # 오른쪽 이미지
        # -------------------------------
        if (
            i + 1 < len(cart_items) and
            cart_items[i + 1].get("image_url")
        ):

            requests.append({
                "createImage": {
                    "url": cart_items[i + 1]["image_url"],
                    "elementProperties": {
                        "pageObjectId": page_id,
                        "size": {
                            "width": {
                                "magnitude": 360,
                                "unit": "PT"
                            },
                            "height": {
                                "magnitude": 405,
                                "unit": "PT"
                            }
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 360,
                            "translateY": 0,
                            "unit": "PT"
                        }
                    }
                }
            })

            # 오른쪽 이미지 번호
            right_label_id = f"right_label_{image_number}"

            requests.append({
                "createShape": {
                    "objectId": right_label_id,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": page_id,
                        "size": {
                            "width": {
                                "magnitude": 30,
                                "unit": "PT"
                            },
                            "height": {
                                "magnitude": 30,
                                "unit": "PT"
                            }
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": 370,
                            "translateY": 25,
                            "unit": "PT"
                        }
                    }
                }
            })

            requests.append({
                "insertText": {
                    "objectId": right_label_id,
                    "text": str(image_number)
                }
            })

            requests.append({
                "updateTextStyle": {
                    "objectId": right_label_id,
                    "style": {
                        "fontSize": {
                            "magnitude": 18,
                            "unit": "PT"
                        },
                        "bold": True
                    },
                    "textRange": {
                        "type": "ALL"
                    },
                    "fields": "fontSize,bold"
                }
            })

            image_number += 1

    if requests:
        slides_service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": requests}
        ).execute()

    return f"https://docs.google.com/presentation/d/{presentation_id}"
