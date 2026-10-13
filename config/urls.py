from django.contrib import admin
from django.urls import include, path
from tickets import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),

    path("", views.home, name="home"),
    path("api/suggest/", views.api_suggest),
    path("t/<int:pk>/", views.ticket_detail, name="ticket"),
    path("api/ticket/<int:pk>/", views.api_ticket),
    path("t/<int:pk>/cancel/", views.ticket_cancel, name="ticket_cancel"),
    path("t/<int:pk>/rate/", views.ticket_rate, name="ticket_rate"),

    path("board/", views.board, name="board"),
    path("api/board/", views.api_board),

    path("staff/", views.staff_dashboard, name="staff"),
    path("staff/queue/", views.staff_queue, name="staff_queue"),
    path("staff/action/<str:action>/", views.staff_action, name="staff_action"),

    path("manager/", views.manager_report, name="manager"),
    path("api/manager/ai/", views.api_manager_ai),
]