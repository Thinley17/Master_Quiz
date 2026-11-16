"""Django email backend that sends transactional messages through Brevo's API."""

import base64
import json
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend


BREVO_SEND_URL = 'https://api.brevo.com/v3/smtp/email'


class BrevoEmailBackendError(Exception):
    """Raised when Brevo cannot accept an outgoing email."""


class BrevoEmailBackend(BaseEmailBackend):
    """Send Django email messages over HTTPS without using SMTP."""

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.api_key = getattr(settings, 'BREVO_API_KEY', '').strip()
        self.sender_email = getattr(settings, 'BREVO_SENDER_EMAIL', '').strip()
        self.sender_name = getattr(settings, 'BREVO_SENDER_NAME', 'QuizMaster').strip() or 'QuizMaster'
        self.timeout = getattr(settings, 'BREVO_API_TIMEOUT', 20)

    @staticmethod
    def _recipient(address):
        name, email = parseaddr(address)
        recipient = {'email': email or address}
        if name:
            recipient['name'] = name
        return recipient

    @staticmethod
    def _message_content(message):
        for alternative in getattr(message, 'alternatives', ()):
            if hasattr(alternative, 'content'):
                content = alternative.content
                mimetype = alternative.mimetype
            else:
                content, mimetype = alternative[0], alternative[1]
            if mimetype == 'text/html':
                return 'htmlContent', content

        if getattr(message, 'content_subtype', 'plain') == 'html':
            return 'htmlContent', message.body
        return 'textContent', message.body

    @staticmethod
    def _attachments(message):
        attachments = []
        for attachment in getattr(message, 'attachments', ()):
            if isinstance(attachment, tuple):
                filename, content, _mimetype = attachment
                if isinstance(content, str):
                    content = content.encode('utf-8')
            else:
                filename = attachment.get_filename() or 'attachment'
                content = attachment.get_payload(decode=True)
                if content is None:
                    content = attachment.get_payload()
            if content is None:
                content = b''
            elif isinstance(content, str):
                content = content.encode('utf-8')

            attachments.append({
                'name': filename,
                'content': base64.b64encode(content).decode('ascii'),
            })
        return attachments

    def _payload(self, message):
        if not self.sender_email:
            raise ImproperlyConfigured('Set BREVO_SENDER_EMAIL to a verified Brevo sender address.')

        content_key, content = self._message_content(message)
        payload = {
            'sender': {'email': self.sender_email, 'name': self.sender_name},
            'to': [self._recipient(address) for address in message.to],
            'subject': message.subject,
            content_key: content,
        }

        for field in ('cc', 'bcc'):
            recipients = getattr(message, field, ())
            if recipients:
                payload[field] = [self._recipient(address) for address in recipients]

        reply_to = getattr(message, 'reply_to', ())
        if reply_to:
            payload['replyTo'] = self._recipient(reply_to[0])

        attachments = self._attachments(message)
        if attachments:
            payload['attachment'] = attachments
        return payload

    def _send(self, message):
        if not self.api_key:
            raise ImproperlyConfigured('Set BREVO_API_KEY before sending email.')

        request = Request(
            BREVO_SEND_URL,
            data=json.dumps(self._payload(message)).encode('utf-8'),
            headers={
                'accept': 'application/json',
                'api-key': self.api_key,
                'content-type': 'application/json',
            },
            method='POST',
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                return 200 <= response.status < 300
        except HTTPError as error:
            raise BrevoEmailBackendError(
                f'Brevo rejected the email request (HTTP {error.code}).'
            ) from None
        except (URLError, TimeoutError, OSError):
            raise BrevoEmailBackendError('Could not connect to the Brevo email API.') from None

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        sent = 0
        for message in email_messages:
            if not message.recipients():
                continue
            try:
                sent += bool(self._send(message))
            except Exception:
                if not self.fail_silently:
                    raise
        return sent
