from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import ai, logic
from .models import Counter, Service, Ticket


# ---------- KHÁCH ----------
def home(request):
    if request.method == "POST":
        service = get_object_or_404(Service, pk=request.POST.get("service"), is_active=True)
        t = logic.create_ticket(
            service,
            request.POST.get("customer_name", "").strip()[:100],
            request.POST.get("note", "").strip()[:500],
        )
        return redirect("ticket", pk=t.pk)

    logic.expire_old_tickets()
    items = []
    for s in Service.objects.filter(is_active=True):
        s.waiting = s.tickets.filter(status=Ticket.WAITING).count()
        s.est = logic.estimate_minutes(s, s.waiting)
        items.append(s)
    return render(request, "home.html", {"services": items})


def api_suggest(request):
    result = ai.classify(request.GET.get("q", "")[:300])
    return JsonResponse(result or {}, json_dumps_params={"ensure_ascii": False})


def ticket_detail(request, pk):
    t = get_object_or_404(Ticket.objects.select_related("service", "counter"), pk=pk)
    return render(request, "ticket.html", {"t": t, "info": logic.ticket_info(t)})


def api_ticket(request, pk):
    t = get_object_or_404(Ticket.objects.select_related("service", "counter"), pk=pk)
    info = logic.ticket_info(t)
    return JsonResponse({
        "status": t.status,
        "label": t.get_status_display(),
        "position": info["position"],
        "wait": info["minutes"],
        "open_counters": info["open_counters"],
        "counter": t.counter.name if (t.counter and t.status == Ticket.SERVING) else None,
    })


@require_POST
def ticket_cancel(request, pk):
    t = get_object_or_404(Ticket, pk=pk)
    if t.status == Ticket.WAITING:
        t.status = Ticket.CANCELLED
        t.finished_at = timezone.now()
        t.save()
    return redirect("ticket", pk=pk)


@require_POST
def ticket_rate(request, pk):
    t = get_object_or_404(Ticket, pk=pk, status=Ticket.DONE)
    if t.rating is None:
        try:
            rating = int(request.POST.get("rating", 0))
        except ValueError:
            rating = 0
        if 1 <= rating <= 5:
            t.rating = rating
            t.feedback = request.POST.get("feedback", "")[:255]
            t.save()
    return redirect("ticket", pk=pk)


# ---------- MÀN HÌNH CHUNG ----------
def board(request):
    return render(request, "board.html")


def api_board(request):
    logic.expire_old_tickets()
    counters = []
    for c in Counter.objects.all():
        cur = c.tickets.filter(status=Ticket.SERVING).select_related("service").first()
        counters.append({"name": c.name, "open": c.is_open, "current": cur.code if cur else None})
    services = []
    for s in Service.objects.filter(is_active=True):
        waiting = list(s.tickets.filter(status=Ticket.WAITING).order_by("created_at"))
        services.append({
            "name": s.name,
            "waiting": len(waiting),
            "next": [t.code for t in waiting[:5]],
            "est": logic.estimate_minutes(s, len(waiting)),
        })
    return JsonResponse({"counters": counters, "services": services})


# ---------- NHÂN VIÊN ----------
@login_required
def staff_dashboard(request):
    counter = Counter.objects.filter(staff=request.user).first()
    if not counter:
        return render(request, "staff.html", {"counter": None})
    current = counter.tickets.filter(status=Ticket.SERVING).select_related("service").first()
    other_counters = []
    if current:
        other_counters = (
            Counter.objects.filter(is_open=True, services=current.service)
            .exclude(pk=counter.pk).distinct()
        )
    skipped = counter.tickets.filter(
        status=Ticket.SKIPPED, finished_at__date=timezone.localdate()
    ).select_related("service").order_by("-finished_at")[:5]
    return render(request, "staff.html", {
        "counter": counter,
        "current": current,
        "services": Service.objects.filter(is_active=True),
        "other_counters": other_counters,
        "skipped": skipped,
    })


