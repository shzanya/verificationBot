import logging
from datetime import datetime
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape

console = Console(width=100)


class StylishLogger:
    """Стильный логгер для Discord-бота"""

    COLOR_MAP = {
        "info": "green",
        "error": "red",
        "warning": "yellow",
        "debug": "cyan"
    }

    ICON_MAP = {
        "info": "ℹ️",
        "error": "❌",
        "warning": "⚠️",
        "debug": "🔍"
    }

    def __init__(self, name: str = "VerificationBot"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)

        # Очистим старые обработчики
        self.logger.handlers.clear()

        # Настроим RichHandler
        handler = RichHandler(
            console=console,
            show_time=False,
            show_path=False,
            markup=True,
            rich_tracebacks=True
        )

        # Простой форматтер
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

        # Убавим громкость discord логов
        logging.getLogger("discord").setLevel(logging.WARNING)

    def _log(self, level: str, message: str):
        """Общий метод логирования с форматированием"""
        icon = self.ICON_MAP.get(level, "")
        color = self.COLOR_MAP.get(level, "white")
        timestamp = datetime.now().strftime("%H:%M:%S")

        formatted = f"[dim]{timestamp}[/dim] {icon} [{color}]{escape(message)}[/{color}]"
        getattr(self.logger, level)(formatted)

    def info(self, message: str):
        self._log("info", message)

    def error(self, message: str):
        self._log("error", message)

    def warning(self, message: str):
        self._log("warning", message)

    def debug(self, message: str):
        self._log("debug", message)

    def success(self, message: str):
        """Отдельный лог для успешных действий"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[dim]{timestamp}[/dim] ✅ [bold green]{escape(message)}[/bold green]"
        self.logger.info(formatted)
        
    def papka(self, message: str):
        """Отдельный лог для успешных действий"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[dim]{timestamp}[/dim] 📁 [bold green]{escape(message)}[/bold green]"
        self.logger.info(formatted)

    def separator(self, title: str = ""):
        """Разделитель в логах"""
        if title:
            console.print(f"\n[bold blue]{'=' * 20} {title} {'=' * 20}[/bold blue]")
        else:
            console.print(f"[dim]{'─' * 60}[/dim]")



# Создание экземпляра логгера
logger = StylishLogger()
