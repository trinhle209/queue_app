import math
import statistics
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Avg, Count, Max
from django.utils import timezone

from .models import Service, Ticket


def expire_old_tickets():
    """Dọn vé tồn đọng từ ngày trước để không làm sai dự báo và không bị gọi nhầm."""
    today = timezone.localdate()
    now = timezone.now()
    Ticket.objects.filter(status=Ticket.WAITING, created_at__date__lt=today).update(
        status=Ticket.CANCELLED, finished_at=now)
    Ticket.objects.filter(status=Ticket.SERVING, created_at__date__lt=today).update(
        status=Ticket.SKIPPED, finished_at=now)


def create_ticket(service, name, note):
    """Cấp số thứ tự theo ngày; khoá dòng Service để 2 người bấm cùng lúc không trùng số."""
    with transaction.atomic():
        Service.objects.select_for_update().get(pk=service.pk)
        today = timezone.localdate()
        last = (
            Ticket.objects.filter(service=service, created_at__date=today)
            .aggregate(m=Max("number"))["m"] or 0
        )
        return Ticket.objects.create(service=service, number=last + 1, customer_name=name, note=note)


def avg_service_minutes(service):
    """Thời gian phục vụ điển hình = TRUNG VỊ của 50 lượt gần nhất trong 7 ngày."""
    since = timezone.now() - timedelta(days=7)
    qs = Ticket.objects.filter(
        service=service, status=Ticket.DONE,
        called_at__isnull=False, finished_at__gte=since,
    ).order_by("-finished_at")[:50]
    durations = [(t.finished_at - t.called_at).total_seconds() / 60 for t in qs]
    if len(durations) < 3:
        return service.default_minutes
    return max(0.5, statistics.median(durations))


def estimate_minutes(service, ahead):
    """Dự báo phút chờ = (số người trước + 0.5 × số quầy đang bận) × TG xử lý / số quầy mở."""
    open_counters = list(service.counters.filter(is_open=True))
    n = max(1, len(open_counters))
    busy = Ticket.objects.filter(status=Ticket.SERVING, counter__in=open_counters).count()
    avg = avg_service_minutes(service)
    return math.ceil((ahead + 0.5 * busy) * avg / n)


def ticket_info(ticket):
    ahead = Ticket.objects.filter(
        service=ticket.service, status=Ticket.WAITING, created_at__lt=ticket.created_at
    ).count()
    return {
        "position": ahead + 1,
        "minutes": estimate_minutes(ticket.service, ahead),
        "open_counters": ticket.service.counters.filter(is_open=True).count(),
    }


def report_data(days=30):
    since = timezone.now() - timedelta(days=days)
    all_qs = Ticket.objects.filter(created_at__gte=since)
    called = all_qs.filter(called_at__isnull=False).select_related("service")

    by_hour = defaultdict(list)
    by_service = defaultdict(lambda: {"wait": [], "serve": []})
    for t in called:
        wait = (t.called_at - t.created_at).total_seconds() / 60
        by_hour[timezone.localtime(t.created_at).hour].append(wait)
        by_service[t.service.name]["wait"].append(wait)
        if t.status == Ticket.DONE and t.finished_at:
            by_service[t.service.name]["serve"].append((t.finished_at - t.called_at).total_seconds() / 60)

    by_counter = defaultdict(list)
    done_qs = all_qs.filter(
        status=Ticket.DONE, counter__isnull=False,
        called_at__isnull=False, finished_at__isnull=False,
    ).select_related("counter")
    for t in done_qs:
        by_counter[t.counter.name].append((t.finished_at - t.called_at).total_seconds() / 60)

    rated = all_qs.filter(rating__isnull=False)
    rating_by_service = [
        {"service": r["service__name"], "avg": round(r["a"], 2), "count": r["n"]}
        for r in rated.values("service__name").annotate(a=Avg("rating"), n=Count("id")).order_by("-a")
    ]
    rating_dist = [{"star": s, "count": rated.filter(rating=s).count()} for s in (5, 4, 3, 2, 1)]
    recent_feedback = [
        {"code": t.code, "rating": t.rating, "text": t.feedback}
        for t in rated.exclude(feedback="").select_related("service").order_by("-created_at")[:8]
    ]

    def avg(x):
        return round(sum(x) / len(x), 1) if x else 0

    return {
        "days": days,
        "total": all_qs.count(),
        "done": all_qs.filter(status=Ticket.DONE).count(),
        "skipped": all_qs.filter(status=Ticket.SKIPPED).count(),
        "cancelled": all_qs.filter(status=Ticket.CANCELLED).count(),
        "avg_rating": round(all_qs.aggregate(a=Avg("rating"))["a"] or 0, 2),
        "by_hour": [{"hour": h, "tickets": len(w), "avg_wait": avg(w)} for h, w in sorted(by_hour.items())],
        "by_service": [
            {"service": n, "tickets": len(v["wait"]), "avg_wait": avg(v["wait"]), "avg_serve": avg(v["serve"])}
            for n, v in by_service.items()
        ],
        "by_counter": [
            {"counter": n, "served": len(v), "avg_serve": avg(v)} for n, v in sorted(by_counter.items())
        ],
        "rating_by_service": rating_by_service,
        "rating_dist": rating_dist,
        "recent_feedback": recent_feedback,
    }