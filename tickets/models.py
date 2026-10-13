from django.conf import settings
from django.db import models


class Service(models.Model):
    name = models.CharField("Tên dịch vụ", max_length=100)
    prefix = models.CharField("Ký hiệu số (A, B...)", max_length=3, unique=True)
    description = models.CharField("Mô tả (AI dùng để phân loại)", max_length=255, blank=True)
    default_minutes = models.PositiveIntegerField("Thời gian xử lý mặc định (phút)", default=5)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.prefix} - {self.name}"


class Counter(models.Model):
    name = models.CharField("Tên quầy", max_length=50)
    staff = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name="Nhân viên phụ trách",
    )
    services = models.ManyToManyField(Service, related_name="counters")
    is_open = models.BooleanField("Đang mở", default=False)

    def __str__(self):
        return self.name


class Ticket(models.Model):
    WAITING, SERVING, DONE, SKIPPED, CANCELLED = "waiting", "serving", "done", "skipped", "cancelled"
    STATUS_CHOICES = [
        (WAITING, "Đang chờ"),
        (SERVING, "Đang phục vụ"),
        (DONE, "Đã xong"),
        (SKIPPED, "Bỏ qua (vắng mặt)"),
        (CANCELLED, "Đã huỷ"),
    ]

    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="tickets")
    number = models.PositiveIntegerField()
    customer_name = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=WAITING)
    counter = models.ForeignKey(Counter, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    # Chuyển quầy: nếu có giá trị thì CHỈ quầy này được gọi vé
    target_counter = models.ForeignKey(
        Counter, null=True, blank=True, on_delete=models.SET_NULL, related_name="redirected_tickets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    called_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"])]

    @property
    def code(self):
        return f"{self.service.prefix}{self.number:03d}"

    def __str__(self):
        return self.code