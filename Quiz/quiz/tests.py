from datetime import timedelta
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Choice, Question, Quiz, Result, Subject


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
