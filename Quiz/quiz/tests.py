from datetime import timedelta
from io import BytesIO

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ActivityLog, Choice, Question, Quiz, Result, Subject


class QuizAccessTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user('teacher', password='secure-pass', is_staff=True)
        self.student = User.objects.create_user('student', password='secure-pass')
        self.other_student = User.objects.create_user('other', password='secure-pass')
        self.subject = Subject.objects.create(name='Science')
        self.quiz = Quiz.objects.create(title='Physics basics', subject=self.subject, created_by=self.teacher)
        self.question = Question.objects.create(quiz=self.quiz, text='What is H2O?', order=1)
        self.correct = Choice.objects.create(question=self.question, text='Water', is_correct=True)
        self.incorrect = Choice.objects.create(question=self.question, text='Oxygen')

    def test_home_page_is_public_on_root_url(self):
        response = self.client.get(reverse('quiz:home'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'quiz/home.html')

    def test_students_cannot_open_teacher_create_page(self):
        self.client.login(username='student', password='secure-pass')
        response = self.client.get(reverse('quiz:quiz_create'))
        self.assertEqual(response.status_code, 403)

    def test_teacher_creates_quiz_with_first_question_in_one_submission(self):
        self.client.login(username='teacher', password='secure-pass')
        response = self.client.post(reverse('quiz:quiz_create'), {
            'subject': self.subject.id,
            'title': 'New science quiz',
            'description': 'A quiz created with its first question.',
            'max_attempts': 2,
            'is_active': 'on',
            'text': 'Which gas do plants absorb?',
            'choice-0-text': 'Carbon dioxide',
            'choice-0-is_correct': 'on',
            'choice-1-text': 'Oxygen',
            'choice-2-text': 'Hydrogen',
            'choice-3-text': 'Nitrogen',
        })
        quiz = Quiz.objects.get(title='New science quiz')
        self.assertRedirects(response, reverse('quiz:question_list', args=[quiz.id]))
        self.assertEqual(quiz.questions.count(), 1)
        self.assertEqual(quiz.questions.first().choices.filter(is_correct=True).count(), 1)

    def test_excel_only_without_file_shows_validation_instead_of_crashing(self):
        self.client.login(username='teacher', password='secure-pass')
        response = self.client.post(reverse('quiz:quiz_create'), {
            'subject': self.subject.id,
            'title': 'Missing Excel file quiz',
            'max_attempts': 1,
            'is_active': 'on',
            'question_source': 'excel',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose your Excel .xlsx file')
        self.assertFalse(Quiz.objects.filter(title='Missing Excel file quiz').exists())

    def test_student_can_unlock_and_complete_manually_created_quiz(self):
        self.client.login(username='teacher', password='secure-pass')
        response = self.client.post(reverse('quiz:quiz_create'), {
            'subject': self.subject.id,
            'title': 'Student attendance flow',
            'description': 'Manual question without an Excel upload.',
            'max_attempts': 1,
            'is_active': 'on',
            'question_source': 'manual',
            'text': 'What is 2 + 2?',
            'choice-0-text': '4',
            'choice-0-is_correct': 'on',
            'choice-1-text': '3',
            'choice-2-text': '5',
            'choice-3-text': '6',
        })
        quiz = Quiz.objects.get(title='Student attendance flow')
        self.assertRedirects(response, reverse('quiz:question_list', args=[quiz.id]))
        question = quiz.questions.get()
        correct_choice = question.choices.get(is_correct=True)

        student_client = self.client_class()
        student_client.force_login(self.student)
        response = student_client.get(reverse('quiz:take_quiz', args=[quiz.id]))
        self.assertEqual(response.status_code, 200)
        response = student_client.post(reverse('quiz:take_quiz', args=[quiz.id]), {'access_key': quiz.access_key})
        self.assertRedirects(response, reverse('quiz:take_quiz', args=[quiz.id]))
        response = student_client.post(reverse('quiz:take_quiz', args=[quiz.id]), {
            f'question_{question.id}': correct_choice.id,
        })
        self.assertRedirects(response, reverse('quiz:result', args=[quiz.id]))
        self.assertEqual(Result.objects.get(user=self.student, quiz=quiz).score, 100)

    def test_teacher_can_add_another_complete_question(self):
        self.client.login(username='teacher', password='secure-pass')
        response = self.client.post(reverse('quiz:question_create', args=[self.quiz.id]), {
            'text': 'Which organ pumps blood?',
            'choice-0-text': 'Heart',
            'choice-0-is_correct': 'on',
            'choice-1-text': 'Lung',
            'choice-2-text': 'Liver',
            'choice-3-text': 'Kidney',
        })
        self.assertRedirects(response, reverse('quiz:question_list', args=[self.quiz.id]))
        self.assertEqual(self.quiz.questions.count(), 2)

    def test_teacher_can_create_quiz_and_import_excel_in_one_submission(self):
        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['order', 'question', 'choice1', 'choice2', 'choice3', 'choice4', 'correct_answer'])
        sheet.append([4, '2 + 2 equals?', '3', '4', '5', '6', '2'])
        buffer = BytesIO()
        workbook.save(buffer)
        upload = SimpleUploadedFile('questions.xlsx', buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.client.login(username='teacher', password='secure-pass')
        response = self.client.post(reverse('quiz:quiz_create'), {
            'subject': self.subject.id,
            'title': 'Imported maths quiz',
            'max_attempts': 1,
            'is_active': 'on',
            'import-file': upload,
        })
        quiz = Quiz.objects.get(title='Imported maths quiz')
        self.assertRedirects(response, reverse('quiz:question_list', args=[quiz.id]))
        self.assertEqual(quiz.questions.get().order, 4)
        self.assertEqual(quiz.questions.get().choices.get(is_correct=True).text, '4')

    def test_teacher_can_import_excel_and_add_manual_question_together(self):
        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['question', 'choice1', 'choice2', 'choice3', 'choice4', 'correct_answer'])
        sheet.append(['Imported question', 'A', 'B', 'C', 'D', '1'])
        buffer = BytesIO()
        workbook.save(buffer)
        upload = SimpleUploadedFile('combined.xlsx', buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.client.login(username='teacher', password='secure-pass')
        response = self.client.post(reverse('quiz:quiz_create'), {
            'subject': self.subject.id,
            'title': 'Combined quiz',
            'max_attempts': 1,
            'is_active': 'on',
            'import-file': upload,
            'text': 'Manual question',
            'choice-0-text': 'Correct',
            'choice-0-is_correct': 'on',
            'choice-1-text': 'Wrong one',
            'choice-2-text': 'Wrong two',
            'choice-3-text': 'Wrong three',
        })
        quiz = Quiz.objects.get(title='Combined quiz')
        self.assertRedirects(response, reverse('quiz:question_list', args=[quiz.id]))
        self.assertEqual(quiz.questions.count(), 2)
        self.assertEqual(quiz.questions.get(text='Manual question').order, 2)

    def test_teacher_can_import_excel_with_friendly_column_names(self):
        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['Question', 'Option A', 'Option B', 'Option C', 'Option D', 'Correct Answer'])
        sheet.append(['Friendly headers', 'Yes', 'No', 'Maybe', 'Unknown', 'A'])
        buffer = BytesIO()
        workbook.save(buffer)
        upload = SimpleUploadedFile('friendly.xlsx', buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.client.login(username='teacher', password='secure-pass')
        response = self.client.post(reverse('quiz:quiz_create'), {
            'subject': self.subject.id,
            'title': 'Friendly import quiz',
            'max_attempts': 1,
            'is_active': 'on',
            'import-file': upload,
        })
        quiz = Quiz.objects.get(title='Friendly import quiz')
        self.assertRedirects(response, reverse('quiz:question_list', args=[quiz.id]))
        self.assertEqual(quiz.questions.get().choices.get(is_correct=True).text, 'Yes')

    def test_scheduled_and_inactive_quizzes_are_unavailable(self):
        self.quiz.start_time = timezone.now() + timedelta(hours=1)
        self.quiz.save()
        self.assertFalse(self.quiz.is_available())

    def test_quiz_gets_access_key_and_student_must_enter_it(self):
        self.assertEqual(len(self.quiz.access_key), 8)
        self.client.login(username='student', password='secure-pass')
        response = self.client.get(reverse('quiz:take_quiz', args=[self.quiz.id]))
        self.assertContains(response, 'Enter your quiz key')
        response = self.client.post(reverse('quiz:take_quiz', args=[self.quiz.id]), {'access_key': 'WRONG'})
        self.assertContains(response, 'Enter your quiz key')
        response = self.client.post(reverse('quiz:take_quiz', args=[self.quiz.id]), {'access_key': self.quiz.access_key})
        self.assertRedirects(response, reverse('quiz:take_quiz', args=[self.quiz.id]))
        self.quiz.start_time = None
        self.quiz.is_active = False
        self.quiz.save()
        self.assertFalse(self.quiz.is_available())

    def test_attempt_limit_is_enforced(self):
        self.quiz.max_attempts = 1
        self.quiz.save()
        Result.objects.create(user=self.student, quiz=self.quiz, score=100, correct_answers=1, total_questions=1)
        self.client.login(username='student', password='secure-pass')
        response = self.client.get(reverse('quiz:take_quiz', args=[self.quiz.id]))
        self.assertRedirects(response, reverse('quiz:quiz_list'))

    def test_choice_from_another_question_cannot_score(self):
        other_question = Question.objects.create(quiz=self.quiz, text='Another question', order=2)
        foreign_correct_choice = Choice.objects.create(question=other_question, text='Foreign correct', is_correct=True)
        self.client.login(username='student', password='secure-pass')
        response = self.client.post(reverse('quiz:take_quiz', args=[self.quiz.id]), {
            f'question_{self.question.id}': foreign_correct_choice.id,
            f'question_{other_question.id}': foreign_correct_choice.id,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Result.objects.filter(user=self.student, quiz=self.quiz).exists())


class SecurityWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('security-admin', 'admin@example.com', 'Security-admin-pass-123!')
        self.student = User.objects.create_user('security-student', 'student@example.com', 'Security-student-pass-123!')
        self.teacher = User.objects.create_user('security-teacher', password='Security-teacher-pass-123!', is_staff=True)

    def test_password_login_accepts_email_and_rotates_session(self):
        session = self.client.session
        session['before_login'] = True
        session.save()
        old_session_key = session.session_key

        response = self.client.post('/accounts/login/', {
            'username': self.student.email,
            'password': 'Security-student-pass-123!',
        })

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(self.client.session.session_key, old_session_key)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.student.pk))

    def test_new_passwords_are_stored_as_bcrypt_hashes(self):
        self.assertTrue(self.student.password.startswith('bcrypt_sha256$'))
        self.assertNotIn('Security-student-pass-123!', self.student.password)

    def test_password_policy_accepts_eight_characters_with_a_symbol(self):
        validate_password('Abcdef1!', user=User(username='different-user'))
        validate_password('admin@123', user=User(username='admin'))
        with self.assertRaises(ValidationError):
            validate_password('Abcdefg1', user=User(username='different-user'))
        with self.assertRaises(ValidationError):
            validate_password('Abcde1!', user=User(username='different-user'))

    def test_login_locks_after_five_bad_passwords(self):
        for _ in range(5):
            response = self.client.post('/accounts/login/', {
                'username': self.student.username,
                'password': 'incorrect-password',
            })
            self.assertEqual(response.status_code, 200)

        response = self.client.post('/accounts/login/', {
            'username': self.student.username,
            'password': 'Security-student-pass-123!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, 'Too many failed sign-in attempts')

    def test_logout_clears_session_and_login_can_be_used_again(self):
        self.assertTrue(self.client.login(username=self.admin.username, password='Security-admin-pass-123!'))
        response = self.client.get('/accounts/logout/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/accounts/login/')
        self.assertNotIn('_auth_user_id', self.client.session)

        response = self.client.post('/accounts/login/', {
            'username': self.admin.username,
            'password': 'Security-admin-pass-123!',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.admin.pk))

    def test_admin_uses_the_shared_login_portal(self):
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 302)
        portal_redirect = self.client.get(response['Location'])
        self.assertEqual(portal_redirect.status_code, 302)
        self.assertTrue(portal_redirect['Location'].startswith('/accounts/login/'))
        login_page = self.client.get(portal_redirect['Location'])
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, 'Welcome back')

        response = self.client.post(portal_redirect['Location'], {
            'username': self.admin.username,
            'password': 'Security-admin-pass-123!',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/admin/')
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_logged_in_student_is_sent_back_to_app_not_admin_login_loop(self):
        self.client.force_login(self.student)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response['Location'])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/dashboard/')

    def test_activity_log_is_superuser_only_and_escapes_user_content(self):
        ActivityLog.objects.create(
            username='<script>alert(1)</script>', action='view', method='GET',
            path='/dashboard/', status_code=200, ip_address='127.0.0.1',
        )
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/audit-log/').status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.get('/audit-log/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;', html=False)

    def test_activity_log_search_treats_sql_payload_as_plain_text(self):
        self.client.force_login(self.admin)
        response = self.client.get('/audit-log/', {'q': "' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result_count'], 0)

    def test_password_reset_tokens_are_not_written_to_activity_log(self):
        self.client.get('/accounts/reset/private-user-id/private-reset-token/')
        latest = ActivityLog.objects.latest('created_at')
        self.assertNotIn('private-user-id', latest.path)
        self.assertNotIn('private-reset-token', latest.path)

    def test_staff_teachers_cannot_use_global_django_admin_data(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get('/admin/quiz/quiz/').status_code, 403)
        self.assertEqual(self.client.get('/admin/auth/user/').status_code, 403)
