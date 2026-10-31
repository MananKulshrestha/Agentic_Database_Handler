"""Start the API on Render using its assigned port and internal Postgres URL."""

import os

import uvicorn


def main():
    database_url = os.environ.get("APP_DATABASE_URL", "")
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            os.environ["APP_DATABASE_URL"] = database_url.replace(
                prefix, "postgresql+psycopg://", 1
            )
            break

    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        workers=1,
    )


if __name__ == "__main__":
    main()
