from django.db import migrations, models
import secrets


def generate_keys(apps, schema_editor):
    Quiz = apps.get_model('quiz', 'Quiz')
    for quiz in Quiz.objects.filter(access_key__isnull=True):
        quiz.access_key = secrets.token_hex(4).upper()
        quiz.save(update_fields=['access_key'])


class Migration(migrations.Migration):
    dependencies = [('quiz', '0002_subject_quiz_schedule')]

    operations = [
        migrations.AddField(
            model_name='quiz',
            name='access_key',
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.RunPython(generate_keys, migrations.RunPython.noop),
    ]
