import json
import os
import re
import time

from google import genai
from google.genai import types

from .models import Counter, Service, Ticket

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


def _client():
    key = os.getenv("GEMINI_API_KEY")
    return genai.Client(api_key=key) if key else None


def _generate(client, contents, config):
    """Gọi Gemini, tự thử lại khi quá tải (503/429), có thể đổi sang model dự phòng."""
    models = [MODEL]
    backup = os.getenv("GEMINI_FALLBACK_MODEL")
    if backup:
        models.append(backup)

    last = None
    for m in models:
        for attempt in range(2):
            try:
                return client.models.generate_content(model=m, contents=contents, config=config)
            except Exception as e:
                last = e
                text = str(e)
                if "503" in text or "429" in text or "UNAVAILABLE" in text:
                    time.sleep(1)  # đợi 1 giây rồi thử lại
                    continue
                break  # lỗi khác (sai model, sai key): sang model dự phòng
    raise last


def _fallback(text, services):
    """Không có AI thì chọn dịch vụ có nhiều từ trùng nhất."""
    words = set(re.findall(r"\w+", text.lower()))
    best, best_score = services[0], -1
    for s in services:
        vocab = set(re.findall(r"\w+", f"{s.name} {s.description}".lower()))
        score = len(words & vocab)
        if score > best_score:
            best, best_score = s, score
    return best, "Gợi ý theo từ khoá (chưa bật AI)"


def _suggest_counter(service):
    counters = service.counters.filter(is_open=True)
    for c in counters:  # ưu tiên quầy đang rảnh
        if not c.tickets.filter(status=Ticket.SERVING).exists():
            return c.name
    return counters.first().name if counters.exists() else None


def classify(text):
    services = list(Service.objects.filter(is_active=True))
    if not services:
        return None
    chosen, reason, source = None, "", "keyword"

    client = _client()
    if client and text.strip():
        try:
            options = "\n".join(f"{s.id}: {s.name} - {s.description}" for s in services)
            resp = _generate(
                client,
                f"Danh sách dịch vụ:\n{options}\n\nYêu cầu của khách: {text}",
                types.GenerateContentConfig(
                    system_instruction=(
                        "Bạn là trợ lý phân loại yêu cầu tại quầy dịch vụ. "
                        "Chọn đúng MỘT dịch vụ phù hợp nhất. "
                        'Chỉ trả về JSON: {"service_id": <số>, "reason": "<lý do ngắn, tiếng Việt>"}'
                    ),
                    response_mime_type="application/json",
                    max_output_tokens=300,
                ),
            )
            raw = re.search(r"\{.*\}", resp.text, re.S).group(0)
            data = json.loads(raw)
            chosen = next((s for s in services if s.id == int(data["service_id"])), None)
            reason, source = data.get("reason", ""), "ai"
        except Exception as e:
            print("Gemini lỗi:", e)
            chosen = None

    if chosen is None:
        chosen, reason = _fallback(text, services)
        source = "keyword"

    return {
        "service_id": chosen.id,
        "service_name": chosen.name,
        "reason": reason,
        "counter": _suggest_counter(chosen),
        "source": source,
    }


def summarize_bottleneck(data):
    client = _client()
    if client and data["total"] > 0:
        try:
            resp = _generate(
                client,
                json.dumps(data, ensure_ascii=False),
                types.GenerateContentConfig(
                    system_instruction=(
                        "Bạn là chuyên gia vận hành dịch vụ. Dựa trên số liệu hàng đợi (JSON), "
                        "tóm tắt bằng tiếng Việt: (1) khung giờ và dịch vụ nghẽn, (2) nguyên nhân có thể, "
                        "(3) 3 đề xuất cụ thể. Ngắn gọn, dưới 180 từ, chỉ dựa trên số liệu được cho."
                    ),
                    max_output_tokens=1000,
                ),
            )
            return resp.text
        except Exception as e:
            print("Gemini lỗi:", e)

    # Fallback không cần AI
    if not data["by_hour"]:
        return "Chưa đủ dữ liệu để phân tích."
    worst_h = max(data["by_hour"], key=lambda x: x["avg_wait"])
    worst_s = max(data["by_service"], key=lambda x: x["avg_wait"]) if data["by_service"] else None
    text = f"Giờ cao điểm: {worst_h['hour']}h (chờ trung bình {worst_h['avg_wait']} phút, {worst_h['tickets']} lượt)."
    if worst_s:
        text += f" Dịch vụ chờ lâu nhất: {worst_s['service']} ({worst_s['avg_wait']} phút)."
    return text + " Nên bố trí thêm quầy vào khung giờ này."