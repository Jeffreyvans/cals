# EduSphere – School Management System (Django)
Setup:
    python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    python manage.py makemigrations accounts academics finance
    python manage.py migrate
    python manage.py seed          # demo data
    python manage.py runserver
Open http://127.0.0.1:8000  — demo logins (password demo1234): admin, registrar, bursar, lecturer, student1

Configuration: copy `.env.example` to `.env` and edit (or export the same
`EDUSPHERE_*` variables in the shell / systemd unit). Dev runs need no setup.
For production set at least:

    EDUSPHERE_DEBUG=0
    EDUSPHERE_SECRET_KEY=<long random string>
    EDUSPHERE_ALLOWED_HOSTS=fees.school.example

The app refuses to boot without the first three. School name and currency:
`EDUSPHERE_INSTITUTION_NAME` / `EDUSPHERE_CURRENCY` (see .env.example for all
settings, including HTTPS opt-in `EDUSPHERE_SSL=1`).
