from io import BytesIO
import io
import json
import secrets
import hashlib
import logging
from datetime import timedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.cache import cache
from django.db import models, transaction, IntegrityError
from django.http import HttpResponse, HttpResponseForbidden
from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import Quiz, Question, Choice, Result, ActivityLog
from .forms import QuizForm, QuestionForm, ChoiceForm, QuizTakeForm, BulkQuestionImportForm, RegistrationForm, QuizAccessForm, EmailOTPForm
from django.contrib.auth import login

logger = logging.getLogger(__name__)


def _increment_limited_cache(key, timeout):
    try:
        return cache.incr(key)
    except ValueError:
        cache.add(key, 1, timeout=timeout)
        return 1


def _local(tag):
    """Namespace-agnostic tag name ('{ns}row' -> 'row')."""
    return tag.rsplit('}', 1)[-1]


def _xlsx_rows_stdlib(upload):
    """Read an .xlsx (a zip of XML files) using only the standard library."""
    import zipfile
    import xml.etree.ElementTree as ET

    def children(element, name):
        return [child for child in element if _local(child.tag) == name]

    def descendants(tree_root, name):
        return [node for node in tree_root.iter() if _local(node.tag) == name]

    try:
        with zipfile.ZipFile(io.BytesIO(upload.read())) as zf:
            if sum(info.file_size for info in zf.infolist()) > 25 * 1024 * 1024:
                raise ValueError('The uncompressed Excel workbook must be 25 MB or smaller.')
            names = set(zf.namelist())
            shared = []
            if 'xl/sharedStrings.xml' in names:
                sst = ET.fromstring(zf.read('xl/sharedStrings.xml'))
                for si in descendants(sst, 'si'):
                    shared.append(''.join(t.text or '' for t in descendants(si, 't')))
            sheet_name = 'xl/worksheets/sheet1.xml' if 'xl/worksheets/sheet1.xml' in names else None
            if sheet_name is None:
                candidates = sorted(name for name in names if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'))
                if not candidates:
                    raise ValueError('That workbook has no readable worksheets.')
                sheet_name = candidates[0]
            sheet = ET.fromstring(zf.read(sheet_name))
    except zipfile.BadZipFile:
        raise ValueError('RETRY_WITH_OPENPYXL')

    rows = []
    for row_el in descendants(sheet, 'row'):
        values = {}
        for cell in children(row_el, 'c'):
            letters = ''.join(ch for ch in cell.get('r', '') if ch.isalpha()).upper()
            col = 0
            for ch in letters:
                col = col * 26 + (ord(ch) - 64)
            col -= 1
            cell_type = cell.get('t')
            if cell_type == 'inlineStr':
                is_list = children(cell, 'is')
                value = ''.join(t.text or '' for t in descendants(is_list[0], 't')) if is_list else None
            else:
                v_list = children(cell, 'v')
                raw = v_list[0].text if v_list and v_list[0].text is not None else None
                if raw is None:
                    value = None
                elif cell_type == 's':
                    index = int(float(raw))
                    value = shared[index] if 0 <= index < len(shared) else None
                elif cell_type == 'b':
                    value = '1' if raw.strip() == '1' else '0'
                else:
                    value = raw
            if value not in (None, ''):
                values[col] = value
        rows.append(tuple(values.get(i) for i in range((max(values) + 1) if values else 0)))
    return rows


def xlsx_rows_from_upload(upload):
    """Rows from the first worksheet. Stdlib reader first; openpyxl only as fallback."""
    upload.seek(0)
    try:
        return _xlsx_rows_stdlib(upload)
    except ValueError as error:
        if str(error) != 'RETRY_WITH_OPENPYXL':
            raise
    except Exception:
        pass
    # Fallback for exotic workbooks the lightweight reader cannot handle.
    upload.seek(0)
    try:
        import openpyxl
    except ImportError:
        raise ValueError('This file could not be read as an Excel workbook. Download the template, paste your questions into it, and upload that.')
    try:
        workbook = openpyxl.load_workbook(upload, read_only=True, data_only=True)
        try:
            return [tuple(row) for row in workbook.active.iter_rows(values_only=True)]
        finally:
            workbook.close()
    except Exception:
        raise ValueError('This file could not be read as an Excel workbook. Download the template, paste your questions into it, and upload that.')


def build_template_xlsx():
    """Build the question-import template .xlsx without any third-party library."""
    import zipfile

    headers = ['order', 'question', 'choice1', 'choice2', 'choice3', 'choice4', 'correct_answer']
    sample = [1, 'What is the capital of France?', 'Paris', 'Rome', 'Madrid', 'Berlin', '1']

    def escape(text):
        return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    def column_letter(index):
        label = ''
        index += 1
        while index:
            index, remainder = divmod(index - 1, 26)
            label = chr(65 + remainder) + label
        return label

    def cell_xml(ref, val):
        if isinstance(val, (int, float)):
            return f'<c r="{ref}"><v>{val}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(val)}</t></is></c>'

    body = ''
    for row_index, row in enumerate([headers, sample], start=1):
        cells = ''.join(cell_xml(f'{column_letter(i)}{row_index}', v) for i, v in enumerate(row))
        body += f'<row r="{row_index}">{cells}</row>'
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{body}</sheetData></worksheet>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    workbook_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="Questions" sheetId="1" r:id="rId1"/></sheets></workbook>')
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                     '</Relationships>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', content_types)
        zf.writestr('_rels/.rels', root_rels)
        zf.writestr('xl/workbook.xml', workbook_xml)
        zf.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        zf.writestr('xl/worksheets/sheet1.xml', sheet)
    return buffer.getvalue()


