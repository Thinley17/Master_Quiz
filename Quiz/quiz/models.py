from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
from django.utils import timezone
import secrets


class Subject(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class Quiz(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name='quizzes')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quizzes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    max_attempts = models.PositiveIntegerField(default=1)
    access_key = models.CharField(max_length=32, unique=True, null=True, blank=True)

    class Meta:
        ordering = ['subject__name', '-created_at']
        constraints = [
            models.CheckConstraint(
                check=Q(max_attempts__gte=1), name='quiz_max_attempts_at_least_one'
            ),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.access_key:
            self.access_key = secrets.token_hex(4).upper()
        super().save(*args, **kwargs)

    def get_question_count(self):
        return self.questions.count()

    def is_available(self):
        return self.status_label == 'Active'

    @property
    def availability_label(self):
        return self.status_label

    @property
    def status_label(self):
        now = timezone.now()
        if not self.is_active:
            return 'Draft'
        if self.start_time and now < self.start_time:
            return 'Upcoming'
        if self.end_time and now > self.end_time:
            return 'Completed'
        return 'Active'

    @property
    def status_slug(self):
        return self.status_label.lower()

    @property
    def duration_minutes(self):
        if self.start_time and self.end_time:
            seconds = max(0, int((self.end_time - self.start_time).total_seconds()))
            return seconds // 60
        return None

class Question(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.text[:50]

class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=200)
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return self.text

class Result(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='results')
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='results')
    score = models.FloatField()
    correct_answers = models.IntegerField()
    total_questions = models.IntegerField()
    date_taken = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.quiz.title} - {self.score}%"
