import hashlib

from django import forms
from django.core.cache import cache
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from .models import Quiz, Question, Choice


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = 'datetime-local'

class RegistrationForm(UserCreationForm):
    email = forms.EmailField(
        label='Email address',
        widget=forms.EmailInput(attrs={'class': 'form-control', 'autocomplete': 'email'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['username', 'email', 'password1', 'password2'])
        self.fields['username'].widget.attrs.update({'autocomplete': 'username'})
        self.fields['password1'].widget.attrs.update({'autocomplete': 'new-password'})
        self.fields['password2'].widget.attrs.update({'autocomplete': 'new-password'})
        for field in self.fields.values():
            field.help_text = ''
            if not field.widget.attrs.get('class'):
                field.widget.attrs['class'] = 'form-control'

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account already uses this email address.')
        return email


class EmailOTPForm(forms.Form):
    code = forms.CharField(
        label='Verification code',
        min_length=6,
        max_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'inputmode': 'numeric', 'autocomplete': 'one-time-code',
            'placeholder': '6-digit code', 'pattern': '[0-9]{6}',
        }),
    )

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if not code.isdigit():
            raise forms.ValidationError('Enter the 6-digit code from your email.')
        return code


class RateLimitedLoginFormMixin:
    failure_limit = 5
    ip_failure_limit = 30
    throttle_window = 15 * 60

    def _throttle_key(self, kind, ip, identifier=''):
        fingerprint = hashlib.sha256(f'{ip}|{identifier.casefold()}'.encode('utf-8')).hexdigest()
        return f'login-throttle:{kind}:{fingerprint}'

    def _record_failure(self, key):
        try:
            return cache.incr(key)
        except ValueError:
            cache.add(key, 1, timeout=self.throttle_window)
            return 1

    def clean(self):
        identifier = self.cleaned_data.get('username', '').strip()
        if identifier and '@' in identifier:
            match = User.objects.filter(email__iexact=identifier).only('username').first()
            if match and not User.objects.filter(email__iexact=identifier).exclude(pk=match.pk).exists():
                self.cleaned_data['username'] = match.get_username()

        ip = self.request.META.get('REMOTE_ADDR', 'unknown') if self.request else 'unknown'
        username = self.cleaned_data.get('username', identifier).strip()
        account_key = self._throttle_key('account', ip, username)
        ip_key = self._throttle_key('ip', ip)
        if cache.get(f'{account_key}:locked') or cache.get(f'{ip_key}:locked'):
            raise forms.ValidationError('Too many failed sign-in attempts. Try again in 15 minutes.')

        try:
            cleaned = super().clean()
        except forms.ValidationError:
            if self.cleaned_data.get('username') and self.cleaned_data.get('password'):
                account_failures = self._record_failure(account_key)
                ip_failures = self._record_failure(ip_key)
                if account_failures >= self.failure_limit:
                    cache.set(f'{account_key}:locked', True, timeout=self.throttle_window)
                if ip_failures >= self.ip_failure_limit:
                    cache.set(f'{ip_key}:locked', True, timeout=self.throttle_window)
            raise
        cache.delete(account_key)
        cache.delete(ip_key)
        return cleaned


class UsernameOrEmailAuthenticationForm(RateLimitedLoginFormMixin, AuthenticationForm):
    username = forms.CharField(
        label='Username or email',
        widget=forms.TextInput(attrs={'autofocus': True, 'autocomplete': 'username'}),
    )

class QuizForm(forms.ModelForm):
    description = forms.CharField(
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    class Meta:
        model = Quiz
        fields = ['subject', 'title', 'description', 'start_time', 'end_time', 'max_attempts', 'access_key', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'start_time': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
            'end_time': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
        }

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        is_active = cleaned_data.get('is_active')
        if start_time and end_time and end_time <= start_time:
            raise forms.ValidationError('End time must be after the start time.')
        if is_active and self.instance.pk and not self.instance.questions.exists():
            self.add_error('is_active', 'Add at least one question before publishing this quiz.')
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Subject
        default_subjects = ['Biology', 'Chemistry', 'Computer Science', 'English', 'Geography', 'History', 'Mathematics', 'Physics']
        for subject_name in default_subjects:
            Subject.objects.get_or_create(name=subject_name)
        self.fields['subject'].empty_label = 'Choose subject'
        self.fields['title'].widget.attrs['placeholder'] = 'Example: Biology chapter 3 quiz'
        self.fields['max_attempts'].widget.attrs['min'] = 1
        self.fields['access_key'].required = False
        self.fields['access_key'].label = 'Quiz access key'
        self.fields['access_key'].widget.attrs['placeholder'] = 'Leave blank to generate automatically'
        self.fields['is_active'].label = 'Publish quiz'
        if not self.instance.pk:
            # New quizzes start published so they appear for students right away;
            # untick the switch to keep a draft private.
            self.fields['is_active'].initial = True
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'


class BulkQuestionImportForm(forms.Form):
    file = forms.FileField(
        help_text='Excel .xlsx only. Columns: order, question, choice1, choice2, choice3, choice4, correct_answer.',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.xlsx'}),
    )

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload is None:
            return None
        if upload.size > 5 * 1024 * 1024:
            raise forms.ValidationError('The Excel file must be 5 MB or smaller.')
        if not upload.name.lower().endswith('.xlsx'):
            raise forms.ValidationError('Please upload an Excel .xlsx file.')
        return upload

class QuestionForm(forms.ModelForm):
    text = forms.CharField(
        max_length=5000,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Write your question clearly...'}),
    )

    class Meta:
        model = Question
        fields = ['text']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

class ChoiceForm(forms.ModelForm):
    text = forms.CharField(required=False, max_length=200)

    class Meta:
        model = Choice
        fields = ['text', 'is_correct']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['text'].widget.attrs['class'] = 'form-control'
        self.fields['is_correct'].widget.attrs['class'] = 'form-check-input'

class QuizTakeForm(forms.Form):
    def __init__(self, questions, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for q in questions:
            choices = [(c.id, c.text) for c in q.choices.all()]
            self.fields[f'question_{q.id}'] = forms.ChoiceField(
                choices=choices,
                widget=forms.RadioSelect,
                label=q.text,
                required=False
            )


class QuizAccessForm(forms.Form):
    access_key = forms.CharField(
        label='Enter quiz key',
        max_length=32,
        strip=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Example: A1B2C3D4', 'autocomplete': 'off'}),
    )
