from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_errorrecord"),
    ]

    operations = [
        migrations.AddField(
            model_name="institution",
            name="show_platform_branding",
            field=models.BooleanField(
                default=True,
                help_text="Show the small 'Powered by' attribution on pages and documents.",
            ),
        ),
    ]
