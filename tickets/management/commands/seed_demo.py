import random
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tickets.models import Counter, Service, Ticket

# Trọng số số khách theo giờ: cao điểm 9-10h và 14-15h
HOUR_WEIGHTS = {8: 3, 9: 9, 10: 10, 11: 5, 13: 4, 14: 9, 15: 8, 16: 4}
FEEDBACKS = [
    "Nhân viên nhiệt tình", "Chờ hơi lâu", "Xử lý nhanh gọn", "Cần thêm quầy giờ cao điểm",
    "Hài lòng", "Giao diện dễ dùng", "Dự báo thời gian chờ khá chính xác", "",
]


class Command(BaseCommand):
    help = "Sinh dữ liệu vé mẫu (đánh dấu [demo]) để báo cáo và AI có số liệu."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14, help="Số ngày quá khứ (mặc định 14)")
        parser.add_argument("--clear", action="store_true", help="Xoá vé demo cũ trước khi sinh")

    def handle(self, *args, **opts):
        services = list(Service.objects.filter(is_active=True))
        counters = list(Counter.objects.prefetch_related("services"))
        if not services or not counters:
            raise CommandError("Hãy tạo Service và Counter trong /admin/ trước.")

        if opts["clear"]:
            n = Ticket.objects.filter(customer_name__startswith="[demo]").delete()[0]
            self.stdout.write(f"Đã xoá {n} vé demo cũ.")

        total = 0
        today = timezone.localdate()
        for d in range(opts["days"], 0, -1):
            day = today - timedelta(days=d)
            for s in services:
                eligible = [c for c in counters if s in c.services.all()] or counters
                hours = list(HOUR_WEIGHTS)
                weights = [HOUR_WEIGHTS[h] for h in hours]
                n_tickets = random.randint(18, 32)
                arrivals = sorted(
                    (random.choices(hours, weights)[0], random.randint(0, 59)) for _ in range(n_tickets)
                )
                for idx, (h, m) in enumerate(arrivals, start=1):
                    created = timezone.make_aware(datetime.combine(day, time(h, m)))
                    peak = HOUR_WEIGHTS[h] / 10
                    wait = random.uniform(1, 4) + peak * random.uniform(4, 14)
                    dur = max(1.0, s.default_minutes * random.uniform(0.6, 1.5))
                    if random.random() < 0.05:
                        dur *= 2.5  # ca phức tạp
                    r = random.random()
                    counter = random.choice(eligible)
                    kwargs = dict(service=s, number=idx, customer_name="[demo]",
                                  note=f"Hỏi về {s.name.lower()}")
                    if r < 0.06:  # khách huỷ trước khi được gọi
                        kwargs.update(status=Ticket.CANCELLED, finished_at=created + timedelta(minutes=wait / 2))
                    else:
                        called = created + timedelta(minutes=wait)
                        kwargs.update(counter=counter, called_at=called)
                        if r < 0.12:  # khách vắng
                            kwargs.update(status=Ticket.SKIPPED, finished_at=called + timedelta(minutes=1))
                        else:
                            kwargs.update(status=Ticket.DONE, finished_at=called + timedelta(minutes=dur))
                            if random.random() < 0.7:
                                base = 5 if wait < 8 else (4 if wait < 14 else 3)
                                kwargs["rating"] = max(1, min(5, base + random.choice([-1, 0, 0, 1])))
                                kwargs["feedback"] = random.choice(FEEDBACKS)
                    t = Ticket.objects.create(**kwargs)
                    Ticket.objects.filter(pk=t.pk).update(created_at=created)  # auto_now_add ghi đè nên phải update
                    total += 1
        self.stdout.write(self.style.SUCCESS(f"Đã tạo {total} vé demo cho {opts['days']} ngày."))