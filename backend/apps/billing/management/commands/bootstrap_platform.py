from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from billing.bootstrap import (
    BootstrapError,
    bootstrap_platform,
    inspect_platform_bootstrap,
    safe_report_json,
)


class Command(BaseCommand):
    help = "Idempotently initialize the canonical catalog, wallets, and first isolated platform owner."

    def add_arguments(self, parser):
        parser.add_argument("--non-interactive", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--create-wallets", action="store_true")
        parser.add_argument("--migrate-subscriptions-to-wallet", action="store_true")
        parser.add_argument("--rotate-owner-password", action="store_true")
        parser.add_argument("--password-stdin", action="store_true")
        parser.add_argument("--password-file")
        parser.add_argument("--adopt-existing-owner", action="store_true")
        parser.add_argument("--safe-json-report", action="store_true")
        parser.add_argument("--owner-email")
        parser.add_argument("--owner-first-name")
        parser.add_argument("--owner-last-name")

    def _password(self, options) -> str:
        password_file = options.get("password_file") or os.environ.get(
            "BOOTSTRAP_PLATFORM_OWNER_PASSWORD_FILE",
            os.environ.get("PLATFORM_OWNER_PASSWORD_FILE", ""),
        )
        if password_file:
            try:
                return Path(password_file).expanduser().read_text(encoding="utf-8").rstrip("\r\n")
            except OSError as exc:
                raise CommandError("Unable to read the configured bootstrap password file.") from exc
        if options.get("password_stdin"):
            return sys.stdin.readline().rstrip("\r\n")
        if value := os.environ.get(
            "BOOTSTRAP_PLATFORM_OWNER_PASSWORD",
            os.environ.get("PLATFORM_OWNER_PASSWORD", ""),
        ):
            return value
        if not options.get("non_interactive") and not options.get("check") and not options.get("dry_run"):
            return getpass.getpass("Initial platform owner password: ")
        return ""

    def handle(self, *args, **options):
        email = str(
            options.get("owner_email")
            or os.environ.get("BOOTSTRAP_PLATFORM_OWNER_EMAIL", "")
            or os.environ.get("PLATFORM_OWNER_EMAIL", "")
        ).strip().lower()
        configured_name = str(os.environ.get("BOOTSTRAP_PLATFORM_OWNER_NAME", "")).strip()
        configured_first, _, configured_last = configured_name.partition(" ")
        first_name = str(
            options.get("owner_first_name")
            or configured_first
            or os.environ.get("PLATFORM_OWNER_FIRST_NAME", "")
        ).strip()
        last_name = str(
            options.get("owner_last_name")
            or configured_last
            or os.environ.get("PLATFORM_OWNER_LAST_NAME", "")
        ).strip()
        if not email and not options["non_interactive"] and not options["check"] and not options["dry_run"]:
            email = input("Initial platform owner email: ").strip().lower()

        if options["check"] or options["dry_run"]:
            report = inspect_platform_bootstrap(owner_email=email)
            report["mode"] = "check" if options["check"] else "dry_run"
            output = safe_report_json(report)
            self.stdout.write(output)
            if options["check"] and not (
                report["catalog_ready"]
                and report["active_owner_present"]
                and (report["requested_owner_ready"] is not False)
            ):
                raise CommandError("Platform bootstrap check did not pass.")
            return

        password = self._password(options)
        try:
            report = bootstrap_platform(
                owner_email=email,
                owner_first_name=first_name,
                owner_last_name=last_name,
                owner_password=password,
                create_wallets=options["create_wallets"],
                migrate_subscriptions_to_wallet=options["migrate_subscriptions_to_wallet"],
                rotate_owner_password=options["rotate_owner_password"],
                adopt_existing_owner=options["adopt_existing_owner"],
            )
        except BootstrapError as exc:
            raise CommandError(str(exc)) from exc
        if options["safe_json_report"]:
            self.stdout.write(safe_report_json(report))
        else:
            self.stdout.write(self.style.SUCCESS("Platform bootstrap completed safely."))
