"""Límite único de tamaño de media (R10).

Telegram Bot API no acepta envíos de video > 50 MB; el downloader aplica la
misma cota como tope de descarga para no traer media que jamás se podría
reenviar. Una sola constante evita que downloader y sender diverjan: el sender
la usa como red de seguridad user-safe para downloaders que no apliquen la cota
(fakes/otros adapters del ``MediaDownloader`` Protocol).
"""

MAX_MEDIA_BYTES = 50 * 1024 * 1024