TEMPLATE_HEADERS = ['order', 'question', 'choice1', 'choice2', 'choice3', 'choice4', 'correct_answer']


def parse_questions_from_excel(upload):
    """Validate an Excel file and return clean question rows for preview/import."""
    if not upload.name.lower().endswith('.xlsx'):
        raise ValueError('Please upload an Excel .xlsx file.')
    all_rows = xlsx_rows_from_upload(upload)
    rows_iter = iter(all_rows)
    header_row = next(rows_iter, None)
    if not header_row:
        raise ValueError('The workbook is empty.')
    headers = [str(value).strip().lower() if value is not None else '' for value in header_row]
    normalized_headers = {''.join(character for character in header if character.isalnum()): index for index, header in enumerate(headers)}

    def find_column(*aliases):
        for alias in aliases:
            if alias in normalized_headers:
                return normalized_headers[alias]
        # tolerate simple plurals like "Questions" / "Options"
        for alias in aliases:
            if not alias.endswith('s') and alias + 's' in normalized_headers:
                return normalized_headers[alias + 's']
            if alias.endswith('s') and alias[:-1] in normalized_headers:
                return normalized_headers[alias[:-1]]
        return None

    def cell(row, index):
        return row[index] if index < len(row) else None

    aliases = {
        'question': ('question', 'questiontext', 'prompt'),
        'choice1': ('choice1', 'option1', 'optiona', 'answera', 'a'),
        'choice2': ('choice2', 'option2', 'optionb', 'answerb', 'b'),
        'choice3': ('choice3', 'option3', 'optionc', 'answerc', 'c'),
        'choice4': ('choice4', 'option4', 'optiond', 'answerd', 'd'),
        'correct_answer': ('correctanswer', 'correctoption', 'answer', 'correct'),
    }
    positions = {}
    missing = []
    for field, field_aliases in aliases.items():
        position = find_column(*field_aliases)
        if position is None:
            missing.append(field)
        else:
            positions[field] = position
    if missing:
        found = ', '.join(header for header in headers if header)
        hint = f' Your file has these columns: {found}.' if found else ''
        raise ValueError('Missing columns: ' + ', '.join(missing) + '.' + hint)
    order_position = normalized_headers.get('order')
    cleaned_rows = []
    letters = {'a': 1, 'b': 2, 'c': 3, 'd': 4}
    for row_number, row in enumerate(rows_iter, start=2):
        if row_number > 501:
            raise ValueError('An import may contain at most 500 questions.')
        if not any(value is not None and str(value).strip() for value in row):
            continue
        question_text = cell(row, positions['question'])
        choices = [cell(row, positions[f'choice{i}']) for i in range(1, 5)]
        correct = ''.join(character for character in str(cell(row, positions['correct_answer']) or '') if character.isalnum()).casefold()
        if not question_text or not all(choices):
            raise ValueError(f'Row {row_number} needs a question and four choices.')
        if len(str(question_text).strip()) > 5000 or any(len(str(choice).strip()) > 200 for choice in choices):
            raise ValueError(f'Row {row_number} contains text that exceeds the allowed length.')
        matching_indices = [
            index
            for index, choice_text in enumerate(choices, start=1)
            if correct in (
                str(index),
                next((letter for letter, number in letters.items() if number == index), ''),
                str(choice_text).strip().casefold(),
            )
        ]
        if len(matching_indices) != 1:
            raise ValueError(f'Row {row_number} correct_answer must match exactly one choice number, letter, or text.')
        order = None
        order_value = cell(row, order_position) if order_position is not None else None
        if order_value not in (None, ''):
            try:
                order = int(order_value)
            except (TypeError, ValueError):
                raise ValueError(f'Row {row_number} order must be a whole number.')
            if not 0 <= order <= 2147483647:
                raise ValueError(f'Row {row_number} order is outside the allowed range.')
        cleaned_rows.append({
            'source_row': row_number,
            'order': order,
            'question': str(question_text).strip(),
            'choices': [str(choice_text).strip() for choice_text in choices],
            'correct_index': matching_indices[0],
        })
    if not cleaned_rows:
        raise ValueError('No question rows were found.')
    return cleaned_rows


