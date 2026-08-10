from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ai_runtime.providers import AIProviderError, OpenAIResponsesProvider


class Command(BaseCommand):
    help = "Run one explicitly authorized, synthetic Responses API smoke request."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-live", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm_live"]:
            raise CommandError("Pass --confirm-live to authorize the bounded external request.")
        if not settings.AI_RUNTIME_ENABLE_REAL_OPENAI or not settings.OPENAI_API_KEY:
            raise CommandError("Real OpenAI must be explicitly enabled and configured.")
        try:
            result = OpenAIResponsesProvider(
                model=settings.OPENAI_MODEL,
                timeout_seconds=min(settings.OPENAI_REQUEST_TIMEOUT_SECONDS, 20),
            ).generate(
                prompt="Synthetic health check. Reply with the single word OK.",
                tools=[],
                latest_message="Synthetic health check",
                max_output_tokens=64,
            )
        except AIProviderError as exc:
            raise CommandError(f"Smoke request failed safely: {exc.code}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Responses API smoke passed (input={result.input_tokens}, output={result.output_tokens}, latency_ms={result.latency_ms})."
            )
        )
