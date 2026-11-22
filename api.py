"""HTTP client for the Group 6 FastAPI dashboard contract."""
import os
import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.getenv("API_TIMEOUT", "3"))

class APIError(RuntimeError):
    pass

def request_json(method, path, *, payload=None):
    try:
        response = requests.request(
            method, f"{API_BASE_URL}{path}", json=payload, timeout=TIMEOUT
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {"ok": True}
    except requests.RequestException as exc:
        detail = getattr(getattr(exc, "response", None), "text", "")
        raise APIError(f"{method} {path} failed: {exc} {detail}".strip()) from exc

def get_health(): return request_json("GET", "/api/health")
def get_state(): return request_json("GET", "/api/state")
def get_parcels(): return request_json("GET", "/api/parcels")
def get_metrics(): return request_json("GET", "/api/metrics")
def get_history(): return request_json("GET", "/api/history")
def get_graph(): return request_json("GET", "/api/graph")
def get_alerts(): return request_json("GET", "/api/alerts")

def send_control(command, bay_id=None):
    body = {"command": command}
    if bay_id:
        body["bay_id"] = bay_id
    return request_json("POST", "/api/control", payload=body)
