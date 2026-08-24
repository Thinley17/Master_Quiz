from django.db import migrations


def add_default_subjects(apps, schema_editor):
    Subject = apps.get_model('quiz', 'Subject')
    for name in ('Biology', 'Chemistry', 'Computer Science', 'English', 'Geography', 'History', 'Mathematics', 'Physics'):
        Subject.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [('quiz', '0004_alter_quiz_options')]
    operations = [migrations.RunPython(add_default_subjects, migrations.RunPython.noop)]
