# Introduction-to-Programming-Group-project
Nova SBE - Fall 2026 -  Introduction to programming - Group Project

## Commands

```bash
pip install -r requirements.txt
streamlit run app.py
pytest
```

Database tests are skipped unless `TEST_DATABASE_URL` points to an empty test database (never the real one).

## Data storage

Locally the app stores its data in `data/data.json`. The deployed app uses a Supabase (PostgreSQL) database instead, because Streamlit Community Cloud erases local files on every reboot.

Set-up (once, by one team member):

1. Create a free project on supabase.com in a US region (Streamlit Cloud runs in the US). Keep the database password in a password manager.
2. In the project, open **Connect** and copy the **Session pooler** connection string, not the direct one: the direct connection only works over IPv6. Put the password into it.
3. On Streamlit Community Cloud, open the app's **Settings > Secrets** and add `DATABASE_URL = "postgresql://..."`.
4. For local testing against Supabase, put the same line into `.streamlit/secrets.toml`, which is git-ignored. Never commit it or paste it into chats.

The tables are created automatically on the first start. See `.env.example` for the variable names.