@login_required
def staff_queue(request):
    counter = Counter.objects.filter(staff=request.user).first()
    if not counter:  # tài khoản chưa gán quầy (vd. admin): trả bảng rỗng, không báo 404
        return render(request, "staff_queue.html", {"rows": []})
    waiting = (
        Ticket.objects.filter(status=Ticket.WAITING, service__in=counter.services.all())
        .filter(Q(target_counter__isnull=True) | Q(target_counter=counter))
        .select_related("service", "target_counter").order_by("created_at")
    )
    now = timezone.now()
    rows = [{"t": t, "mins": int((now - t.created_at).total_seconds() // 60)} for t in waiting]
    return render(request, "staff_queue.html", {"rows": rows})


@login_required
@require_POST
def staff_action(request, action):
    counter = get_object_or_404(Counter, staff=request.user)
    current = counter.tickets.filter(status=Ticket.SERVING).first()

    if action == "toggle":
        if current:
            messages.error(request, "Hãy xử lý xong khách hiện tại trước khi đóng quầy.")
        else:
            counter.is_open = not counter.is_open
            counter.save()

    elif action == "next":
        if not counter.is_open:
            messages.error(request, "Hãy mở quầy trước.")
        elif current:
            messages.error(request, "Bạn đang phục vụ một khách.")
        else:
            logic.expire_old_tickets()
            with transaction.atomic():
                t = (
                    Ticket.objects.select_for_update(skip_locked=True, of=("self",))
                    .filter(status=Ticket.WAITING, service__in=counter.services.all())
                    .filter(Q(target_counter__isnull=True) | Q(target_counter=counter))
                    .order_by("created_at").first()
                )
                if t:
                    t.status, t.counter, t.called_at = Ticket.SERVING, counter, timezone.now()
                    t.target_counter = None
                    t.save()
                else:
                    messages.info(request, "Hiện không có khách chờ.")

    elif action == "done" and current:
        current.status, current.finished_at = Ticket.DONE, timezone.now()
        current.save()

    elif action == "skip" and current:  # khách vắng mặt
        current.status, current.finished_at = Ticket.SKIPPED, timezone.now()
        current.save()

    elif action == "recall":  # gọi lại khách đã bị bỏ qua (vắng mặt) hôm nay
        t = get_object_or_404(Ticket, pk=request.POST.get("ticket_id"),
                              status=Ticket.SKIPPED, counter=counter)
        t.status, t.counter, t.called_at, t.finished_at = Ticket.WAITING, None, None, None
        t.target_counter = counter  # chỉ quầy này được gọi lại, và vì vé cũ nên sẽ được gọi tiếp theo
        t.save()
        messages.info(request, f"Đã đưa {t.code} trở lại hàng chờ. Bấm Gọi số tiếp theo để gọi lại.")

    elif action == "transfer" and current:
        counter_id = request.POST.get("counter_id")
        service_id = request.POST.get("service_id")
        if counter_id:  # CHUYỂN QUẦY: giữ nguyên dịch vụ, chỉ quầy đích được gọi
            target = get_object_or_404(Counter, pk=counter_id)
            if target.pk == counter.pk:
                messages.error(request, "Đây đang là quầy của bạn.")
            elif not target.is_open:
                messages.error(request, f"{target.name} đang đóng.")
            elif not target.services.filter(pk=current.service_id).exists():
                messages.error(request, f"{target.name} không phục vụ dịch vụ này.")
            else:
                current.status, current.counter, current.called_at = Ticket.WAITING, None, None
                current.target_counter = target
                current.save()
                messages.info(request, f"Đã chuyển {current.code} sang {target.name}.")
        elif service_id:  # CHUYỂN DỊCH VỤ: khách chọn nhầm dịch vụ
            new_service = get_object_or_404(Service, pk=service_id)
            current.service = new_service
            current.status, current.counter, current.called_at = Ticket.WAITING, None, None
            current.target_counter = None
            current.save()
            messages.info(request, f"Đã chuyển khách sang dịch vụ {new_service.name}.")

    return redirect("staff")


# ---------- QUẢN LÝ ----------
def is_manager(u):
    return u.is_authenticated and u.is_superuser


def _days(request):
    try:
        d = int(request.GET.get("days", 30))
    except ValueError:
        d = 30
    return d if d in (7, 30, 90) else 30


@user_passes_test(is_manager)
def manager_report(request):
    return render(request, "manager.html", {"d": logic.report_data(_days(request))})


@user_passes_test(is_manager)
def api_manager_ai(request):
    text = ai.summarize_bottleneck(logic.report_data(_days(request)))
    return JsonResponse({"text": text}, json_dumps_params={"ensure_ascii": False})