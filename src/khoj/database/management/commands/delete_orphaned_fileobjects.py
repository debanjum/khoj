from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef

from khoj.database.models import Entry, FileObject


class Command(BaseCommand):
    help = "Deletes FileObjects that have no associated Entries"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually perform the deletion. Without this flag, only shows what would be deleted.",
        )

    def handle(self, *args, **options):
        # Find FileObjects with no related entries using subquery
        orphaned_files = FileObject.objects.annotate(
            has_entries=Exists(Entry.objects.filter(file_object=OuterRef("pk")))
        ).filter(has_entries=False)

        total_orphaned = orphaned_files.count()
        mode = "DELETE" if options["apply"] else "DRY RUN"
        self.stdout.write(f"[{mode}] Found {total_orphaned} orphaned FileObjects")

        if total_orphaned == 0:
            self.stdout.write("No orphaned FileObjects to process")
            return

        # Process in batches of 1000
        batch_size = 1000
        processed = 0

        while processed < total_orphaned:
            # Get batch of IDs to process
            batch_ids = list(orphaned_files.values_list("id", flat=True)[:batch_size])
            if not batch_ids:
                break

            if options["apply"]:
                # Delete by ID to avoid slice/limit issues
                count = FileObject.objects.filter(id__in=batch_ids).delete()[0]
                processed += count
                self.stdout.write(f"Deleted {processed}/{total_orphaned} orphaned FileObjects")
            else:
                processed += len(batch_ids)
                self.stdout.write(f"Would delete {processed}/{total_orphaned} orphaned FileObjects")

            # Re-query to get fresh state
            orphaned_files = FileObject.objects.annotate(
                has_entries=Exists(Entry.objects.filter(file_object=OuterRef("pk")))
            ).filter(has_entries=False)

        action = "Deleted" if options["apply"] else "Would delete"
        self.stdout.write(self.style.SUCCESS(f"{action} {processed} orphaned FileObjects"))
