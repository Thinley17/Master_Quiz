import logging
from urllib.parse import urlencode

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path, include, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import views as auth_views
from quiz.forms import UsernameOrEmailAuthenticationForm

logger = logging.getLogger(__name__)


def admin_login_redirect(request):
    """Send admin sign-ins through the shared QuizMaster login page."""
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect('quiz:dashboard')
    destination = request.GET.get('next') or '/admin/'
    if not url_has_allowed_host_and_scheme(
        destination,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        destination = '/admin/'
    if request.user.is_authenticated:
        return redirect(destination)
    return redirect(f"{reverse('login')}?{urlencode({'next': destination})}")


class SafePasswordResetView(auth_views.PasswordResetView):
    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except Exception:
            logger.exception('Unable to send a password reset email.')
            form.add_error(None, 'We could not send a reset email right now. Please try again later.')
            return self.form_invalid(form)

urlpatterns = [
    path('admin/login/', admin_login_redirect, name='admin_login_redirect'),
    path('admin/', admin.site.urls),
    path('', include('quiz.urls')),          # <-- this line is essential
    path('accounts/password_reset/', SafePasswordResetView.as_view(template_name='quiz/auth_password_reset_form.html'), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='quiz/auth_password_reset_done.html'), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='quiz/auth_password_reset_confirm.html'), name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='quiz/auth_password_reset_complete.html'), name='password_reset_complete'),
    path('accounts/login/', auth_views.LoginView.as_view(
        template_name='registration/login.html',
        authentication_form=UsernameOrEmailAuthenticationForm,
    ), name='login'),
    path('accounts/', include('django.contrib.auth.urls')),
]
