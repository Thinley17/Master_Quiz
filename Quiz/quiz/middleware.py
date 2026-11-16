import logging

from django.db import DatabaseError
from .models import ActivityLog

logger = logging.getLogger('quiz.security')


class ActivityAuditMiddleware:
    """Record each website request without storing form contents or query strings."""

    EXCLUDED_PREFIXES = ('/static/', '/media/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        username = user.get_username() if user and user.is_authenticated else ''
        response = self.get_response(request)

        request_path = request.path_info
        if not request_path.startswith(self.EXCLUDED_PREFIXES):
            # Log the resolved route pattern, not dynamic segments such as reset tokens.
            match = getattr(request, 'resolver_match', None)
            route = getattr(match, 'route', '') if match else ''
            path = f'/{route.lstrip("/")}' if route else ('/' if request_path == '/' else '<unmatched route>')
            method = request.method.upper()
            action = {
                'GET': 'view',
                'HEAD': 'view',
                'POST': 'submit',
                'PUT': 'update',
                'PATCH': 'update',
                'DELETE': 'delete',
            }.get(method, 'other')
            current_user = getattr(request, 'user', None)
            if current_user and current_user.is_authenticated:
                username = current_user.get_username()
            if method == 'POST' and request_path in ('/accounts/login/', '/admin/login/'):
                if response.status_code == 200:
                    action = 'login_fail'
                    username = request.POST.get('username', '').strip()[:150]
                elif current_user and current_user.is_authenticated:
                    action = 'login_ok'
            try:
                ActivityLog.objects.create(
                    username=username,
                    action=action,
                    method=method,
                    path=path[:512],
                    status_code=response.status_code,
                    ip_address=request.META.get('REMOTE_ADDR'),
                )
            except DatabaseError:
                # An audit-store outage must not turn otherwise healthy pages into 500s.
                logger.exception('Could not persist a website activity event.')

        return response
