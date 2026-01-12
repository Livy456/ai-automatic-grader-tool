Make sure you are in the backend directory

1. Run the following command in the terminal to install packages used for the backend:

>> pip install -r requirements.txt


2. Install alembic to have database migrations

>> python -m pip install alembic

3. Initialize alembic in the backend

>> alembic init alembic

4. Generate the migration (for wsl)

>> export DATABASE_URL="postgresql://dev:dev@localhost:5432/ai_grader"
>> alembic revision --autogenerate -m "create assignment_uploads"
>> alembic upgrade head

4. Run the following command in the terminal to run the backend:

>> python -m app.main

5. Access the backend information locally from this link:

>> INSERT LINK EVENTUALLY