def clean_question_payload(payload):
    if not isinstance(payload, str) or len(payload) > 8 * 1024 * 1024:
        raise ValueError('Import preview is too large. Upload a smaller Excel file.')
    try:
        rows = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        raise ValueError('Import preview expired. Upload the Excel file again.')
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError('Import preview must contain at most 500 questions.')
    cleaned_rows = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f'Preview row {index} is invalid.')
        raw_question = row.get('question', '')
        if not isinstance(raw_question, str):
            raise ValueError(f'Preview row {index} has invalid question text.')
        question = raw_question.strip()
        raw_choices = row.get('choices')
        if not isinstance(raw_choices, list) or any(not isinstance(choice, str) for choice in raw_choices):
            raise ValueError(f'Preview row {index} must contain exactly four choices.')
        choices = [choice.strip() for choice in raw_choices]
        try:
            if isinstance(row.get('correct_index'), bool):
                raise ValueError
            correct_index = int(row.get('correct_index'))
        except (TypeError, ValueError):
            raise ValueError(f'Preview row {index} has an invalid correct answer.')
        order = row.get('order')
        if order not in (None, ''):
            try:
                if isinstance(order, bool) or (isinstance(order, float) and not order.is_integer()):
                    raise ValueError
                order = int(order)
            except (TypeError, ValueError):
                raise ValueError(f'Preview row {index} has an invalid order.')
        else:
            order = None
        if not question or len(choices) != 4 or not all(choices):
            raise ValueError(f'Preview row {index} needs one question and four choices.')
        if len(question) > 5000 or any(len(choice) > 200 for choice in choices):
            raise ValueError(f'Preview row {index} contains text that exceeds the allowed length.')
        if correct_index not in (1, 2, 3, 4):
            raise ValueError(f'Preview row {index} has an invalid correct answer.')
        if order is not None and not 0 <= order <= 2147483647:
            raise ValueError(f'Preview row {index} has an invalid order.')
        cleaned_rows.append({
            'source_row': row.get('source_row', index + 1),
            'order': order,
            'question': question,
            'choices': choices,
            'correct_index': correct_index,
        })
    if not cleaned_rows:
        raise ValueError('No question rows were found.')
    return cleaned_rows


def create_questions_from_rows(quiz, rows):
    next_order = quiz.questions.aggregate(max_order=models.Max('order'))['max_order']
    next_order = (next_order + 1) if next_order is not None else 1
    created = 0
    for row in rows:
        order = row['order'] if row['order'] is not None else next_order
        question = Question.objects.create(quiz=quiz, text=row['question'], order=order)
        for index, choice_text in enumerate(row['choices'], start=1):
            Choice.objects.create(question=question, text=choice_text, is_correct=index == row['correct_index'])
        created += 1
        next_order += 1
    return created


def import_questions_from_excel(quiz, upload):
    return create_questions_from_rows(quiz, parse_questions_from_excel(upload))

def is_staff(user):
    return user.is_staff


def get_teacher_quiz_or_redirect(request, quiz_id):
    """Fetch a quiz owned by the current teacher; on miss, flash and point back."""
    quiz = Quiz.objects.filter(id=quiz_id, created_by=request.user).first()
    if quiz is None:
        messages.warning(request, 'That quiz no longer exists or belongs to another account.')
        return None
    return quiz

# Home view for the root URL.
def home(request):
    return render(request, 'quiz/home.html')


