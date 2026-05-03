from django.db import migrations
import pgvector.django


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0004_purge_noise_records"),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql="",
        ),
        migrations.AddField(
            model_name="knowledgebase",
            name="embedding",
            field=pgvector.django.VectorField(
                blank=True,
                dimensions=768,
                null=True,
            ),
        ),
    ]
