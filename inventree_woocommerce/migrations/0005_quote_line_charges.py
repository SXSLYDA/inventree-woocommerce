from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventree_woocommerce", "0004_quotes_and_shipments"),
    ]

    operations = [
        migrations.AlterField(
            model_name="quoteline",
            name="part_id",
            field=models.PositiveIntegerField(
                blank=True, null=True, help_text="InvenTree Part id (blank: labour, freight or another charge)"),
        ),
    ]
