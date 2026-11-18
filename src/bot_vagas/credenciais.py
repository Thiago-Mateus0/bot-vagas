"""Login e senha da GUPY no chaveiro do sistema (gnome-keyring via Secret Service), nunca em arquivo.

Decisão do Thiago (2026-11-17): para o modo ser 100% automático, o bot DIGITA o login e a senha da Gupy quando a
Gupy pedir login (a sessão dura ~1 dia). Regras:
- guardados só no chaveiro do Linux (`bot-vagas senha-gupy`), criptografado e liberado pela sessão do usuário;
- nunca em arquivo do projeto, log, banco, screenshot de texto, mensagem de erro ou prompt do modelo;
- digitados só em páginas *.gupy.io (allowlist do piloto), no máximo uma vez por rodada;
- código/verificação/captcha => o bot para e avisa (não contorna nada).
"""

from __future__ import annotations

from typing import Any

SERVICO = "bot-vagas-gupy"
CHAVE_LOGIN = "login"
CHAVE_SENHA = "senha"


class CredencialIndisponivel(RuntimeError):
    """Chaveiro trancado/indisponível. A mensagem nunca contém login ou senha."""


def _kr(kr: Any = None):
    if kr is not None:
        return kr
    import keyring

    return keyring


def salvar(login: str, senha: str, kr: Any = None) -> None:
    login, senha = (login or "").strip(), senha or ""
    if not login or not senha:
        raise ValueError("login e senha não podem ser vazios")
    k = _kr(kr)
    try:
        k.set_password(SERVICO, CHAVE_LOGIN, login)
        k.set_password(SERVICO, CHAVE_SENHA, senha)
    except Exception as e:  # noqa: BLE001 (backend do chaveiro pode levantar vários tipos)
        raise CredencialIndisponivel(f"não consegui gravar no chaveiro do sistema ({type(e).__name__})") from None


def carregar(kr: Any = None) -> tuple[str, str] | None:
    """(login, senha) ou None se não houver. Levanta CredencialIndisponivel se o chaveiro não responder."""
    k = _kr(kr)
    try:
        login = k.get_password(SERVICO, CHAVE_LOGIN)
        senha = k.get_password(SERVICO, CHAVE_SENHA)
    except Exception as e:  # noqa: BLE001
        raise CredencialIndisponivel(f"chaveiro do sistema indisponível ou trancado ({type(e).__name__})") from None
    if not login or not senha:
        return None
    return login, senha


def apagar(kr: Any = None) -> bool:
    k = _kr(kr)
    apagou = False
    for chave in (CHAVE_LOGIN, CHAVE_SENHA):
        try:
            k.delete_password(SERVICO, chave)
            apagou = True
        except Exception:  # noqa: BLE001 (não existia)
            pass
    return apagou
