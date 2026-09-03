"""Paquete shared — utilidades transversales del entrypoint (item 6).

Contiene los helpers stdlib-only que el composition root (``main.py``)
necesita para el bootstrap: formateo user-safe de errores de configuración
(``errors.py``) y el arranque central del logging (``logging.py``). NO importa
ninguna capa del proyecto (ni settings/aiogram/pydantic): solo stdlib, para que
este paquete no acople utilidades a transporte ni a configuración.
"""
