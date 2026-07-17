# Finishing the pgvector install (one-time, needs Administrator)

pgvector has already been **compiled** against this machine's PostgreSQL 16
(via MSVC Build Tools). The only remaining step — copying the built files
into `C:\Program Files\PostgreSQL\16\` — needs Administrator rights that
the automated shell doesn't have. Everything runs correctly without this;
the system falls back to an in-process NumPy vector index
(`VECTOR_BACKEND=numpy` in `.env`). This step upgrades it to native
Postgres ANN search (`VECTOR_BACKEND=pgvector`).

## 1. Copy the built extension files (run in an *elevated* PowerShell)

```powershell
$src = "$env:LOCALAPPDATA\Temp\claude\c--Users-yeshw-Downloads-AI-Models-Task-2\6f00224f-8b19-49e6-92ff-b663e3028abd\scratchpad\pgvector"
Copy-Item "$src\vector.dll" "C:\Program Files\PostgreSQL\16\lib\" -Force
Copy-Item "$src\vector.control" "C:\Program Files\PostgreSQL\16\share\extension\" -Force
Copy-Item "$src\sql\vector--*.sql" "C:\Program Files\PostgreSQL\16\share\extension\" -Force
Copy-Item "$src\sql\vector.sql" "C:\Program Files\PostgreSQL\16\share\extension\" -Force
```

> The scratchpad path is session-specific — if it's gone, re-clone and rebuild:
> ```powershell
> git clone --depth 1 https://github.com/pgvector/pgvector.git C:\pgvector-build
> cmd /c 'call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" && set "PGROOT=C:\Program Files\PostgreSQL\16" && cd /d C:\pgvector-build && nmake /NOLOGO /F Makefile.win'
> ```
> then point `$src` at `C:\pgvector-build` instead.

## 2. Create the extension (no admin needed — normal psql)

```powershell
$env:PGPASSWORD="postgres"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h 127.0.0.1 -d collabv_search -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

## 3. Run the migration script (from the repo root)

```powershell
python -m search_platform.migrate_to_pgvector
```

This converts the `embedding` column from `float[]` to `vector(384)`,
backfills it from existing rows, adds an HNSW cosine index, and flips
`VECTOR_BACKEND=pgvector` in `.env`. Restart the server afterward.
