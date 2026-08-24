from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import Quiz, Question, Choice


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = 'datetime-local'

class RegistrationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.help_text = ''
            if not field.widget.attrs.get('class'):
                field.widget.attrs['class'] = 'form-control'

class QuizForm(forms.ModelForm):
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

class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ['text']
        widgets = {'text': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Write your question clearly...'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

class ChoiceForm(forms.ModelForm):
    text = forms.CharField(required=False)

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
