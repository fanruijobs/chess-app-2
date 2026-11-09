"""Calibrate a book's piece font: cluster one page's glyphs, label clusters."""

import logging

from django.core.management.base import BaseCommand, CommandError

from books.cv import build_profile_data, cluster_board, detect_diagrams, load_image
from books.models import Book, CalibrationProfile

logger = logging.getLogger("books.cv")

PIECE_CODES = {"wK", "wQ", "wR", "wB", "wN", "wP", "bK", "bQ", "bR", "bB", "bN", "bP"}


class Command(BaseCommand):
    help = (
        "Calibrate a book's piece font from one page photo. Prints the glyph "
        "clusters, then you label them with --label '3=wN' (repeatable). "
        "Labels are saved into the book's CalibrationProfile."
    )

    def add_arguments(self, parser):
        parser.add_argument("book_slug")
        parser.add_argument("image_path", help="page photo (any OpenCV-readable format)")
        parser.add_argument(
            "--label", action="append", default=[],
            help="cluster label, e.g. '3=wN' (repeatable); codes: wK..wP, bK..bP",
        )
        parser.add_argument("--diagram-index", type=int, default=0,
                            help="which diagram on the page to cluster (default first)")
        parser.add_argument("--cluster-cut", type=float, default=1.2,
                            help="agglomerative merge cutoff (z-scored distance)")
        parser.add_argument("--empty-threshold", type=float, default=0.04,
                            help="ink ratio below which a square is empty")
        parser.add_argument("--solidity-cut", type=float, default=0.4,
                            help="white/black solidity split (informational)")
        parser.add_argument(
            "--baseline", action="store_true",
            help="pieces sit ON the bottom grid line (e.g. Polgar diagrams); "
            "extends square patches below the line so pieces aren't clipped",
        )
        parser.add_argument("--dry-run", action="store_true",
                            help="print clusters, save nothing")
        parser.add_argument("--verbose", action="store_true", help="debug logging")

    def handle(self, *args, **options):
        logging.getLogger("books.cv").setLevel(
            logging.DEBUG if options["verbose"] else logging.INFO
        )
        book = Book.objects.filter(slug=options["book_slug"]).first()
        if book is None:
            raise CommandError(f"No book with slug {options['book_slug']!r}.")

        labels = {}
        for label_arg in options["label"]:
            cluster_id, _, code = label_arg.partition("=")
            cluster_id, code = cluster_id.strip(), code.strip()
            if code not in PIECE_CODES:
                raise CommandError(f"invalid piece code {code!r} in --label {label_arg!r}")
            labels[cluster_id] = code

        img = load_image(options["image_path"])
        diagrams = detect_diagrams(img)
        if not diagrams:
            raise CommandError("no diagrams detected on this image")
        index = min(options["diagram_index"], len(diagrams) - 1)
        info = cluster_board(
            diagrams[index].board_img,
            cut=options["cluster_cut"],
            empty_max_ink=options["empty_threshold"],
            baseline=options["baseline"],
        )

        self.stdout.write(f"Clusters ({len(info['clusters'])}):")
        for cluster_id, stats in sorted(info["clusters"].items()):
            current = labels.get(cluster_id, "-")
            self.stdout.write(
                f"  cluster {cluster_id}: n={stats['count']} "
                f"square={stats['square_color']} (suggests {stats['suggested_color']}) "
                f"examples={','.join(stats['example_squares'])} label={current}"
            )

        if options["dry_run"]:
            return

        missing = [c for c in info["clusters"] if c not in labels]
        if missing:
            raise CommandError(
                f"unlabeled clusters: {missing}. Re-run with --label '<id>=<code>' for each."
            )

        profile, _ = CalibrationProfile.objects.get_or_create(book=book)
        profile.data = build_profile_data(
            info,
            labels_by_cluster=labels,
            empty_max_ink=options["empty_threshold"],
            solidity_cut=options["solidity_cut"],
            source={"image_path": options["image_path"], "diagram_index": index},
            baseline=options["baseline"],
        )
        profile.save(update_fields=["data", "updated_at"])
        book.calibration_status = Book.Calibration.READY
        book.save(update_fields=["calibration_status", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(f"Saved calibration profile for {book.slug}.")
        )
