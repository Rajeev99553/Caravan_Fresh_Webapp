"""
Production WSGI entrypoint.

Gunicorn (or any WSGI server) imports `app` from here:

    gunicorn --preload -w 2 -b 0.0.0.0:$PORT wsgi:app

On first boot against an empty database it creates the schema and loads the
initial data according to SEED_MODE (demo / minimal / none). This is idempotent
— it never re-seeds a database that already has users. Use `--preload` so the
one-time seed runs once in the master process, not once per worker.
"""
from caravan import create_app
from caravan import data

app = create_app()


def _prepare_database():
    with app.app_context():
        data.init_schema()
        from caravan.seed import bootstrap
        from caravan import data as d
        if d.count("users") == 0:
            mode = app.config.get("SEED_MODE", "demo")
            print(f"[wsgi] empty database — bootstrapping (SEED_MODE={mode})")
            print("[wsgi] bootstrap:", bootstrap(mode=mode))
        else:
            print("[wsgi] database already initialised — skipping seed")


_prepare_database()


if __name__ == "__main__":
    # Fallback for `python wsgi.py` (dev only; use gunicorn in production).
    app.run(host="0.0.0.0", port=5000)
