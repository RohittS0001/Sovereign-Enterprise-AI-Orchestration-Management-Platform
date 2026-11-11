"""Create the pgvector HNSW index for the configured embedding model (PostgreSQL only).

python -m scripts.vector_index            # show the DDL and the existing HNSW indexes
python -m scripts.vector_index --create   # create it (CONCURRENTLY: no write lock)

Then set RAG_VECTOR_INDEX_DIMENSIONS to the model's dimension (e.g. 768 for
nomic-embed-text) so search uses the index. See backend/app/domains/knowledge/vector_index.py.
"""

import argparse
import asyncio

from backend.app.core.config import Settings
from backend.app.db.session import Database
from backend.app.domains.knowledge.vector_index import (
    create_index,
    create_index_sql,
    existing_indexes,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--dimensions", type=int, help="default: RAG_VECTOR_INDEX_DIMENSIONS")
    args = parser.parse_args()
    settings = Settings()
    model = settings.embedding_model_name
    dimensions = args.dimensions or settings.rag_vector_index_dimensions
    if not model or not dimensions:
        raise SystemExit(
            "Set EMBEDDING_MODEL_NAME and RAG_VECTOR_INDEX_DIMENSIONS (or --dimensions)"
        )
    database = Database(settings.database_url)
    if database.backend != "postgresql":
        raise SystemExit("HNSW indexes need PostgreSQL with pgvector")
    try:
        async with database.engine.connect() as connection:
            print(create_index_sql(model, dimensions))
            if args.create:
                print("created", await create_index(connection, model, dimensions))
            print("HNSW indexes:", ", ".join(await existing_indexes(connection)) or "none")
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
