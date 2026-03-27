from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgebase',
            name='pdf_file',
            field=models.FileField(blank=True, null=True, upload_to='knowledge_pdfs/'),
        ),
        migrations.AlterField(
            model_name='knowledgebase',
            name='url',
            field=models.URLField(blank=True, default='', max_length=500),
        ),
    ]