@login_required
def activity_log_view(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Only administrators can view the activity log.')

    search_query = request.GET.get('q', '').strip()[:100]
    selected_action = request.GET.get('action', '')
    logs = ActivityLog.objects.all()
    if search_query:
        logs = logs.filter(
            models.Q(username__icontains=search_query)
            | models.Q(path__icontains=search_query)
            | models.Q(ip_address__icontains=search_query)
        )
    valid_actions = {value for value, _label in ActivityLog.ACTION_CHOICES}
    if selected_action in valid_actions:
        logs = logs.filter(action=selected_action)
    else:
        selected_action = ''

    paginator = Paginator(logs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    pagination_params = request.GET.copy()
    pagination_params.pop('page', None)
    return render(request, 'quiz/activity_log.html', {
        'page_obj': page_obj,
        'page_range': paginator.get_elided_page_range(page_obj.number),
        'pagination_query': pagination_params.urlencode(),
        'search_query': search_query,
        'selected_action': selected_action,
        'action_choices': ActivityLog.ACTION_CHOICES,
        'result_count': logs.count(),
    })

def register(request):
    pending = request.session.get('pending_registration')

    if request.method == 'POST' and request.POST.get('start_over'):
        request.session.pop('pending_registration', None)
        return redirect('quiz:register')

    if pending and request.method == 'POST':
        form = EmailOTPForm(request.POST)
        expires_at = parse_datetime(pending.get('expires_at', ''))
        if not expires_at or timezone.now() >= expires_at:
            request.session.pop('pending_registration', None)
            messages.error(request, 'That code has expired. Please start registration again.')
            return redirect('quiz:register')
        if form.is_valid() and check_password(form.cleaned_data['code'], pending['code_hash']):
            if User.objects.filter(username=pending['username']).exists() or User.objects.filter(email__iexact=pending['email']).exists():
                request.session.pop('pending_registration', None)
                messages.error(request, 'That username or email is already in use. Please register again.')
                return redirect('quiz:register')
            try:
                user = User(username=pending['username'], email=pending['email'])
                user.password = pending['password_hash']
                user.save()
            except IntegrityError:
                request.session.pop('pending_registration', None)
                messages.error(request, 'That username is already in use. Please register again.')
                return redirect('quiz:register')
            request.session.pop('pending_registration', None)
            login(request, user)
            messages.success(request, 'Email verified. Your account is ready.')
            return redirect('quiz:dashboard')
        if form.is_valid():
            pending['attempts'] = pending.get('attempts', 0) + 1
            if pending['attempts'] >= 5:
                request.session.pop('pending_registration', None)
                messages.error(request, 'Too many incorrect codes. Start registration again to request a new code.')
                return redirect('quiz:register')
            request.session['pending_registration'] = pending
            form.add_error('code', f'That code is incorrect. {5 - pending["attempts"]} attempts remain.')
        return render(request, 'registration/register.html', {'otp_form': form, 'pending_email': pending['email']})

    if pending:
        return render(request, 'registration/register.html', {
            'otp_form': EmailOTPForm(), 'pending_email': pending['email'],
        })

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            code = f'{secrets.randbelow(1_000_000):06d}'
            email = form.cleaned_data['email']
            ip = request.META.get('REMOTE_ADDR', 'unknown')
            ip_hash = hashlib.sha256(ip.encode('utf-8')).hexdigest()
            email_hash = hashlib.sha256(email.casefold().encode('utf-8')).hexdigest()
            email_limit_key = f'registration-mail:email:{email_hash}'
            ip_limit_key = f'registration-mail:ip:{ip_hash}'
            if cache.get(email_limit_key, 0) >= 5 or cache.get(ip_limit_key, 0) >= 15:
                form.add_error(None, 'Too many verification requests. Please wait and try again later.')
                return render(request, 'registration/register.html', {'form': form}, status=429)
            try:
                send_mail(
                    'Your QuizMaster verification code',
                    f'Your QuizMaster verification code is {code}. It expires in 10 minutes.',
                    None,
                    [email],
                    fail_silently=False,
                )
            except Exception:
                logger.exception('Unable to send a registration verification email.')
                form.add_error(None, 'We could not send the verification email. Check the email settings and try again.')
            else:
                _increment_limited_cache(email_limit_key, 60 * 60)
                _increment_limited_cache(ip_limit_key, 60 * 60)
                request.session['pending_registration'] = {
                    'username': form.cleaned_data['username'],
                    'email': email,
                    'password_hash': make_password(form.cleaned_data['password1']),
                    'code_hash': make_password(code),
                    'expires_at': (timezone.now() + timedelta(minutes=10)).isoformat(),
                    'attempts': 0,
                }
                return render(request, 'registration/register.html', {
                    'otp_form': EmailOTPForm(), 'pending_email': email,
                })
    else:
        form = RegistrationForm()
    return render(request, 'registration/register.html', {'form': form})

# ---------- Quiz CRUD (staff only) ----------
class QuizListView(LoginRequiredMixin, ListView):
    model = Quiz
    template_name = 'quiz/quiz_list.html'
    context_object_name = 'quizzes'

    def get_queryset(self):
        queryset = Quiz.objects.select_related('subject', 'created_by').prefetch_related('questions').order_by('subject__name', '-created_at')
        # Teachers manage only their own quizzes; students see the catalogue grouped by subject.
        if self.request.user.is_staff:
            queryset = queryset.filter(created_by=self.request.user).annotate(
                attempt_count=models.Count('results', distinct=True),
                question_total=models.Count('questions', distinct=True),
            )
        else:
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not self.request.user.is_staff:
            result_counts = dict(
                Result.objects.filter(user=self.request.user, quiz__in=context['quizzes'])
                .values_list('quiz_id')
                .annotate(total=models.Count('id'))
            )
            latest_results = {}
            for result in (
                Result.objects.filter(user=self.request.user, quiz__in=context['quizzes'])
                .select_related('quiz')
                .order_by('quiz_id', '-date_taken')
            ):
                latest_results.setdefault(result.quiz_id, result)
            for quiz in context['quizzes']:
                quiz.user_attempt_count = result_counts.get(quiz.id, 0)
                quiz.user_latest_result = latest_results.get(quiz.id)
        grouped = {}
        for quiz in context['quizzes']:
            grouped.setdefault(quiz.subject, {}).setdefault(quiz.status_label, []).append(quiz)
        status_order = {'Active': 0, 'Upcoming': 1, 'Completed': 2, 'Draft': 3}
        for subject in grouped:
            grouped[subject] = {
                status: grouped[subject][status]
                for status in sorted(grouped[subject], key=lambda s: status_order.get(s, 9))
            }
        context['subject_groups'] = grouped.items()
        return context

class QuizCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Quiz
    form_class = QuizForm
    template_name = 'quiz/quiz_form.html'
    success_url = reverse_lazy('quiz:quiz_list')

    def test_func(self):
        return self.request.user.is_staff

    def get_choice_forms(self):
        return [ChoiceForm(self.request.POST or None, prefix=f'choice-{i}') for i in range(4)]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['question_form'] = kwargs.get('question_form', QuestionForm(self.request.POST or None))
        context['choice_forms'] = kwargs.get('choice_forms', self.get_choice_forms())
        context['bulk_form'] = kwargs.get('bulk_form', BulkQuestionImportForm(self.request.POST or None, self.request.FILES or None, prefix='import'))
        # Both question sources are optional at creation time, so the browser
        # must not hard-block submission on empty fields; validation happens
        # in form_valid where we know which sources the teacher chose.
        for optional_form in [context['question_form'], *context['choice_forms'], context['bulk_form']]:
            for field in optional_form.fields.values():
                field.required = False
        return context

    def form_valid(self, form):
        question_form = QuestionForm(self.request.POST)
        choice_forms = self.get_choice_forms()
        bulk_form = BulkQuestionImportForm(self.request.POST, self.request.FILES, prefix='import')
        for optional_form in (question_form, bulk_form):
            for field in optional_form.fields.values():
                field.required = False

        question_form.is_valid()
        choices_valid = all(choice_form.is_valid() for choice_form in choice_forms)
        filled_choices = [choice_form for choice_form in choice_forms if choices_valid and choice_form.cleaned_data.get('text')]
        correct_count = sum(bool(choice_form.cleaned_data.get('is_correct')) for choice_form in filled_choices)

        manual_text = (question_form.cleaned_data.get('text') or '').strip()
        manual_attempted = bool(manual_text) or bool(filled_choices)
        question_is_valid = choices_valid and bool(manual_text) and len(filled_choices) == 4 and correct_count == 1

        has_import_file = bool(self.request.FILES.get(bulk_form.add_prefix('file')))
        # Honor the selector; infer a reasonable source for older clients that
        # submit without it (for example, an Excel upload from a cached page).
        source = self.request.POST.get('question_source')
        if source not in ('excel', 'manual', 'both'):
            source = 'both' if has_import_file and manual_attempted else ('excel' if has_import_file else 'manual')
        wants_import = source in ('excel', 'both')
        wants_manual = source in ('manual', 'both')

        use_import = wants_import and has_import_file
        import_is_valid = bulk_form.is_valid() if use_import else False

        def reload_page():
            return self.render_to_response(self.get_context_data(form=form, question_form=question_form, choice_forms=choice_forms, bulk_form=bulk_form))

        if wants_import and not has_import_file and not (wants_manual and manual_attempted and question_is_valid):
            bulk_form.add_error('file', 'Choose your Excel .xlsx file. Tip: browsers clear the selected file after every attempt, so re-select it if you are trying again.')
            return reload_page()
        if use_import and not import_is_valid:
            return reload_page()
        if wants_manual and manual_attempted and not question_is_valid:
            # The teacher typed something manually but it is incomplete;
            # never silently drop their work alongside an import.
            if choices_valid:
                if not manual_text:
                    question_form.add_error(None, 'Write the question text, or switch the source above to Excel import only.')
                elif len(filled_choices) != 4:
                    question_form.add_error(None, 'Enter all four answer choices.')
                elif correct_count != 1:
                    question_form.add_error(None, 'Mark exactly one correct answer.')
            return reload_page()
        if wants_manual and not manual_attempted and not use_import:
            question_form.add_error(None, 'Write your question in Step 3, or switch the source above to Excel import.')
            return reload_page()
        if not wants_import and not wants_manual:
            question_form.add_error(None, 'Pick how you want to add questions using the selector above Steps 2 and 3.')
            return reload_page()

        with transaction.atomic():
            form.instance.created_by = self.request.user
            new_quiz = form.save()
            imported_count = 0
            if use_import:
                try:
                    imported_count = import_questions_from_excel(new_quiz, bulk_form.cleaned_data['file'])
                except (ValueError, ImportError, KeyError) as error:
                    # Roll everything back and re-render with self.object still None
                    # so the create page keeps its Steps 2-4 builder visible.
                    transaction.set_rollback(True)
                    bulk_form.add_error('file', str(error))
                    return reload_page()
            if wants_manual and question_is_valid:
                question = question_form.save(commit=False)
                question.quiz = new_quiz
                question.order = (new_quiz.questions.aggregate(max_order=models.Max('order'))['max_order'] or 0) + 1
                question.save()
                for choice_form in choice_forms:
                    choice = choice_form.save(commit=False)
                    choice.question = question
                    choice.save()
        if use_import and question_is_valid:
            message = f'Quiz "{new_quiz.title}" created with {imported_count} imported questions and one manual question.'
        elif use_import:
            message = f'Quiz "{new_quiz.title}" created with {imported_count} imported questions.'
        else:
            message = f'Quiz "{new_quiz.title}" created with your first question. Add more below if needed.'
        messages.success(self.request, message)
        return redirect('quiz:question_list', quiz_id=new_quiz.id)

class QuizUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Quiz
    form_class = QuizForm
    template_name = 'quiz/quiz_form.html'
    success_url = reverse_lazy('quiz:quiz_list')

    def test_func(self):
        return self.request.user.is_staff and self.get_object().created_by == self.request.user

class QuizDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Quiz
    template_name = 'quiz/quiz_confirm_delete.html'
    success_url = reverse_lazy('quiz:quiz_list')

    def test_func(self):
        return self.request.user.is_staff and self.get_object().created_by == self.request.user


@login_required
def quiz_detail(request, pk):
    queryset = Quiz.objects.select_related('subject', 'created_by').prefetch_related('questions')
    if request.user.is_staff:
        quiz = get_object_or_404(queryset, pk=pk, created_by=request.user)
    else:
        quiz = get_object_or_404(queryset, pk=pk, is_active=True)

    attempt_count = Result.objects.filter(user=request.user, quiz=quiz).count() if not request.user.is_staff else 0
    latest_result = (
        Result.objects.filter(user=request.user, quiz=quiz).order_by('-date_taken').first()
        if not request.user.is_staff else None
    )
    can_start = (
        not request.user.is_staff
        and quiz.is_available()
        and quiz.get_question_count() > 0
        and attempt_count < quiz.max_attempts
    )
    return render(request, 'quiz/quiz_detail.html', {
        'quiz': quiz,
        'attempt_count': attempt_count,
        'latest_result': latest_result,
        'can_start': can_start,
        'teacher_attempt_count': Result.objects.filter(quiz=quiz).count() if request.user.is_staff else None,
    })

# ---------- Question management (staff only) ----------
@login_required
@user_passes_test(is_staff)
def question_list(request, quiz_id):
    quiz = get_teacher_quiz_or_redirect(request, quiz_id)
    if quiz is None:
        return redirect('quiz:quiz_list')
    questions = quiz.questions.all()
    return render(request, 'quiz/question_list.html', {'quiz': quiz, 'questions': questions})

@login_required
@user_passes_test(is_staff)
def question_create(request, quiz_id):
    quiz = get_teacher_quiz_or_redirect(request, quiz_id)
    if quiz is None:
        return redirect('quiz:quiz_list')
    question_form = QuestionForm(request.POST or None)
    choice_forms = [ChoiceForm(request.POST or None, prefix=f'choice-{i}') for i in range(4)]

    choices_valid = all(form.is_valid() for form in choice_forms)
    filled_choices = [form for form in choice_forms if choices_valid and form.cleaned_data.get('text')]
    correct_count = sum(bool(form.cleaned_data.get('is_correct')) for form in filled_choices) if choices_valid else 0
    if request.method == 'POST' and question_form.is_valid() and choices_valid and len(filled_choices) == 4 and correct_count == 1:
        question = question_form.save(commit=False)
        question.quiz = quiz
        question.order = (quiz.questions.aggregate(max_order=models.Max('order'))['max_order'] or 0) + 1
        question.save()
        for form in choice_forms:
            if form.cleaned_data.get('text'):
                choice = form.save(commit=False)
                choice.question = question
                choice.save()
        messages.success(request, 'Question added successfully.')
        return redirect('quiz:question_list', quiz_id=quiz.id)

    if request.method == 'POST' and choices_valid:
        if len(filled_choices) != 4:
            question_form.add_error(None, 'Enter all four answer choices.')
        elif correct_count != 1:
            question_form.add_error(None, 'Mark exactly one correct answer.')

    return render(request, 'quiz/question_form.html', {
        'quiz': quiz,
        'question_form': question_form,
        'choice_forms': choice_forms,
    })

@login_required
@user_passes_test(is_staff)
def question_edit(request, question_id):
    question = Question.objects.filter(id=question_id, quiz__created_by=request.user).select_related('quiz').first()
    if question is None:
        messages.warning(request, 'That question no longer exists or belongs to another account.')
        return redirect('quiz:quiz_list')
    quiz = question.quiz
    question_form = QuestionForm(request.POST or None, instance=question)
    existing_choices = list(question.choices.all()[:4])
    choice_forms = [
        ChoiceForm(request.POST or None, instance=existing_choices[i] if i < len(existing_choices) else None, prefix=f'choice-{i}')
        for i in range(4)
    ]
    choices_valid = all(form.is_valid() for form in choice_forms)
    filled_choices = [form for form in choice_forms if form.cleaned_data.get('text')] if choices_valid else []
    correct_count = sum(bool(form.cleaned_data.get('is_correct')) for form in filled_choices) if choices_valid else 0
    if request.method == 'POST' and question_form.is_valid() and choices_valid and len(filled_choices) == 4 and correct_count == 1:
        question_form.save()
        for form in choice_forms:
            if form.cleaned_data.get('text'):
                choice = form.save(commit=False)
                choice.question = question
                choice.save()
            elif form.instance.pk:
                form.instance.delete()
        messages.success(request, 'Question updated.')
        return redirect('quiz:question_list', quiz_id=quiz.id)
    if request.method == 'POST' and choices_valid:
        if len(filled_choices) != 4:
            question_form.add_error(None, 'Enter all four answer choices.')
        elif correct_count != 1:
            question_form.add_error(None, 'Mark exactly one correct answer.')
    return render(request, 'quiz/question_form.html', {'quiz': quiz, 'question_form': question_form, 'choice_forms': choice_forms, 'editing': True})

@login_required
@user_passes_test(is_staff)
def question_delete(request, question_id):
    question = Question.objects.filter(id=question_id, quiz__created_by=request.user).select_related('quiz').first()
    if question is None:
        messages.warning(request, 'That question no longer exists or belongs to another account.')
        return redirect('quiz:quiz_list')
    quiz = question.quiz
    if request.method == 'POST':
        question.delete()
        messages.success(request, 'Question deleted.')
        return redirect('quiz:question_list', quiz_id=quiz.id)
    return render(request, 'quiz/question_confirm_delete.html', {'question': question, 'quiz': quiz})

# ---------- Taking a quiz (authenticated users) ----------
@login_required
def take_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    if request.user.is_staff:
        messages.warning(request, 'Teachers manage quizzes from the dashboard instead of attempting them.')
        return redirect('quiz:quiz_detail', pk=quiz.id)
    if not quiz.is_available():
        messages.warning(request, 'This quiz is not currently available.')
        return redirect('quiz:quiz_detail', pk=quiz.id)
    attempt_count = Result.objects.filter(user=request.user, quiz=quiz).count()
    if attempt_count >= quiz.max_attempts:
        messages.warning(request, 'You have reached the attempt limit for this quiz.')
        return redirect('quiz:quiz_list')
    access_session_key = f'quiz_access_{quiz.id}'
    access_form = QuizAccessForm(request.POST or None)
    if request.session.get(access_session_key) != quiz.access_key:
        if request.method == 'POST' and access_form.is_valid() and access_form.cleaned_data['access_key'].casefold() == quiz.access_key.casefold():
            request.session[access_session_key] = quiz.access_key
            return redirect('quiz:take_quiz', quiz_id=quiz.id)
        return render(request, 'quiz/take_quiz.html', {'quiz': quiz, 'access_form': access_form, 'requires_key': True})
    questions = quiz.questions.all().prefetch_related('choices')
    if not questions:
        messages.warning(request, 'This quiz has no questions.')
        return redirect('quiz:quiz_detail', pk=quiz.id)

    if request.method == 'POST':
        form = QuizTakeForm(questions, request.POST)
        if form.is_valid():
            correct = 0
            total = len(questions)
            for q in questions:
                chosen_id = form.cleaned_data.get(f'question_{q.id}')
                # Never trust a submitted choice id: it must belong to this question.
                if Choice.objects.filter(id=chosen_id, question=q, is_correct=True).exists():
                    correct += 1
            score_percent = (correct / total) * 100 if total > 0 else 0
            with transaction.atomic():
                locked_quiz = Quiz.objects.select_for_update().get(pk=quiz.pk)
                if not locked_quiz.is_available():
                    messages.warning(request, 'This quiz is no longer available.')
                    return redirect('quiz:quiz_list')
                if Result.objects.filter(user=request.user, quiz=locked_quiz).count() >= locked_quiz.max_attempts:
                    messages.warning(request, 'You have reached the attempt limit for this quiz.')
                    return redirect('quiz:quiz_list')
                Result.objects.create(user=request.user, quiz=locked_quiz, score=score_percent, correct_answers=correct, total_questions=total)
            messages.success(request, f'You scored {score_percent:.1f}%')
            return redirect('quiz:result', quiz_id=quiz.id)
    else:
        form = QuizTakeForm(questions)

    remaining_seconds = None
    if quiz.end_time:
        remaining_seconds = max(0, int((quiz.end_time - timezone.now()).total_seconds()))

    return render(request, 'quiz/take_quiz.html', {
        'quiz': quiz,
        'form': form,
        'remaining_seconds': remaining_seconds,
        'question_count': questions.count(),
        'access_form': access_form,
    })


@login_required
@user_passes_test(is_staff)
def bulk_question_import(request, quiz_id):
    quiz = get_teacher_quiz_or_redirect(request, quiz_id)
    if quiz is None:
        return redirect('quiz:quiz_list')
    form = BulkQuestionImportForm(request.POST or None, request.FILES or None)
    preview_rows = None
    preview_payload = ''

    if request.method == 'POST' and request.POST.get('confirm_import'):
        try:
            rows = clean_question_payload(request.POST.get('payload'))
            with transaction.atomic():
                created = create_questions_from_rows(quiz, rows)
            messages.success(request, f'{created} questions imported successfully.')
            return redirect('quiz:question_list', quiz_id=quiz.id)
        except ValueError as error:
            form.add_error('file', str(error))

    elif request.method == 'POST' and form.is_valid():
        try:
            preview_rows = parse_questions_from_excel(form.cleaned_data['file'])
            preview_payload = json.dumps(preview_rows)
        except (ValueError, ImportError, KeyError) as error:
            form.add_error('file', str(error))

    return render(request, 'quiz/bulk_import.html', {
        'quiz': quiz,
        'form': form,
        'preview_rows': preview_rows,
        'preview_payload': preview_payload,
    })


@login_required
@user_passes_test(is_staff)
def download_question_template(request):
    """Return a generic question-import template before a quiz exists."""
    response = HttpResponse(build_template_xlsx(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="quizmaster-question-template.xlsx"'
    return response


@login_required
@user_passes_test(is_staff)
def download_import_template(request, quiz_id):
    """Return a ready-to-fill workbook for the selected teacher's quiz."""
    quiz = get_object_or_404(Quiz, id=quiz_id, created_by=request.user)
    response = HttpResponse(build_template_xlsx(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{quiz.title}-questions-template.xlsx"'
    return response


@login_required
@user_passes_test(is_staff)
def teacher_quiz_results(request, quiz_id):
    quiz = get_teacher_quiz_or_redirect(request, quiz_id)
    if quiz is None:
        return redirect('quiz:quiz_list')
    results = Result.objects.filter(quiz=quiz).select_related('user').order_by('-date_taken')
    average_score = results.aggregate(models.Avg('score'))['score__avg'] or 0
    return render(request, 'quiz/teacher_results.html', {
        'quiz': quiz,
        'results': results,
        'average_score': average_score,
        'attempt_count': results.count(),
    })

@login_required
def result(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    result = Result.objects.filter(user=request.user, quiz=quiz).order_by('-date_taken').first()
    if not result:
        messages.warning(request, 'You have not taken this quiz yet.')
        return redirect('quiz:quiz_list')
    return render(request, 'quiz/result.html', {'quiz': quiz, 'result': result})

# ---------- Dashboard ----------
@login_required
def dashboard(request):
    if request.user.is_staff:
        quizzes = list(
            Quiz.objects.filter(created_by=request.user)
            .select_related('subject')
            .prefetch_related('questions')
            .annotate(attempt_count=models.Count('results', distinct=True))
            .order_by('-created_at')
        )
        status_counts = {'Draft': 0, 'Upcoming': 0, 'Active': 0, 'Completed': 0}
        for quiz in quizzes:
            status_counts[quiz.status_label] = status_counts.get(quiz.status_label, 0) + 1
        recent_attempts = (
            Result.objects.filter(quiz__created_by=request.user)
            .select_related('user', 'quiz', 'quiz__subject')
            .order_by('-date_taken')[:10]
        )
        return render(request, 'quiz/dashboard.html', {
            'teacher_dashboard': True,
            'quizzes': quizzes,
            'status_counts': status_counts,
            'total_quizzes': len(quizzes),
            'total_attempts': Result.objects.filter(quiz__created_by=request.user).count(),
            'recent_attempts': recent_attempts,
        })

    results = Result.objects.filter(user=request.user).select_related('quiz', 'quiz__subject').order_by('-date_taken')
    total = results.count()
    avg = results.aggregate(models.Avg('score'))['score__avg'] or 0
    catalogue = list(
        Quiz.objects.filter(is_active=True)
        .select_related('subject', 'created_by')
        .prefetch_related('questions')
        .annotate(
            question_total=models.Count('questions', distinct=True),
            user_attempt_total=models.Count('results', filter=models.Q(results__user=request.user), distinct=True),
        )
        .order_by('subject__name', 'start_time', 'title')
    )
    available_quizzes = []
    upcoming_quizzes = []
    ended_quizzes = []
    for quiz in catalogue:
        quiz.user_attempt_count = quiz.user_attempt_total
        status = quiz.status_label
        if status == 'Active' and quiz.question_total > 0:
            available_quizzes.append(quiz)
        elif status == 'Upcoming':
            upcoming_quizzes.append(quiz)
        elif status == 'Completed':
            ended_quizzes.append(quiz)
    latest_results = {}
    for result in results:
        latest_results.setdefault(result.quiz_id, result)
    for quiz in catalogue:
        quiz.user_latest_result = latest_results.get(quiz.id)
    return render(request, 'quiz/dashboard.html', {
        'results': results[:8],
        'total_quizzes_taken': total,
        'avg_score': avg,
        'quizzes': available_quizzes,
        'upcoming_quizzes': upcoming_quizzes,
        'ended_quizzes': ended_quizzes,
    })
