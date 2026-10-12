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
> Noneto aporta una interfaz de terminal y el código que integra tu cuenta con TIDAL mediante la
> biblioteca no oficial `tidalapi`. No opera el servicio de TIDAL ni desarrolla o controla sus API.
> Cambios externos pueden hacer que las funciones dejen de estar disponibles; no se garantiza
> compatibilidad futura. Noneto CLI solo reproduce en streaming: no descarga ni guarda música,
> y rechaza los streams cifrados (no evade DRM).

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

1. La primera vez se abre una ventana con el inicio de sesión de TIDAL. Inicia sesión y la ventana
   se cierra sola. Solo hace falta una vez; luego la sesión se recupera sola.
2. Si la ventana no puede abrirse (falta WebView2), se usa tu navegador: al terminar, TIDAL muestra una
   página "Oops"; copia su dirección (Ctrl+L, Ctrl+C) y Noneto CLI la detecta sola.
3. Se abre tu primera playlist.
4. Haz clic en una canción, o selecciónala y pulsa Enter. La cola sigue con las siguientes.

**Barra superior:** **Biblioteca ▾** abre tus playlists propias y favoritas, permite crear una
y actualizar la lista. Al lado está el buscador (canciones o artistas) y la insignia de calidad.
Mientras suena algo muestra lo que de verdad llega, medido en el audio decodificado (por ejemplo
`◆ FLAC 24/96`); sin reproducir solo muestra lo pedido (`Pedido: Hi-Res`). Si TIDAL entrega menos de
lo que esa pista ofrece, la insignia cambia a `◇ … ↓` en otro color. Al pasar el mouse se ven lo
pedido, lo recibido y las calidades en que existe la pista.

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

El aleatorio conserva la canción actual y mezcla solo las siguientes. Al desactivarlo, las pendientes
recuperan su orden original. **+ Cola** siempre agrega al final. Con repetición de toda la cola, cada
nuevo ciclo se vuelve a mezclar y evita empezar con la misma canción que terminó el anterior si hay
otra distinta. Las entradas duplicadas de una playlist se conservan.

Usa una terminal compatible con mouse; el diseño funciona desde 80 × 24 y luce mejor desde 100 × 28.

**Temas:** Ctrl+T cambia entre nueve paletas (noneto-dark, catppuccin-mocha, dracula, tokyo-night,
nord, rose-pine, flexoki, catppuccin-latte y rose-pine-dawn). El visualizador usa los colores de cada
una y Noneto CLI recuerda la última elegida.

**Idioma:** español o inglés según el idioma de Windows; `--lang es` o `--lang en` lo fija.

La búsqueda encuentra canciones y playlists. La pestaña **Playlists** muestra las listas encontradas;
clic o Enter abre sus canciones. El botón **Biblioteca ▾** permite elegir otra lista de tu biblioteca.

**Controles multimedia de Windows:** Noneto publica canción, artista, álbum, carátula y progreso,
y acepta pausa, reproducción, anterior/siguiente y cambios de posición desde el panel del sistema.
La presentación de la barra depende de Windows. El audio sigue reproduciéndose con mpv.
La integración crea un acceso **Noneto CLI** en el menú Inicio para identificar la aplicación;
si desinstalas Noneto con uv, puedes borrar también ese acceso del menú Inicio.

| Tecla | Acción |
| --- | --- |
| Ctrl+L | Abrir Biblioteca; al elegir una playlist, enfocar sus canciones para usar las flechas |
| Ctrl+F | Enfocar búsqueda |
| Ctrl+N | Crear playlist |
| Enter en la tabla | Reproducir desde la selección |
| Espacio fuera de un campo de texto | Reproducir / pausar |
| Ctrl+Derecha | Siguiente canción |
| Ctrl+T | Cambiar de tema |
| Ctrl+Q / Ctrl+C | Salir y detener el audio |

```powershell
noneto --playlist "https://tidal.com/browse/playlist/UUID-DE-TU-PLAYLIST"
noneto                             # pide la mejor calidad: Hi-Res FLAC hasta 24-bit / 192 kHz
noneto --quality LOSSLESS          # tope en 16-bit / 44.1 kHz
noneto --quality HIGH              # menos datos (AAC 320 kbps)
noneto --lang en                   # interfaz en inglés
noneto --logout                    # borra la sesión local
```

## Privacidad

- No se pide ni se guarda tu contraseña. Los tokens se guardan en `%LOCALAPPDATA%\Noneto\session.bin`,
  cifrados con DPAPI para tu usuario de Windows. `--logout` los borra de este equipo; no los revoca en TIDAL.
- Ni la música, ni las letras, ni las carátulas se guardan en disco.
- Los registros (`%LOCALAPPDATA%\Noneto\logs\noneto.log`) solo tienen fechas, IDs de canciones, etapas,
  estados HTTP y tiempos. No incluyen tokens, búsquedas, URLs firmadas ni audio, y quedan solo en tu equipo.

## Alcance

Noneto CLI ofrece el cliente: la interfaz, los controles y la integración local. El catálogo,
la disponibilidad del audio y el acceso a la cuenta dependen de TIDAL y de las interfaces que
utiliza `tidalapi`. Noneto no ofrece un servicio de música ni acceso independiente de tu cuenta.

Si una API cambia o una integración deja de ser viable, las funciones afectadas pueden
modificarse, deshabilitarse o retirarse. No se promete disponibilidad continua ni una corrección
para todos los cambios de terceros. El software se ofrece tal como está, sin las garantías
indicadas en [LICENSE](LICENSE); este aviso no sustituye las licencias de las dependencias
ni las condiciones aplicables al servicio de TIDAL.

- Por ahora solo Windows.
- Sin descargas offline. Crea playlists y agrega canciones; todavía no renombra ni elimina.
- Obtiene el stream al llegar a cada canción: puede haber una pausa breve entre pistas.

## Pruebas

```powershell
uv venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -e ".[windows-test]"
.venv\Scripts\python.exe -m noneto --install-mpv
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

No requieren cuenta. Las de mpv usan audio local con salida nula; la del visualizador usa el dispositivo
de audio real a un volumen inaudible.

## Licencia

La licencia [MIT](LICENSE) cubre el código y la documentación propios de **Noneto CLI**.
Las dependencias de terceros conservan sus propias licencias; MIT no las sustituye.

En particular, **`tidalapi`**, la biblioteca Python que usamos para acceder a TIDAL, se utiliza
como dependencia separada, sin modificar, bajo
[LGPL-3.0-or-later](https://github.com/EbbLabs/python-tidal/blob/v0.8.11/LICENSE).
También es software libre, con condiciones distintas de MIT. La licencia de Noneto no concede
derechos sobre el servicio o la API de TIDAL, su música, letras, carátulas o marcas.

Otras dependencias principales: Textual y Rich (MIT), Requests (Apache-2.0),
NumPy (BSD), Pillow (MIT-CMU). mpv (GPL/LGPL) no se incluye: se descarga desde
[shinchiro/mpv-winbuild-cmake](https://github.com/shinchiro/mpv-winbuild-cmake) y se ejecuta como proceso aparte.
