from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def assign_general_subject(apps, schema_editor):
    Subject = apps.get_model('quiz', 'Subject')
    Quiz = apps.get_model('quiz', 'Quiz')
    subject, _ = Subject.objects.get_or_create(name='General')
    Quiz.objects.filter(subject__isnull=True).update(subject=subject)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('quiz', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Subject',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('description', models.TextField(blank=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.AddField(
            model_name='quiz',
            name='subject',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='quizzes', to='quiz.subject'),
        ),
        migrations.AddField(
            model_name='quiz',
            name='start_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='quiz',
            name='end_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='quiz',
            name='max_attempts',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RunPython(assign_general_subject, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='quiz',
            name='subject',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='quizzes', to='quiz.subject'),
        ),
    ]
