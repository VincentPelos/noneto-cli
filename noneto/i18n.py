"""User-facing text in Spanish (the source language) or English.

Spanish strings are the keys: `_("Buscar")` returns "Search" when English is active.
Translate when the text is shown or raised; app.main sets the language before importing the
TUI, whose class-level bindings and labels are built at import time. Logs stay untranslated.
"""
import ctypes
import os

LANGUAGES = ("es", "en")
_language = "es"

EN = {
    # Terminal, before and after the TUI
    "Diagnostico: {path}": "Diagnostics: {path}",
    "No se pudo abrir el archivo de diagnostico.": "Could not open the diagnostics file.",
    "Sesion local eliminada.": "Local session deleted.",
    "Instalando mpv (si falta)...": "Installing mpv (if missing)...",
    "mpv listo: {path}": "mpv ready: {path}",
    "Hasta luego.": "Goodbye.",
    "No se pudo completar la operacion ({label}).": "The operation could not be completed ({label}).",
    "Comprueba tu conexion y tu cuenta. Para iniciar sesion otra vez: noneto --logout":
        "Check your connection and your account. To sign in again: noneto --logout",
    "Sesion recuperada.": "Session restored.",
    "No se pudo recuperar la sesion. Inicia sesion nuevamente.": "Could not restore the session. Please sign in again.",
    "Tu sesion es de un tipo antiguo sin acceso a Hi-Res. Inicia sesion otra vez (solo esta vez).":
        "Your session is an older type without Hi-Res access. Please sign in again (only this once).",
    "Inicia sesion en la ventana de TIDAL que se abrio. Se cierra sola al terminar.":
        "Sign in in the TIDAL window that just opened. It closes by itself when you are done.",
    "No se pudo abrir la ventana. Inicia sesion en TIDAL desde tu navegador:":
        "Could not open the window. Sign in to TIDAL in your browser:",
    "Al terminar, TIDAL muestra una pagina de error (\"Oops\"). Es normal.":
        "When you finish, TIDAL shows an error page (\"Oops\"). That is expected.",
    "Copia la direccion de esa pagina (Ctrl+L, Ctrl+C): Noneto la detecta sola. (Ctrl+C aqui cancela)":
        "Copy that page's address (Ctrl+L, Ctrl+C): Noneto picks it up by itself. (Ctrl+C here cancels)",
    "Sesion iniciada.": "Signed in.",
    "Iniciar sesion en TIDAL - Noneto CLI": "Sign in to TIDAL - Noneto CLI",
    # Command line
    "Noneto CLI: cliente no oficial de TIDAL para la terminal.": "Noneto CLI: unofficial TIDAL client for the terminal.",
    "Enlace o UUID de la playlist que se abre al iniciar": "Link or UUID of the playlist to open at startup",
    "Calidad maxima pedida a TIDAL; por defecto, la mejor disponible":
        "Highest quality requested from TIDAL; by default, the best available",
    "Elimina la sesion local y termina": "Delete the local session and exit",
    "Descarga mpv (version fijada, SHA-256 verificado) y termina": "Download mpv (pinned version, SHA-256 verified) and exit",
    "Idioma de la interfaz (por defecto, el de Windows)": "Interface language (default: the Windows language)",
    "Por ahora Noneto CLI funciona solo en Windows.": "For now Noneto CLI only runs on Windows.",
    # Errors shown verbatim (AppError)
    "Inicio de sesion cancelado.": "Sign-in cancelled.",
    "TIDAL rechazo el codigo; puede haber caducado. Ejecuta noneto otra vez.":
        "TIDAL rejected the code; it may have expired. Run noneto again.",
    "No se recibio la direccion de inicio de sesion a tiempo. Ejecuta noneto otra vez.":
        "The sign-in address did not arrive in time. Run noneto again.",
    "Destino de red no valido.": "Invalid network destination.",
    "Solo se permiten destinos HTTPS validos.": "Only valid HTTPS destinations are allowed.",
    "El destino no pertenece al servicio autorizado.": "The destination does not belong to the authorized service.",
    "No se permiten direcciones de red privadas.": "Private network addresses are not allowed.",
    "Esa no es la pagina a la que TIDAL redirige al iniciar sesion.":
        "That is not the page TIDAL redirects to after signing in.",
    "TIDAL no autorizo el inicio de sesion.": "TIDAL did not authorize the sign-in.",
    "La direccion pegada no contiene un codigo de autorizacion.": "The address does not contain an authorization code.",
    "Manifiesto DASH no valido.": "Invalid DASH manifest.",
    "Manifiesto DASH demasiado profundo.": "DASH manifest is nested too deeply.",
    "Espacio de nombres DASH no soportado.": "Unsupported DASH namespace.",
    "El manifiesto usa una funcion DASH no soportada.": "The manifest uses an unsupported DASH feature.",
    "No se permiten referencias XML externas.": "External XML references are not allowed.",
    "Multiples BaseURL no soportadas.": "Multiple BaseURL elements are not supported.",
    "Referencia DASH no valida.": "Invalid DASH reference.",
    "El manifiesto no contiene destinos de audio.": "The manifest has no audio destinations.",
    "Manifiesto de audio demasiado grande.": "Audio manifest too large.",
    "Formato de manifiesto no soportado.": "Unsupported manifest format.",
    "Este stream requiere un metodo de reproduccion no soportado.": "This stream needs an unsupported playback method.",
    "Tidal no devolvio un manifiesto de audio valido.": "TIDAL did not return a valid audio manifest.",
    "Demasiadas acciones pendientes. Espera un momento.": "Too many pending actions. Wait a moment.",
    "Se perdio la comunicacion con mpv. Reintenta reproducir.": "Lost communication with mpv. Try playing again.",
    "No se pudo cargar el audio. Reintenta o elige otra cancion.": "Could not load the audio. Try again or pick another song.",
    "El audio no cargo en 45 segundos. Reintenta.": "The audio did not load within 45 seconds. Try again.",
    "Falta mpv. Instalalo con: noneto --install-mpv": "mpv is missing. Install it with: noneto --install-mpv",
    "mpv no pudo iniciarse.": "mpv could not start.",
    "mpv no respondio en 10 segundos.": "mpv did not respond within 10 seconds.",
    "No se pudo verificar el canal del reproductor.": "Could not verify the player channel.",
    "No se pudo configurar el canal del reproductor.": "Could not configure the player channel.",
    "Falta tar.exe de Windows para extraer mpv.": "Windows tar.exe is missing; it is needed to extract mpv.",
    "No se pudo descargar mpv (HTTP {status}).": "Could not download mpv (HTTP {status}).",
    "La descarga de mpv es demasiado grande.": "The mpv download is too large.",
    "El hash SHA-256 de mpv no coincide; no se instalo.": "The mpv SHA-256 hash does not match; it was not installed.",
    "El archivo descargado no contiene mpv.exe.": "The downloaded file does not contain mpv.exe.",
    "Tidal devolvio una redireccion no permitida.": "TIDAL returned a disallowed redirect.",
    "Comando de reproduccion demasiado grande.": "Playback command too large.",
    "Se perdio la comunicacion con el reproductor.": "Lost communication with the player.",
    "mpv no respondio al comando a tiempo.": "mpv did not answer the command in time.",
    "mpv rechazo un comando o se desconecto.": "mpv rejected a command or disconnected.",
    "No se pudo preparar el cierre automatico del reproductor.": "Could not prepare the player's automatic shutdown.",
    "No se pudo configurar el cierre automatico del reproductor.": "Could not configure the player's automatic shutdown.",
    "No se pudo vincular el reproductor al cierre de Noneto CLI.": "Could not tie the player to Noneto CLI's shutdown.",
    "Caratula demasiado grande.": "Cover art too large.",
    "Caratula no disponible.": "Cover art not available.",
    "Caratula no valida.": "Invalid cover art.",
    "El nombre debe tener entre 1 y 100 caracteres.": "The name must be between 1 and 100 characters.",
    "Escribe una busqueda de entre 1 y 200 caracteres.": "Type a search between 1 and 200 characters.",
    "La paginacion excedio el limite de seguridad.": "Pagination exceeded the safety limit.",
    "Solo puedes agregar canciones a tus propias playlists.": "You can only add songs to your own playlists.",
    "Tidal devolvio una pagina mayor de lo solicitado.": "TIDAL returned a larger page than requested.",
    # TUI
    "Cancelar": "Cancel",
    "Tu biblioteca": "Your library",
    "Sin playlists. Crea una o actualiza.": "No playlists. Create one or refresh.",
    "+ Nueva playlist": "+ New playlist",
    "Actualizar": "Refresh",
    "Cerrar": "Close",
    "Nueva playlist": "New playlist",
    "Nombre de tu playlist": "Your playlist's name",
    "Crear": "Create",
    "Agregar a una playlist propia": "Add to one of your playlists",
    "Salir": "Quit",
    "Buscar": "Search",
    "Biblioteca": "Library",
    "Pausa": "Pause",
    "Siguiente": "Next",
    "Tema": "Theme",
    "Tema: {name} · Ctrl+T para cambiar": "Theme: {name} · Ctrl+T to change",
    "Usuario": "User",
    "Biblioteca ▾": "Library ▾",
    "Playlists de {name} · Ctrl+L": "{name}'s playlists · Ctrl+L",
    "Buscar canciones o artistas…": "Search songs or artists…",
    "Canciones": "Songs",
    "Más resultados": "More results",
    "Cola": "Queue",
    "Cola · {count}": "Queue · {count}",
    "Sin canciones en cola.": "No songs in the queue.",
    "Letras": "Lyrics",
    "Reproduce una canción para ver sus letras.": "Play a song to see its lyrics.",
    "Cargando letras…": "Loading lyrics…",
    "No hay letras disponibles para esta canción.": "No lyrics available for this song.",
    "Selecciona una canción": "Select a song",
    "Seleccionada  ": "Selected  ",
    "+ Cola": "+ Queue",
    "Sin canción": "No song",
    "Aleatorio": "Shuffle",
    "Anterior": "Previous",
    "Reproducir o pausar": "Play or pause",
    "Repetir: apagado / cola / canción": "Repeat: off / queue / song",
    "Cargando biblioteca…": "Loading library…",
    "La interfaz se cerro por un error. Consulta los logs de Noneto CLI.":
        "The interface closed because of an error. See the Noneto CLI logs.",
    "Título": "Title",
    "Duración": "Duration",
    "No se pudo completar: {label}. Puedes reintentar.": "Could not complete: {label}. You can try again.",
    "{count} playlists · Clic en Biblioteca para abrir otra": "{count} playlists · Click Library to open another",
    "Cargando…": "Loading…",
    "No se pudo cargar. Ábrela otra vez para reintentar.": "Could not load. Open it again to retry.",
    "Búsqueda": "Search",
    "{source} · {count} canciones": "{source} · {count} songs",
    "Sin canciones. Busca música y agrégala a una playlist.": "No songs. Search for music and add it to a playlist.",
    "Resultados: {query}": "Results: {query}",
    "Búsqueda fallida. Pulsa Buscar para reintentar.": "Search failed. Press Search to retry.",
    "Playlist creada en tu cuenta de Tidal.": "Playlist created in your TIDAL account.",
    "No se confirmó la creación. Actualiza la biblioteca antes de reintentar.":
        "The creation was not confirmed. Refresh the library before retrying.",
    "Primero crea una playlist propia desde Biblioteca.": "First create a playlist of your own from Library.",
    "Canción agregada.": "Song added.",
    "Sin cambios: la canción ya existe o no está disponible.": "No changes: the song is already there or unavailable.",
    "Canción agregada al final de la cola.": "Song added to the end of the queue.",
    "+ {count} canciones": "+ {count} songs",
    "Rep: {mode}": "Repeat: {mode}",
    "no": "off",
    "cola": "queue",
    "una": "one",
    # Playback status
    "Sin reproduccion": "Not playing",
    "Cargando": "Loading",
    "Reproduciendo": "Playing",
    "Pausado": "Paused",
    "Detenido": "Stopped",
    "Error": "Error",
    # Quality badge
    "FLAC hasta 24-bit / 192 kHz": "FLAC up to 24-bit / 192 kHz",
    "Lossless 16-bit / 44.1 kHz": "Lossless 16-bit / 44.1 kHz",
    "Pedido: {short}": "Requested: {short}",
    "Pedido: {short} ({detail}).": "Requested: {short} ({detail}).",
    "Al reproducir se muestra la calidad que llega.": "While playing, the quality that arrives is shown.",
    "Esperando el audio.": "Waiting for audio.",
    "Recibido: {received}.": "Received: {received}.",
    "Esta pista existe en: {tier}.": "This track is available in: {tier}.",
    "TIDAL entregó menos de lo que ofrece esta pista.": "TIDAL delivered less than this track offers.",
}


def system_language():
    """Spanish when Windows' display language is Spanish, English otherwise."""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return "es" if os.environ.get("LANG", "").lower().startswith("es") else "en"
    return "es" if lang_id & 0x3FF == 0x0A else "en"  # LANG_SPANISH primary language id


def set_language(language):
    global _language
    if language not in LANGUAGES:
        raise ValueError(language)
    _language = language


def language():
    return _language


def _(text, **values):
    if _language == "en":
        text = EN.get(text, text)
    return text.format(**values) if values else text
