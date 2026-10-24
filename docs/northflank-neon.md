# Northflank Sandbox with Neon

Use a combined build/deployment service from `IbtisamAsghar/FireWatch_AI`, branch
`devtalha`. Choose Dockerfile, build context `/`, Dockerfile location
`/deploy/northflank.Dockerfile`, one instance, and the Sandbox plan. Select at
least 512 MB RAM within the included Sandbox resources; 256 MB leaves too little
headroom for the Python API and model. No GPU, database, or disk is needed.

The Dockerfile includes the checksum-pinned portable model and public evidence
files from [the Render/Neon preparation](render-neon.md). It verifies them during
the build, runs as a non-root user, and starts one Uvicorn worker on port 8000.
No worker, seed, database restore, migration, or training runs at startup.

Add these private runtime environment variables in Northflank:

- `DATABASE_URL`: the direct Neon URL with the `postgresql+psycopg://` scheme,
  retaining its SSL parameters.
- `ADMIN_API_TOKEN`: a private URL-safe token of at least 32 characters.

The private ignored `.env.northflank` on the preparation workstation contains the
existing deployment settings for import. Never commit, screenshot, or paste its
contents into public logs or chat. Other runtime defaults are in the Dockerfile.

Expose container port `8000` using HTTP and enable public access. Configure an
HTTP liveness probe at `/health/live` on that port. Use the generated public HTTPS
hostname to open `/docs` and `/health/ready`.

Verify the hosted API with:

```powershell
.\.venv\Scripts\python.exe scripts/verify_render.py --base-url https://YOUR-NORTHFLANK-HOSTNAME
```

These read-only checks verify model availability, six historical hotspots, and
four alert evidence records. The PC can be switched off after hosted verification
passes. Operator jobs still require a separately configured worker.
