"""PostgreSQL persistence foundation (SQLAlchemy + Alembic).

`base.py` holds the declarative base and session/engine factories. `models/`
holds one module per record type. Nothing here implements business logic —
these are storage records for the domain dataclasses defined throughout
`ibi.*.interfaces`, kept intentionally thin (see DECISIONS.md on why domain
logic does not live on the ORM models).
"""
