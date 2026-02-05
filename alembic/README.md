# Database Migrations

This project uses [Alembic](https://alembic.sqlalchemy.org/) for database schema migrations.

## Quick Reference

### Apply all pending migrations
```bash
alembic upgrade head
```

### Check current version
```bash
alembic current
```

### View migration history
```bash
alembic history
```

### Create a new migration (auto-generate from model changes)
```bash
alembic revision --autogenerate -m "description of changes"
```

### Create an empty migration (manual)
```bash
alembic revision -m "description of changes"
```

### Rollback one migration
```bash
alembic downgrade -1
```

### Rollback to specific version
```bash
alembic downgrade <revision_id>
```

### Rollback all migrations
```bash
alembic downgrade base
```

## Migration Files

Located in `alembic/versions/`:
- `20260205_0001_add_inperson_result_confirmation.py` - Adds columns for in-person tournament result claim/confirmation system
- `20260205_0002_add_tournament_type_columns.py` - Adds is_online, venue, result_confirmation_minutes to tournaments

## Production Deployment

Before deploying to production:
1. Review the migration SQL: `alembic upgrade head --sql`
2. Backup the database
3. Run migrations: `alembic upgrade head`
4. Verify the changes

## Troubleshooting

### SQLite Limitations
SQLite has limited ALTER TABLE support. Alembic uses "batch mode" which recreates tables to work around this. This is handled automatically by setting `render_as_batch=True` in `env.py`.

### Reset migrations (development only)
If you need to start fresh in development:
```bash
# Remove alembic version table
sqlite3 chesskenya.db "DROP TABLE IF EXISTS alembic_version;"

# Then run migrations from scratch
alembic upgrade head
```
