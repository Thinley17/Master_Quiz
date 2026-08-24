# QuizMaster — Django Online Quiz App

A Django app for creating, taking, and tracking quizzes. Staff users can
create quizzes/questions; any logged-in user can take quizzes and see their
results on a personal dashboard.

## Roles and quiz workflow

- **Students** register through the public site. They can browse active quizzes by subject, attempt quizzes during their availability window, and review their own results.
- **Teachers** are Django staff accounts. Enable a teacher in `/admin/` (or create one with `createsuperuser`); they can create and manage only their own quizzes, questions, and Excel imports.
- Each quiz has a subject, active/inactive status, optional start and end date/time, and a maximum attempt count. Availability is enforced when a student submits, not just when the page is displayed.
- Teachers can import a batch from the question screen. Download the supplied Excel template there and use the columns `question`, `choice1`–`choice4`, and `correct_answer` (choice number or exact text).

## Quick start (localhost)

```bash
# 1. Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Apply database migrations (uses SQLite out of the box, zero setup)
python manage.py migrate

# 4. Create an admin account (this account can create quizzes)
python manage.py createsuperuser

# 5. Run the dev server
python manage.py runserver
```

Then open http://127.0.0.1:8000/ in your browser.

- Log in with your superuser account to create quizzes (via the "Create
  Quiz" button, or via /admin/).
- Any registered user (Register link on the home page) can take quizzes
  and view their dashboard.

## Switching to PostgreSQL (optional)

The app ships configured for SQLite so it runs immediately with no extra
setup. If you'd rather use PostgreSQL, install `psycopg2-binary` and
replace the `DATABASES` block in `online_quiz/settings.py` with:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'quiz_db',
        'USER': 'quiz_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

Then create the database/role in `psql` first, e.g.:
```sql
CREATE DATABASE quiz_db;
CREATE USER quiz_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE quiz_db TO quiz_user;
```

---

## Bugs found and fixed in this pass

The project as uploaded would not run. Here's everything that was wrong
and what was changed:

1. **`quiz` app was never registered.** `manage.py` uses
   `online_quiz.settings`, but that settings file's `INSTALLED_APPS` was
   missing `'quiz'` — meaning no migrations, admin, or app functionality
   could ever work. (There was a second, unused `online_quiz_project/`
   folder with a partial fix — Postgres config, `quiz` added — that was
   never wired up. It's been removed to avoid confusion; the fix was
   folded into the real settings file, `online_quiz/settings.py`.)

2. **Missing auth redirect settings.** Added `LOGIN_URL`,
   `LOGIN_REDIRECT_URL`, and `LOGOUT_REDIRECT_URL` — without these,
   Django redirects after login/logout to `/accounts/profile/`, which
   doesn't exist and 404s.

3. **Template filename typo:** `quiz_list.html` was saved as
   `quiz.list.html`, so the quiz list view couldn't find its template.
   Renamed.

4. **`take_quiz.html` had the wrong content** — it was an accidental
   copy-paste of the "delete question" confirmation page. Taking a quiz
   never actually worked. Rewritten to render the quiz's questions as
   radio-button choices and submit answers.

5. **`quiz_form.html` had the wrong content** — it was a copy of the quiz
   *list* page instead of a create/edit form. Rewritten to render the
   actual form fields.

6. **Broken URL reference:** templates used `{% url 'register' %}`, but
   the `register` view is namespaced under `quiz:` (`app_name = 'quiz'`
   in `quiz/urls.py`). This threw `NoReverseMatch` on every page that
   rendered the nav bar while logged out. Fixed to `{% url 'quiz:register' %}`
   in `base.html`, `home.html`, and `registration/login.html`.

7. **No migrations existed** for the `quiz` app (only the migrations
   package's `__init__.py` was present). Generated
   `0001_initial.py` and applied it.

8. **`admin.py`** had `inlines` set on a `TabularInline`, which Django
   doesn't support (nested inlines aren't a thing) — it was silently a
   no-op, and `Question`/`Choice` had no way to be managed from
   `/admin/`. Registered `Question` in the admin with `ChoiceInline` so
   quiz content can be fully managed from the Django admin as well as
   the app's own forms.

9. **Missing `STATICFILES_DIRS`**, added pointing at `quiz/static`.

## Verified working

The full flow was tested end-to-end against a running dev server:
registration/login/logout, staff-only quiz creation, adding a question
with 4 choices, taking the quiz as a regular user, scoring, the results
page, and the personal dashboard — all confirmed working, along with
permission checks (non-staff users get a 403 on quiz-creation routes).
