from __future__ import annotations

from proxy_manager.proxy_health import flag_emoji

# ISO 3166-1 alpha-2 — países comuns para saída de proxy / Tor
COUNTRY_CODES: tuple[tuple[str, str], ...] = (
    ("", "Qualquer país"),
    ("US", "Estados Unidos"),
    ("NL", "Holanda"),
    ("DE", "Alemanha"),
    ("FR", "França"),
    ("GB", "Reino Unido"),
    ("CA", "Canadá"),
    ("JP", "Japão"),
    ("SG", "Singapura"),
    ("AU", "Austrália"),
    ("CH", "Suíça"),
    ("SE", "Suécia"),
    ("IT", "Itália"),
    ("ES", "Espanha"),
    ("PL", "Polônia"),
    ("BR", "Brasil"),
    ("MX", "México"),
    ("IN", "Índia"),
    ("KR", "Coreia do Sul"),
    ("FI", "Finlândia"),
    ("NO", "Noruega"),
    ("AT", "Áustria"),
    ("BE", "Bélgica"),
    ("IE", "Irlanda"),
    ("PT", "Portugal"),
    ("AR", "Argentina"),
    ("CL", "Chile"),
    ("CO", "Colômbia"),
    ("ZA", "África do Sul"),
    ("TR", "Turquia"),
    ("UA", "Ucrânia"),
    ("RO", "Romênia"),
    ("CZ", "República Tcheca"),
    ("HK", "Hong Kong"),
    ("TW", "Taiwan"),
)


def country_label(code: str) -> str:
    code = code.strip().upper()
    for cc, name in COUNTRY_CODES:
        if cc == code:
            if not cc:
                return name
            return f"{flag_emoji(cc)} {name}"
    if code:
        return f"{flag_emoji(code)} {code}"
    return "Qualquer país"


def country_code_from_label(label: str) -> str:
    for code, name in COUNTRY_CODES:
        if label == country_label(code) or label == name:
            return code
    return ""


def country_option_labels() -> list[str]:
    return [country_label(code) for code, _ in COUNTRY_CODES]
