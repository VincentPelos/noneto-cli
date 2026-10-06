<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/noneto-logo-white.png">
    <img alt="Noneto CLI" src="docs/assets/noneto-logo-black.png" width="440">
  </picture>
</h1>

Cliente no oficial de TIDAL para la terminal de Windows: busca canciones, escucha tus playlists, crea otras
y mira las letras con un visualizador estilo cava. Funciona con mouse y teclado.
Usa Python 3.12, Textual, [`tidalapi`](https://github.com/EbbLabs/python-tidal) y mpv.
Necesitas una cuenta de TIDAL con acceso a reproducción.

> [!IMPORTANT]
> Noneto CLI es un proyecto personal, gratuito y **no oficial**. No está afiliado, asociado ni respaldado
> por TIDAL. TIDAL es una marca de sus respectivos dueños.
>
> Usa la API no documentada de TIDAL a través de `tidalapi`: puede dejar de funcionar sin aviso y su uso es
> bajo tu propio riesgo respecto de tu cuenta. Noneto CLI solo reproduce en streaming: no descarga ni guarda
> música, y rechaza los streams cifrados (no evade DRM).

## Instalar

Requiere [uv](https://docs.astral.sh/uv/getting-started/installation/). uv crea el comando `noneto` y,
si hace falta, descarga Python 3.12.

```powershell
uv tool install git+https://github.com/VincentPelos/noneto-cli
noneto --install-mpv
noneto
```

`--install-mpv` descarga una versión fijada de mpv, verifica su SHA-256 y la deja en
`%LOCALAPPDATA%\Noneto\mpv`. Noneto CLI no usa otro mpv que tengas en el PATH.
La instalación no pide tu cuenta. Si `noneto` no se encuentra, ejecuta `uv tool update-shell`
y abre una terminal nueva.

## Actualizar y desinstalar

```powershell
uv tool upgrade noneto-cli
uv tool uninstall noneto-cli
Remove-Item -Recurse "$env:LOCALAPPDATA\Noneto"   # opcional: sesión, logs y mpv
```

## Uso

1. La primera vez se abre el navegador para autorizar Noneto CLI. Si no se abre, usa el enlace de la terminal.
2. Vuelve a la terminal cuando hayas autorizado. Se abre tu primera playlist.
3. Haz clic en una canción, o selecciónala y pulsa Enter. La cola sigue con las siguientes.

**Barra superior:** **Biblioteca ▾** abre tus playlists propias y favoritas, permite crear una
y actualizar la lista. Al lado está el buscador (canciones o artistas) y la insignia `◆` con la
calidad solicitada.

**Pestañas:** **Canciones** muestra la lista (título y, debajo, artista · álbum; `▷` marca la que
suena). **Cola · N** muestra las próximas canciones. **Letras** muestra carátula y letras,
sincronizadas cuando TIDAL las entrega.

**Visualizador:** en Letras, si sobran filas, aparecen barras estilo cava con el espectro real
de lo que suena. Se captura solo el audio de Noneto CLI (no el de otras aplicaciones) y solo mientras
el visualizador está visible. Las muestras no se guardan. Requiere Windows 10 2004 o posterior.

**Selección y reproductor:** **+ Cola** y **+ Playlist** agregan la canción seleccionada.
El reproductor muestra carátula, título, artista · álbum, `⇄` aleatorio, anterior,
reproducir/pausar, siguiente, `↻` repetir (no / cola / una), volumen clicable y una barra de
progreso donde un clic salta a ese punto. La cola no se guarda entre sesiones.

Usa una terminal compatible con mouse; el diseño funciona desde 80 × 24 y luce mejor desde 100 × 28.

| Tecla | Acción |
| --- | --- |
| Ctrl+L | Abrir Biblioteca |
| Ctrl+F | Enfocar búsqueda |
| Ctrl+N | Crear playlist |
| Enter en la tabla | Reproducir desde la selección |
| Espacio fuera de un campo de texto | Reproducir / pausar |
| Ctrl+Derecha | Siguiente canción |
| Ctrl+Q | Salir y detener el audio |

```powershell
noneto --playlist "https://tidal.com/browse/playlist/UUID-DE-TU-PLAYLIST"
noneto --quality HIGH              # menos datos; por defecto LOSSLESS
noneto --quality HI_RES_LOSSLESS   # depende de la canción y de lo que entregue TIDAL
noneto --logout                    # borra la sesión local
```

## Privacidad

- No se pide ni se guarda tu contraseña. Los tokens se guardan en `%LOCALAPPDATA%\Noneto\session.bin`,
  cifrados con DPAPI para tu usuario de Windows. `--logout` los borra de este equipo; no los revoca en TIDAL.
- Ni la música, ni las letras, ni las carátulas se guardan en disco.
- Los registros (`%LOCALAPPDATA%\Noneto\logs\noneto.log`) solo tienen fechas, IDs de canciones, etapas,
  estados HTTP y tiempos. No incluyen tokens, búsquedas, URLs firmadas ni audio, y quedan solo en tu equipo.

## Alcance

- Por ahora solo Windows.
- Sin descargas offline. Crea playlists y agrega canciones; todavía no renombra ni elimina.
- Obtiene el stream al llegar a cada canción: puede haber una pausa breve entre pistas.

## Pruebas

```powershell
uv venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -e .
.venv\Scripts\python.exe -m noneto --install-mpv
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

No requieren cuenta. Las de mpv usan audio local con salida nula; la del visualizador usa el dispositivo
de audio real a un volumen inaudible.

## Licencia

[MIT](LICENSE). Dependencias principales: `tidalapi` (LGPL-3.0), Textual y Rich (MIT), Requests (Apache-2.0),
NumPy (BSD), Pillow (MIT-CMU). mpv (GPL/LGPL) no se incluye: se descarga desde
[shinchiro/mpv-winbuild-cmake](https://github.com/shinchiro/mpv-winbuild-cmake) y se ejecuta como proceso aparte.
