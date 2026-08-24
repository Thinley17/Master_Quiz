from django.urls import path
from . import views

app_name = 'quiz'

urlpatterns = [
    path('', views.home, name='home'),
    path('register/', views.register, name='register'),

    # Quiz CRUD
    path('quizzes/', views.QuizListView.as_view(), name='quiz_list'),
    path('quizzes/create/', views.QuizCreateView.as_view(), name='quiz_create'),
    path('quizzes/<int:pk>/', views.quiz_detail, name='quiz_detail'),
    path('quizzes/<int:pk>/edit/', views.QuizUpdateView.as_view(), name='quiz_update'),
    path('quizzes/<int:pk>/delete/', views.QuizDeleteView.as_view(), name='quiz_delete'),
    path('quizzes/<int:quiz_id>/results/', views.teacher_quiz_results, name='teacher_quiz_results'),

    # Questions
    path('quizzes/<int:quiz_id>/questions/', views.question_list, name='question_list'),
    path('quizzes/<int:quiz_id>/questions/create/', views.question_create, name='question_create'),
    path('quizzes/<int:quiz_id>/questions/import/', views.bulk_question_import, name='bulk_question_import'),
    path('questions/import/template/', views.download_question_template, name='download_question_template'),
    path('quizzes/<int:quiz_id>/questions/import/template/', views.download_import_template, name='download_import_template'),
    path('questions/<int:question_id>/edit/', views.question_edit, name='question_edit'),
    path('questions/<int:question_id>/delete/', views.question_delete, name='question_delete'),

    # Taking quiz
    path('take/<int:quiz_id>/', views.take_quiz, name='take_quiz'),
    path('result/<int:quiz_id>/', views.result, name='result'),

    # Dashboard
    path('dashboard/', views.dashboard, name='dashboard'),
]
