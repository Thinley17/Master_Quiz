from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [('quiz', '0002_subject_quiz_schedule')]

    operations = [
        migrations.AddConstraint(
            model_name='quiz',
            constraint=models.CheckConstraint(
                check=Q(('max_attempts__gte', 1)),
                name='quiz_max_attempts_at_least_one',
            ),
        ),
    ]
