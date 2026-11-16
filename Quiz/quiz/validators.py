import string

from django.core.exceptions import ValidationError


class SpecialCharacterPasswordValidator:
    """Require a punctuation symbol in addition to Django's length check."""

    def validate(self, password, user=None):
        if not any(character in string.punctuation for character in password):
            raise ValidationError(
                'This password must contain at least one special character (such as !, @, or #).',
                code='password_no_special_character',
            )

    def get_help_text(self):
        return 'Your password must contain at least one special character (such as !, @, or #).'
