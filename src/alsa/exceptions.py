"""Errores tipados de alsa-cli: cada uno cuenta al usuario qué hacer."""

from __future__ import annotations


class AlsaError(RuntimeError):
    """Error genérico de alsa-cli."""


class AlsaConnectionError(AlsaError):
    """No se pudo conectar con alsa.es."""

    def __init__(self, message: str):
        super().__init__(
            f"No se pudo conectar con Alsa: {message}. "
            "Revisa tu conexión a internet y prueba de nuevo."
        )


class AlsaBlockedError(AlsaError):
    """Imperva (bot protection) bloqueó la petición."""

    def __init__(self):
        super().__init__(
            "Alsa bloqueó la petición (Imperva). "
            "Espera unos minutos y reintenta; si persiste, prueba desde otra red."
        )


class AlsaAuthError(AlsaError):
    """El token CSRF (p_auth) no funcionó."""

    def __init__(self, detail: str = ""):
        suffix = f" ({detail})" if detail else ""
        super().__init__(
            f"Alsa rechazó el token de sesión{suffix}. "
            "Se obtiene uno nuevo y reintenta automáticamente."
        )


class AlsaParseError(AlsaError):
    """La respuesta de Alsa no tiene el formato esperado."""

    def __init__(self, detail: str):
        super().__init__(
            f"La respuesta de Alsa no se pudo interpretar ({detail}). "
            "La web puede haber cambiado: reporta el problema en GitHub."
        )


class AlsaStationError(AlsaError):
    """Estación desconocida."""


class AlsaTimeoutError(AlsaError):
    """La petición tardó demasiado."""

    def __init__(self, timeout_seconds: int):
        super().__init__(
            f"Alsa no respondió en {timeout_seconds} segundos. "
            "El servicio puede estar lento o saturado — prueba de nuevo."
        )
