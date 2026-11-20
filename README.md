# Vanpere – School Management System (Django)
Setup:
    python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    python manage.py makemigrations accounts academics finance
    python manage.py migrate
    python manage.py seed          # demo data
    python manage.py runserver
Open http://127.0.0.1:8000  — demo logins (password demo1234): admin, registrar, bursar, lecturer, student1

Configuration: copy `.env.example` to `.env` and edit (or export the same
variables in the shell / systemd unit). Dev runs need no setup. The generic
names (`DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DATABASE_URL`, `EMAIL_*`) are
the ones hosts such as Render provide; the older `EDUSPHERE_*` names still
work as fallbacks. For production set at least:

    DEBUG=false
    SECRET_KEY=<long random string>
    ALLOWED_HOSTS=fees.school.example
    DATABASE_URL=postgresql://user:password@host:5432/dbname

School name and currency: `EDUSPHERE_INSTITUTION_NAME` /
`EDUSPHERE_CURRENCY` (see `.env.example` for all settings).

The main platform administrator is created from `PLATFORM_ADMIN_USERNAME`,
`PLATFORM_ADMIN_EMAIL` and `PLATFORM_ADMIN_PASSWORD` during deploy; see
[`DEPLOY.md`](DEPLOY.md) for the full Render walkthrough, environment
variables, object storage, custom domains and password rotation.
